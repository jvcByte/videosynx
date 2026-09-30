"""The worker loop: claim a job, render it, reach a terminal state.

The one rule that matters is that a lost lease means *stop*. If the heartbeat
fails, another worker may already be rendering the same job, and finishing
anyway spends the money twice.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

from .db import HEARTBEAT_SECONDS, Job, Queue, new_owner

# How often a running render asks whether it should still be running. Much
# shorter than HEARTBEAT_SECONDS, because that thread is keeping the lease
# alive and this is answering the user who just pressed Cancel -- a twenty
# second wait for that reads as a broken button. One query a second is nothing
# next to writing thirty frames in the same second.
STOP_CHECK_SECONDS = 1.0

log = logging.getLogger("videosynx.worker")

# Called by the renderer at stage boundaries: (stage name, progress 0..1).
Beat = Callable[[str, float], None]


class LeaseLost(RuntimeError):
    """The lease is no longer ours. Stop immediately; do not finish the job."""


class Canceled(RuntimeError):
    """Whoever submitted the job changed their mind.

    Not a failure, and deliberately distinguishable from a lost lease: one is
    nobody's fault and the other is the user's own decision, and a job that
    was canceled must not be recorded as having gone wrong.
    """


class Refused(RuntimeError):
    """The job asked for something that cannot be honoured, so it was not tried.

    Distinct from a failure: nothing went wrong, the work was declined before
    it could cost anything.
    """


# How long to wait before trying again, by the attempt that just failed. The
# gaps grow so a brief outage is retried while it is still fresh, and a spent
# daily allowance is outlasted -- the free model quota is per day, so anything
# quicker than hours cannot cover it. Six gaps, seven attempts, about 31 hours.
RETRY_BACKOFF_S = (60, 300, 900, 3600, 6 * 3600, 24 * 3600)

# Failures that will not come out differently. `Refused` says it itself:
# "nothing went wrong, the work was declined before it could cost anything".
# The same input declines again, so repeating it only delays the answer.
PERMANENT = frozenset({"Refused"})


def retry_delay(code: str, attempt: int) -> float | None:
    """Seconds before *code* is worth trying again, or None not to try.

    *attempt* is the attempt that just failed. The policy lives here rather
    than in SQL so it can be read and tested as one thing.
    """
    if code in PERMANENT:
        return None
    index = attempt - 1
    if index < 0 or index >= len(RETRY_BACKOFF_S):
        # Out of schedule. max_attempts ends it too; this is the guard that
        # keeps the two from disagreeing.
        return None
    return float(RETRY_BACKOFF_S[index])


def retry_description(seconds: float | None) -> str:
    """The wait, said the way the log line wants it."""
    if seconds is None:
        return "not retrying"
    if seconds < 3600:
        return f"retrying in {seconds / 60:.0f}m"
    return f"retrying in {seconds / 3600:.0f}h"


def failure_code(exc: Exception) -> str:
    """A short tag for the job row, saying whose problem the failure is.

    An exception class name says what broke. ``provider_quota`` says the
    allowance is spent and will come back tomorrow, which is a different thing
    to tell someone than ``provider_error`` -- and it is what lets the page
    offer the free way to keep working instead of just reporting a fault.
    """
    from videosynx.providers import ProviderError

    if isinstance(exc, ProviderError):
        return f"provider_{exc.reason}"
    return type(exc).__name__


class Renderer(Protocol):
    def __call__(self, job: Job, beat: Beat,
                 should_stop: Callable[[], None] | None = None) -> tuple[str, float]:
        """Render *job*; return (artifact_key, duration_seconds).

        *beat* marks a stage boundary. *should_stop* raises if the run should
        be abandoned, and is meant to be handed to whatever long loop the
        renderer owns -- a renderer that never calls it can still be stopped,
        but only at the next stage boundary, which for this pipeline is
        minutes away.
        """


class Worker:
    def __init__(self, queue: Queue, renderer: Renderer, *, owner: str | None = None,
                 poll_seconds: float = 2.0):
        self.queue = queue
        self.renderer = renderer
        self.owner = owner or new_owner()
        self.poll_seconds = poll_seconds
        self._lease_lost = threading.Event()
        # Two different stops, deliberately separate: one ends the worker loop,
        # the other ends the heartbeat for the job in flight. They used to be a
        # single event, and because run_once set it to stop its own heartbeat
        # thread, run_forever's loop condition saw it and the worker exited
        # after exactly one job.
        self._stop = threading.Event()
        self._beat_stop = threading.Event()
        self._beat_thread: threading.Thread | None = None
        self._current: str | None = None
        # Its own event rather than reusing _lease_lost: a canceled job is not
        # an abandoned one, and the two get different log lines and different
        # outcomes on the row.
        self._canceled = threading.Event()
        self._checked_at = 0.0

    # ------------------------------------------------------------- heartbeats

    def stopping(self) -> None:
        """Raise if this run should stop. Safe to call per frame.

        Two event reads, and at most one query a second. The heartbeat thread
        alone would not do: it wakes every HEARTBEAT_SECONDS, so a cancel
        pressed a moment after it last ran would go unnoticed for twenty
        seconds. The render loop is the only thing that knows a frame is
        actually being worked on, so it asks for itself -- rate-limited so a
        caller looping thirty times a second costs thirty times less.
        """
        if self._canceled.is_set():
            raise Canceled(f"{self._current} was canceled")
        if self._lease_lost.is_set():
            raise LeaseLost("lease lost")
        if self._current is None:
            return

        now = time.monotonic()
        if now - self._checked_at < STOP_CHECK_SECONDS:
            return
        self._checked_at = now

        status = self.queue.heartbeat(self._current, self.owner)
        if status is None:
            self._lease_lost.set()
            raise LeaseLost(f"lease on {self._current} is no longer ours")
        if status == "canceled":
            self._canceled.set()
            raise Canceled(f"{self._current} was canceled")

    def beat(self, stage: str | None = None, progress: float | None = None) -> None:
        """Extend the lease, or raise if the job should stop."""
        self.stopping()
        if self._current is None:
            raise LeaseLost("lease lost")
        status = self.queue.heartbeat(self._current, self.owner,
                                      stage=stage, progress=progress)
        if status is None:
            self._lease_lost.set()
            raise LeaseLost(f"lease on {self._current} is no longer ours")
        if status == "canceled":
            self._canceled.set()
            raise Canceled(f"{self._current} was canceled")

    def _beat_loop(self) -> None:
        """Keep the lease alive between stage boundaries.

        A render is minutes of work with few natural checkpoints, so relying
        on the renderer to beat often enough would make the lease a function
        of how the renderer happens to be written.
        """
        while not self._beat_stop.wait(HEARTBEAT_SECONDS):
            if self._current is None:
                continue
            try:
                status = self.queue.heartbeat(self._current, self.owner)
                if status is None:
                    log.warning("lease on %s lost; signalling the run to stop",
                                self._current)
                    self._lease_lost.set()
                    return
                if status == "canceled":
                    log.info("%s was canceled; signalling the run to stop",
                             self._current)
                    self._canceled.set()
                    return
            except Exception:  # a blip must not kill the loop
                log.exception("heartbeat failed; will retry")

    # ------------------------------------------------------------------ cycle

    def run_once(self) -> bool:
        """Claim and process one job. Returns False when the queue is empty."""
        job = self.queue.claim(self.owner)
        if job is None:
            return False

        log.info("claimed %s (%s) attempt %d/%d",
                 job.id, job.topic, job.attempt, job.max_attempts)
        self._current = job.id
        self._lease_lost.clear()
        self._canceled.clear()
        # Clear before starting: a beat thread that sees this already set
        # returns immediately and the job renders with no heartbeat at all.
        self._beat_stop.clear()
        self._beat_thread = threading.Thread(target=self._beat_loop, daemon=True)
        self._beat_thread.start()

        try:
            self.beat("starting", 0.0)
            artifact, duration = self.renderer(job, lambda s, p: self.beat(s, p),
                                               self.stopping)
            if self._lease_lost.is_set() or self._canceled.is_set():
                # Rendered, but the job is not ours to finish any more: either
                # someone else has it, or nobody wants it. Finishing would
                # double-charge in the first case and resurrect a canceled job
                # in the second.
                log.warning("dropping %s: %s during the render", job.id,
                            "canceled" if self._canceled.is_set() else "lease lost")
                return True
            if self.queue.finish(job.id, self.owner, artifact_key=artifact,
                                 duration_s=duration):
                log.info("finished %s -> %s", job.id, artifact)
            else:
                log.warning("could not finish %s: lease is gone", job.id)
        except Canceled:
            # Not a failure, and not recorded as one: the row already says
            # canceled, and fail() would overwrite that with "failed".
            log.info("stopped %s: canceled", job.id)
        except LeaseLost:
            log.warning("abandoning %s: lease lost", job.id)
        except Exception as exc:  # noqa: BLE001 - recorded against the job
            code = failure_code(exc)
            delay = retry_delay(code, job.attempt)
            log.exception("job %s failed (%s), %s",
                          job.id, code, retry_description(delay))
            self.queue.fail(job.id, self.owner, code=code, message=str(exc),
                            delay_seconds=delay)
        finally:
            self._current = None
            self._beat_stop.set()
            if self._beat_thread is not None:
                self._beat_thread.join(timeout=5)
                self._beat_thread = None
        return True

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(self.poll_seconds)
            except KeyboardInterrupt:
                log.info("interrupted; stopping")
                return

    def stop(self) -> None:
        self._stop.set()


# ------------------------------------------------------------------ renderers

class EngineRenderer:
    """Render a job with the videosynx engine, then store the result.

    The job's ``artifact_key`` is a storage key, not a path, so the API can
    hand out a URL without knowing where the bytes are. With no storage
    configured it falls back to the local path, which is what the tests and a
    bare checkout use.
    """

    def __init__(self, output_dir: Path, *, tts: str = "auto", execute: bool = True,
                 storage=None):
        self.output_dir = Path(output_dir)
        self.tts = tts
        self.execute = execute
        self.storage = storage

    def _speak(self, job: Job, beat: Beat, should_stop) -> tuple[str, float]:
        """Read the text aloud and hand back an audio file.

        The same queue, the same lease, the same signed artifact as a video --
        what is different is only how long it takes and what comes out.

        Synthesis runs somewhere between half and one times realtime depending
        on what else is on the machine, so a five-minute clip is minutes of
        CPU. The stop check and the progress report both go into the sentence
        loop inside `synthesise`: a cancel that only lands at the end is not a
        cancel, and a bar that only moves at the end is not a bar.
        """
        from videosynx.providers import KokoroSpeaker
        from videosynx.speech import synthesise

        text = str(job.options.get("text") or "").strip()
        if not text:
            raise Refused("there is no text to read")

        out = self.output_dir / f"{job.id}.mp3"
        beat("speaking", 0.1)
        speaker = self._speaker(job.options.get("tts") or self.tts,
                                job.options.get("voice"))
        try:
            # Both of these go into the synthesis loop, sentence by sentence.
            # Checking only before it starts means a cancel waits out the whole
            # clip -- minutes of CPU for something nobody wants -- and
            # reporting only before it starts means the bar sits at ten per
            # cent for the same minutes, which is a progress bar that has
            # stopped.
            seconds = synthesise(
                text, speaker, out,
                should_stop=should_stop,
                on_progress=lambda done: beat("speaking", 0.1 + 0.8 * done),
            )
        except BaseException:
            # A half-written mp3 is not something anything should play.
            out.unlink(missing_ok=True)
            raise

        if self.storage is None:
            beat("done", 1.0)
            return str(out), seconds

        from .storage import video_key

        beat("uploading", 0.95)
        # Its own suffix, from the same key layout, so a clip and a video for
        # one job id can never be each other by accident.
        key = video_key(job.tier, job.id, ".mp3")
        self.storage.put(key, out)
        out.unlink(missing_ok=True)  # the store has it now
        beat("done", 1.0)
        return key, seconds

    def _speaker(self, tts: str | None = None, voice: str | None = None):
        import os

        from videosynx.providers import (
            ESpeakSpeaker,
            ElevenLabsSpeaker,
            KokoroSpeaker,
            ProviderError,
        )

        choice = tts or self.tts
        log.info("voice: %s (engine %s)", voice or "default", choice)
        if choice == "elevenlabs":
            key = os.environ.get("ELEVENLABS_API_KEY", "")
            eleven_voice = os.environ.get("ELEVENLABS_VOICE_ID", "")
            if key and eleven_voice:
                return ElevenLabsSpeaker(key, eleven_voice)
            # Paid tier with no key configured: better a good local voice than
            # a failed job.
            choice = "kokoro"
        if choice in ("kokoro", "auto"):
            try:
                # The user picks a voice inside the engine the tier chose;
                # KokoroSpeaker falls back to the default for an unknown name.
                return KokoroSpeaker(voice=voice) if voice else KokoroSpeaker()
            except ProviderError:
                return ESpeakSpeaker()
        return ESpeakSpeaker()

    def __call__(self, job: Job, beat: Beat,
                 should_stop: Callable[[], None] | None = None) -> tuple[str, float]:
        import os

        from videosynx.lesson import (
            build_lesson,
            lesson_from_text,
            load_lesson,
        )
        from videosynx.providers import default_writer
        from videosynx.render import render_plan
        from videosynx.script import build_tutorial
        from videosynx.timeline import without_captions

        self.output_dir.mkdir(parents=True, exist_ok=True)
        out = self.output_dir / f"{job.id}.mp4"

        # Everything below was decided at submit time: the tier-owned keys by
        # submit_within_quota, the rest by the user's saved settings. Reading
        # them here is what makes either layer mean anything.
        options = job.options

        # Speech is the same job as a video in every way that matters to the
        # queue -- it leases, it heartbeats, it can be stopped, its artifact is
        # signed the same way -- so it is the same row with less asked of it.
        # Everything below this branch is about frames.
        if options.get("kind") == "speech":
            return self._speak(job, beat, should_stop)
        mode = options.get("mode") or "both"
        max_snippets = options.get("max_snippets")
        style = options.get("style") or "sketch"
        captions = options.get("captions", True)
        watermark = "videosynx" if options.get("watermark") else None
        width, height = (options.get("resolution") or [1920, 1080])[:2]
        log.info("mode: %s, style: %s, %sx%s, watermark: %s",
                 mode, style, width, height, bool(watermark))

        # Three ways in, in order of how much the caller has already decided:
        # their own script, a lesson file on this machine, or just a topic.
        script = options.get("script")
        lesson_path = options.get("lesson")
        generation = options.get("generation") or "auto"

        # Decided before anything is constructed. The API refuses this before
        # it is ever queued, but the queue can be written to directly and local
        # mode's whole promise is that nothing is spent -- so it is not left to
        # one caller to keep. Prose counts as needing a model, because it has
        # to be structured first; lesson JSON does not.
        if generation == "local" and not (script or lesson_path):
            raise Refused(
                "local mode works from content you supply and never calls a "
                "model, so it cannot write a tutorial from a topic. Paste a "
                "script, or supply lesson JSON."
            )

        beat("building", 0.1)
        speaker = self._speaker(options.get("tts") or self.tts, options.get("voice"))
        # Only built when it could be used. Constructing one for a local job
        # would spend nothing, but it would still fail on a missing key for a
        # job that was never going to need one.
        #
        # Every provider that has a key, not just Gemini. A free allowance
        # running out is the ordinary way this runs -- twenty requests per
        # model per day -- so one provider is not enough to keep a queue
        # moving, and the fallback costs nothing to have.
        writer = None if generation == "local" else default_writer()

        if script:
            lesson = lesson_from_text(script, writer=writer,
                                      local=generation == "local")
            tutorial = build_lesson(lesson, speaker=speaker, execute=self.execute)
        elif lesson_path:
            tutorial = build_lesson(load_lesson(lesson_path), speaker=speaker,
                                    execute=self.execute)
        else:
            tutorial = build_tutorial(job.topic, writer=writer, speaker=speaker,
                                      execute=self.execute, mode=mode,
                                      max_snippets=max_snippets)

        plan = without_captions(tutorial.plan) if not captions else tutorial.plan
        beat("rendering", 0.7)
        try:
            render_plan(plan, out, width=width, height=height, style=style,
                        watermark=watermark, should_stop=should_stop)
        except BaseException:
            # A stopped encode leaves a half-written file. The sink kills
            # ffmpeg on the way out, so what is left on disk is a truncated
            # mp4 that nothing should ever pick up.
            out.unlink(missing_ok=True)
            raise

        # The plan the renderer actually produced, saved beside the video.
        #
        # Without it a video that came out wrong cannot be diagnosed at all:
        # the code, the narration and every diagram are generated afresh each
        # run, and nothing else keeps a record of what was generated. This is
        # the same JSON the CLI writes with --save-script.
        from dataclasses import replace

        script_path = out.with_name(f"{out.stem}.script.json")
        replace(tutorial, plan=plan).export(script_path)

        if self.storage is None:
            beat("done", 1.0)
            return str(out), plan.duration_s

        from .storage import video_key

        beat("uploading", 0.95)
        self.storage.put(video_key(job.tier, job.id, ".script.json"), script_path)
        script_path.unlink(missing_ok=True)

        key = video_key(job.tier, job.id)
        self.storage.put(key, out)
        out.unlink(missing_ok=True)  # the store has it now
        beat("done", 1.0)
        return key, plan.duration_s
