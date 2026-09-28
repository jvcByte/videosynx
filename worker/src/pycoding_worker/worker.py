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

log = logging.getLogger("pycoding.worker")

# Called by the renderer at stage boundaries: (stage name, progress 0..1).
Beat = Callable[[str, float], None]


class LeaseLost(RuntimeError):
    """The lease is no longer ours. Stop immediately; do not finish the job."""


class Renderer(Protocol):
    def __call__(self, job: Job, beat: Beat) -> tuple[str, float]:
        """Render *job*; return (artifact_key, duration_seconds)."""


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

    # ------------------------------------------------------------- heartbeats

    def beat(self, stage: str | None = None, progress: float | None = None) -> None:
        """Extend the lease, or raise if it is gone."""
        if self._lease_lost.is_set() or self._current is None:
            raise LeaseLost("lease lost")
        if not self.queue.heartbeat(self._current, self.owner,
                                    stage=stage, progress=progress):
            self._lease_lost.set()
            raise LeaseLost(f"lease on {self._current} is no longer ours")

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
                if not self.queue.heartbeat(self._current, self.owner):
                    log.warning("lease on %s lost; signalling the run to stop",
                                self._current)
                    self._lease_lost.set()
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
        # Clear before starting: a beat thread that sees this already set
        # returns immediately and the job renders with no heartbeat at all.
        self._beat_stop.clear()
        self._beat_thread = threading.Thread(target=self._beat_loop, daemon=True)
        self._beat_thread.start()

        try:
            self.beat("starting", 0.0)
            artifact, duration = self.renderer(job, lambda s, p: self.beat(s, p))
            if self._lease_lost.is_set():
                # Rendered, but we no longer own the job. Someone else is
                # responsible for it now; finishing here would double-charge.
                log.warning("dropping %s: lease lost during the render", job.id)
                return True
            if self.queue.finish(job.id, self.owner, artifact_key=artifact,
                                 duration_s=duration):
                log.info("finished %s -> %s", job.id, artifact)
            else:
                log.warning("could not finish %s: lease is gone", job.id)
        except LeaseLost:
            log.warning("abandoning %s: lease lost", job.id)
        except Exception as exc:  # noqa: BLE001 - recorded against the job
            log.exception("job %s failed", job.id)
            self.queue.fail(job.id, self.owner,
                            code=type(exc).__name__, message=str(exc))
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
    """Render a job with the pycoding engine, then store the result.

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

    def _speaker(self, tts: str | None = None, voice: str | None = None):
        import os

        from pycoding.providers import (
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

    def __call__(self, job: Job, beat: Beat) -> tuple[str, float]:
        import os

        from pycoding.lesson import (
            build_lesson,
            lesson_from_script,
            load_lesson,
        )
        from pycoding.providers import GeminiWriter
        from pycoding.render import render_plan
        from pycoding.script import build_tutorial
        from pycoding.timeline import without_captions

        self.output_dir.mkdir(parents=True, exist_ok=True)
        out = self.output_dir / f"{job.id}.mp4"

        # Everything below was decided at submit time: the tier-owned keys by
        # submit_within_quota, the rest by the user's saved settings. Reading
        # them here is what makes either layer mean anything.
        options = job.options
        mode = options.get("mode") or "both"
        max_snippets = options.get("max_snippets")
        style = options.get("style") or "sketch"
        captions = options.get("captions", True)
        watermark = "pycoding" if options.get("watermark") else None
        width, height = (options.get("resolution") or [1920, 1080])[:2]
        log.info("mode: %s, style: %s, %sx%s, watermark: %s",
                 mode, style, width, height, bool(watermark))

        beat("building", 0.1)
        speaker = self._speaker(options.get("tts") or self.tts, options.get("voice"))
        writer = GeminiWriter(os.environ.get("GEMINI_API_KEY", ""))

        # Three ways in, in order of how much the caller has already decided:
        # their own script, a lesson file on this machine, or just a topic.
        script = options.get("script")
        lesson_path = options.get("lesson")
        if script:
            lesson = lesson_from_script(writer, script)
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
        render_plan(plan, out, width=width, height=height, style=style,
                    watermark=watermark)

        if self.storage is None:
            beat("done", 1.0)
            return str(out), tutorial.plan.duration_s

        from .storage import video_key

        beat("uploading", 0.95)
        key = video_key(job.tier, job.id)
        self.storage.put(key, out)
        out.unlink(missing_ok=True)  # the store has it now
        beat("done", 1.0)
        return key, tutorial.plan.duration_s
