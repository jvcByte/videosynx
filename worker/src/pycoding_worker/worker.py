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
        self._stop = threading.Event()
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
        while not self._stop.wait(HEARTBEAT_SECONDS):
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
            self._stop.set()
            if self._beat_thread is not None:
                self._beat_thread.join(timeout=5)
                self._beat_thread = None
        return True

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self._stop.clear()
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
    """Render a job with the pycoding engine.

    Phase 4 stops here: the artifact is a local file. Phase 6 swaps the final
    step for an upload and returns a storage key instead of a path.
    """

    def __init__(self, output_dir: Path, *, tts: str = "auto", execute: bool = True):
        self.output_dir = Path(output_dir)
        self.tts = tts
        self.execute = execute

    def _speaker(self):
        from pycoding.cli import _real_speaker
        from pycoding.providers import KokoroSpeaker, ProviderError

        class _Args:
            no_tts = False
            tts = self.tts
            voice = "af_heart"
            elevenlabs_api_key = ""
            elevenlabs_voice_id = ""

        args = _Args()
        try:
            return _real_speaker(args)
        except ProviderError:
            return KokoroSpeaker()

    def __call__(self, job: Job, beat: Beat) -> tuple[str, float]:
        import os

        from pycoding.lesson import build_lesson, load_lesson
        from pycoding.render import render_plan
        from pycoding.timeline import build_plan

        self.output_dir.mkdir(parents=True, exist_ok=True)
        out = self.output_dir / f"{job.id}.mp4"

        beat("building", 0.1)
        speaker = self._speaker()

        lesson_path = job.options.get("lesson")
        if lesson_path:
            tutorial = build_lesson(load_lesson(lesson_path), speaker=speaker,
                                    execute=self.execute)
        else:
            from pycoding.providers import GeminiWriter
            from pycoding.script import build_tutorial

            tutorial = build_tutorial(
                job.topic,
                writer=GeminiWriter(os.environ.get("GEMINI_API_KEY", "")),
                speaker=speaker,
                execute=self.execute,
            )

        beat("rendering", 0.7)
        render_plan(tutorial.plan, out)
        beat("done", 1.0)
        return str(out), tutorial.plan.duration_s
