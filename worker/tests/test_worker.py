"""The worker loop, against a stand-in queue.

These deliberately do not need Postgres. The loop is what breaks when the stop
that ends a job's heartbeat and the stop that ends the worker are confused for
one another, and a bug that only appears with a database running is a bug that
hides.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from videosynx_worker.db import Job
from videosynx_worker.worker import Worker


class FakeQueue:
    """Hands out jobs from a list, then reports the queue empty."""

    def __init__(self, jobs: int = 3):
        self.remaining = [f"job-{i}" for i in range(jobs)]
        self.finished: list[str] = []
        self.failed: list[tuple[str, str]] = []
        # Kept beside `failed` rather than widening its tuples, because that
        # list is asserted on by exact equality.
        self.delays: list[tuple[str, float | None]] = []
        self.heartbeats = 0

    def claim(self, owner):
        if not self.remaining:
            return None
        return Job(id=self.remaining.pop(0), user_id="u", topic="t", tier="free",
                   options={}, attempt=1, max_attempts=3)

    def heartbeat(self, job_id, owner, stage=None, progress=None):
        self.heartbeats += 1
        return True

    def finish(self, job_id, owner, *, artifact_key, duration_s):
        self.finished.append(job_id)
        return True

    def fail(self, job_id, owner, *, code, message, delay_seconds=None):
        self.failed.append((job_id, code))
        self.delays.append((job_id, delay_seconds))
        return True


def test_a_spent_allowance_is_named_as_such():
    """The job row has to say whose problem it is.

    An allowance that comes back tomorrow needs a different answer from the
    page than a provider that is simply broken, and the page can only tell
    them apart by the error code.
    """
    from videosynx.providers import ProviderError
    from videosynx_worker.worker import failure_code

    assert failure_code(ProviderError("spent", reason="quota")) == "provider_quota"
    assert failure_code(ProviderError("busy", reason="busy")) == "provider_busy"
    assert failure_code(ValueError("not a provider's fault")) == "ValueError"


def test_the_retry_schedule_grows():
    """A brief outage should be retried while it is fresh; a spent daily
    allowance has to be outlasted, and it comes back in a day."""
    from videosynx_worker.worker import RETRY_BACKOFF_S, retry_delay

    delays = [retry_delay("provider_busy", n) for n in range(1, 7)]
    assert delays == list(RETRY_BACKOFF_S)
    assert delays == sorted(delays), "the gaps must not shrink"
    assert delays[0] == 60, "the first retry should be soon"
    assert delays[-1] >= 24 * 3600, "the last must outlast a daily quota"
    # Out of schedule: nothing left to wait for.
    assert retry_delay("provider_busy", 7) is None
    assert retry_delay("provider_busy", 0) is None


def test_a_declined_job_is_never_retried():
    """`Refused` says it itself: nothing went wrong, nothing was spent. The
    same input declines again, so a day of waiting only delays the answer."""
    from videosynx_worker.worker import retry_delay

    assert retry_delay("Refused", 1) is None
    assert retry_delay("Refused", 3) is None


def test_a_first_attempt_gets_the_first_gap():
    """`attempt` is the attempt that just failed, so it indexes from 1."""
    from videosynx_worker.worker import retry_delay

    assert retry_delay("provider_error", 1) == 60
    assert retry_delay("provider_error", 2) == 300


def test_the_worker_hands_the_queue_the_delay():
    """The policy is only real if it reaches the row."""
    queue = FakeQueue(jobs=1)
    worker = Worker(queue, lambda job, beat, should_stop=None: (_ for _ in ()).throw(
        RuntimeError("boom")), owner="w0")
    worker.run_once()

    assert queue.failed == [("job-0", "RuntimeError")]
    assert queue.delays == [("job-0", 60.0)]


def test_local_mode_refuses_work_it_cannot_do_without_a_model():
    """Belt and braces: the API refuses this before it is ever queued, but the
    queue can be written to directly."""
    from videosynx_worker.db import Job
    from videosynx_worker.worker import EngineRenderer, Refused

    renderer = EngineRenderer(Path("/tmp"))
    job = Job(id="j", user_id="u", topic="a topic that needs writing", tier="free",
              options={"generation": "local", "tts": "espeak"}, attempt=1,
              max_attempts=1)
    with pytest.raises(Refused):
        renderer(job, lambda stage, progress: None)


def test_a_local_job_never_builds_a_provider(tmp_path, monkeypatch):
    """Local mode's promise is that nothing is spent, and every provider has
    to be constructed with a key -- so a machine with no keys, which is
    exactly where someone reaches for local mode, would fail here rather than
    render. The writer is built on a conditional, and this is what says the
    conditional is on the right side of it.
    """
    import json

    import numpy as np
    import videosynx.providers as providers
    import videosynx.render as render
    from videosynx.timeline import SR
    from videosynx_worker.worker import EngineRenderer

    def refuse():
        raise AssertionError("a local job must not build a provider")

    monkeypatch.setattr(providers, "default_writer", refuse)
    monkeypatch.setattr(render, "render_plan", lambda *a, **k: None)

    class Silent:
        def say(self, text, *, previous="", following=""):
            # Above the silence floor, or the narration is trimmed to nothing.
            return np.full(SR // 4, 4000, dtype=np.int16)

    renderer = EngineRenderer(tmp_path)
    monkeypatch.setattr(renderer, "_speaker", lambda *a, **k: Silent())

    lesson = Path(__file__).resolve().parents[2] / "lessons" / "15-1-when-to-fine-tune.json"
    job = Job(id="local-1", user_id="u", topic="", tier="free", attempt=1,
              max_attempts=1,
              options={"generation": "local", "tts": "espeak",
                       "script": lesson.read_text()})
    key, duration = renderer(job, lambda stage, progress: None)

    assert duration > 0, "nothing was rendered"
    assert json.loads((tmp_path / "local-1.script.json").read_text())["sections"]


def test_a_job_that_needs_writing_goes_through_the_chain(tmp_path, monkeypatch):
    """The other side of the same conditional, and the seam that decides
    whether any of the provider work reaches the service at all."""
    import videosynx.providers as providers
    import videosynx.render as render
    from videosynx.providers import ProviderError
    from videosynx_worker.worker import EngineRenderer

    seen = []

    class Reached:
        def write(self, prompt):
            seen.append(prompt)
            raise ProviderError("reached the chain")

    monkeypatch.setattr(providers, "default_writer", lambda: Reached())
    monkeypatch.setattr(render, "render_plan", lambda *a, **k: None)

    renderer = EngineRenderer(tmp_path)
    monkeypatch.setattr(renderer, "_speaker", lambda *a, **k: None)

    job = Job(id="auto-1", user_id="u", topic="Python decorators", tier="free",
              attempt=1, max_attempts=1, options={"tts": "espeak"})
    with pytest.raises(ProviderError, match="reached the chain"):
        renderer(job, lambda stage, progress: None)
    assert seen and "decorators" in seen[0]


def render_ok(job, beat, should_stop=None):
    beat("rendering", 0.5)
    return "key", 1.0


def run_until(worker: Worker, done, timeout: float = 5.0) -> None:
    """Run the worker until *done* holds, then stop it and check it stopped."""
    thread = threading.Thread(target=worker.run_forever, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    try:
        while not done() and time.time() < deadline:
            time.sleep(0.005)
    finally:
        worker.stop()
        thread.join(timeout=timeout)
    assert not thread.is_alive(), "run_forever ignored stop()"


def test_run_forever_keeps_going_after_the_first_job():
    """The worker used to exit after exactly one job.

    run_once set the same event that run_forever loops on, to stop its own
    heartbeat thread. The loop condition then saw a stop that was only ever
    meant for the job that had just finished.
    """
    queue = FakeQueue(jobs=3)
    worker = Worker(queue, render_ok, owner="test", poll_seconds=0.005)

    run_until(worker, lambda: len(queue.finished) >= 3)

    assert queue.finished == ["job-0", "job-1", "job-2"]


def test_the_worker_survives_a_job_that_fails():
    """One bad job must not take the worker down with it."""
    queue = FakeQueue(jobs=3)

    def renderer(job, beat, should_stop=None):
        if job.id == "job-1":
            raise RuntimeError("boom")
        return "key", 1.0

    worker = Worker(queue, renderer, owner="test", poll_seconds=0.005)
    run_until(worker, lambda: len(queue.finished) >= 2 and queue.failed)

    assert queue.failed == [("job-1", "RuntimeError")]
    assert queue.finished == ["job-0", "job-2"]


def test_stop_wakes_a_polling_worker():
    """stop() must interrupt the idle poll rather than wait it out."""
    queue = FakeQueue(jobs=0)
    worker = Worker(queue, render_ok, owner="test", poll_seconds=30)

    thread = threading.Thread(target=worker.run_forever, daemon=True)
    thread.start()
    time.sleep(0.05)  # let it reach the poll
    worker.stop()
    thread.join(timeout=2)

    assert not thread.is_alive(), "stop() did not wake the worker out of its poll"


def test_each_job_gets_its_own_heartbeat():
    """The heartbeat stop is per job, so job two must still be beating.

    If the per-job stop is not cleared before the next job starts, the second
    job's beat thread returns immediately and renders with no lease at all.
    """
    queue = FakeQueue(jobs=3)
    worker = Worker(queue, render_ok, owner="test", poll_seconds=0.005)

    run_until(worker, lambda: len(queue.finished) >= 3)

    # One beat from the renderer per job, plus at least the initial one.
    assert queue.heartbeats >= 3


def test_a_lesson_object_renders_without_being_written_to_disk(tmp_path, monkeypatch):
    """The whole point of sending a lesson as an object rather than as a path.

    A path only means anything when the caller and the worker share a disk,
    which a client of a hosted service does not. This renders from the object
    itself, and refuses a provider the same way local mode does -- a lesson
    that is already written needs nothing written for it.
    """
    import json

    import numpy as np
    import videosynx.providers as providers
    import videosynx.render as render
    from videosynx.timeline import SR
    from videosynx_worker.worker import EngineRenderer

    def refuse():
        raise AssertionError("a submitted lesson must not build a provider")

    monkeypatch.setattr(providers, "default_writer", refuse)
    monkeypatch.setattr(render, "render_plan", lambda *a, **k: None)

    class Silent:
        def say(self, text, *, previous="", following=""):
            return np.full(SR // 4, 4000, dtype=np.int16)

    renderer = EngineRenderer(tmp_path)
    monkeypatch.setattr(renderer, "_speaker", lambda *a, **k: Silent())

    authored = json.loads(
        (Path(__file__).resolve().parents[2] / "lessons"
         / "15-1-when-to-fine-tune.json").read_text())

    job = Job(id="obj-1", user_id="u", topic="", tier="free", attempt=1,
              max_attempts=1,
              options={"generation": "local", "tts": "espeak", "lesson": authored})
    _, duration = renderer(job, lambda stage, progress: None)

    assert duration > 0, "nothing was rendered"
    # The lesson that was submitted is the lesson that was rendered. The
    # export has no title of its own, so the heading is what says so.
    saved = json.loads((tmp_path / "obj-1.script.json").read_text())
    assert len(saved["sections"]) == len(authored["sections"])
    assert saved["sections"][0]["heading"] == authored["sections"][0]["explain"]["heading"]


def test_a_lesson_object_with_nothing_in_it_is_refused(tmp_path, monkeypatch):
    """`lesson` carrying an object that is not a lesson is a refusal, not a
    render of nothing. The API refuses it earlier, but the queue can be
    written to directly."""
    import videosynx.render as render
    from videosynx_worker.worker import EngineRenderer, Refused

    monkeypatch.setattr(render, "render_plan", lambda *a, **k: None)
    renderer = EngineRenderer(tmp_path)
    job = Job(id="obj-2", user_id="u", topic="", tier="free", attempt=1,
              max_attempts=1,
              options={"generation": "local", "tts": "espeak",
                       "lesson": {"title": "No sections"}})
    with pytest.raises(Refused, match="sections"):
        renderer(job, lambda stage, progress: None)
