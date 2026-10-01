"""Queue semantics, against a real Postgres.

SKIP LOCKED, lease expiry and the reaper are all database behaviour, so
testing them against a fake would test the fake. These skip with instructions
if no database is reachable.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

import pytest

from videosynx_worker.db import Queue
from videosynx_worker.worker import LeaseLost, Worker

DSN = os.environ.get("VIDEOSYNX_TEST_DSN", "postgresql:///videosynx_test")


def _reachable() -> bool:
    try:
        import psycopg

        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


HAVE_DB = _reachable()

requires_db = pytest.mark.skipif(
    not HAVE_DB,
    reason=(
        f"no Postgres at {DSN}. Create one with:\n"
        "  sudo -u postgres createuser -s $USER && sudo -u postgres createdb $USER\n"
        "  createdb videosynx_test\n"
        "or set VIDEOSYNX_TEST_DSN"
    ),
)


@pytest.fixture
def queue() -> Queue:
    q = Queue(DSN)
    q.install()
    with q._connect() as conn:
        # The ledger too: a balance left behind by one test is the same kind
        # of leak as a job left behind, and it decides the next test's result.
        conn.execute("truncate jobs, credit_ledger")
    return q


def make_due(queue: Queue, job_id: str) -> None:
    """Skip a job's retry wait, so a test need not sit through the backoff.

    The wait is the behaviour under test in its own right; this is how
    everything else gets past it. Poking the row directly is how this file
    already reaches states that take minutes, e.g. lease_owner below.
    """
    with queue._connect() as conn:
        conn.execute("update jobs set not_before = now() where id = %s", (job_id,))


class FakeRenderer:
    """Stands in for the engine. Optionally fails, or beats, or ignores a lost lease."""

    def __init__(self, *, fail_times: int = 0, beat: bool = False,
                 sleep: float = 0.0, raises: Exception | None = None):
        self.fail_times = fail_times
        self.beat = beat
        self.sleep = sleep
        # A specific exception, for the cases where *which* failure it was
        # decides whether the job is worth retrying.
        self.raises = raises
        self.seen: list[str] = []
        self.calls = 0

    def __call__(self, job, beat, should_stop=None):
        self.calls += 1
        self.seen.append(job.id)
        if self.beat:
            beat("rendering", 0.5)
        if self.sleep:
            time.sleep(self.sleep)
        if self.raises is not None:
            raise self.raises
        if self.calls <= self.fail_times:
            raise RuntimeError("boom")
        return f"artifact/{job.id}.mp4", 12.0


# ---------------------------------------------------------------- availability

@requires_db
def test_schema_installs_repeatedly(queue):
    queue.install()  # twice must not raise


# ---------------------------------------------------------------------- submit

@requires_db
def test_submit_and_idempotency(queue):
    first = queue.submit("decorators", idempotency_key="abc")
    again = queue.submit("decorators", idempotency_key="abc")
    # A double-clicked button must not queue (or bill) a second render.
    assert first == again
    assert queue.counts() == {"queued": 1}


# ----------------------------------------------------------------------- claim

@requires_db
def test_claim_is_exclusive(queue):
    """SKIP LOCKED: two workers must never get the same job."""
    ids = {queue.submit(f"topic {i}") for i in range(5)}
    a, b = queue.claim("worker-a"), queue.claim("worker-b")
    assert a and b
    assert a.id != b.id
    assert {a.id, b.id} <= ids


@requires_db
def test_claim_returns_none_when_empty(queue):
    assert queue.claim("worker-a") is None


@requires_db
def test_concurrent_claims_do_not_collide(queue):
    for i in range(40):
        queue.submit(f"topic {i}")

    claimed: list[str] = []
    lock = threading.Lock()

    def drain(owner: str) -> None:
        while (job := queue.claim(owner)) is not None:
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=drain, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == 40, "a job was claimed twice or lost"
    assert len(set(claimed)) == 40


# ------------------------------------------------------------------- lifecycle

@requires_db
def test_job_reaches_a_terminal_state_exactly_once(queue):
    for i in range(20):
        queue.submit(f"topic {i}")

    renderer = FakeRenderer()
    workers = [Worker(queue, renderer, owner=f"w{i}", poll_seconds=0.01)
               for i in range(4)]
    for _ in range(200):
        if not any(w.run_once() for w in workers):
            break

    counts = queue.counts()
    assert counts.get("succeeded") == 20, counts
    assert len(renderer.seen) == len(set(renderer.seen)) == 20, "a job ran twice"


@requires_db
def test_a_failing_job_waits_before_it_is_tried_again(queue):
    """A retry that starts one second after the failure is not a retry.

    Whatever was wrong -- an allowance, an outage, a key -- has had no time to
    change, so the second attempt fails exactly like the first. That is what
    requeueing immediately amounted to.
    """
    job_id = queue.submit("always fails")
    worker = Worker(queue, FakeRenderer(fail_times=99), owner="w0")

    worker.run_once()
    row = queue.get(job_id)
    assert row["status"] == "queued", "should requeue while attempts remain"
    assert row["not_before"] > datetime.now(timezone.utc), "requeued with no wait"

    # Queued, but not yet claimable. This is the whole of the backoff.
    assert worker.run_once() is False, "ran again without waiting"
    assert queue.get(job_id)["attempt"] == 1, "took a second attempt"

    # Once the wait passes it is picked up again.
    make_due(queue, job_id)
    assert worker.run_once() is True
    assert queue.get(job_id)["attempt"] == 2


@requires_db
def test_a_job_out_of_attempts_fails(queue):
    job_id = queue.submit("always fails")
    worker = Worker(queue, FakeRenderer(fail_times=99), owner="w0")

    with queue._connect() as conn:
        conn.execute("update jobs set attempt = max_attempts where id = %s", (job_id,))
    worker.run_once()

    row = queue.get(job_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "RuntimeError"
    assert row["finished_at"] is not None


@requires_db
def test_a_declined_job_is_not_retried(queue):
    """Refused means nothing went wrong and nothing was spent.

    The same input is declined again, so waiting a day to say so only delays
    the answer.
    """
    from videosynx_worker.worker import Refused

    job_id = queue.submit("declined")
    worker = Worker(queue, FakeRenderer(raises=Refused("no model key")), owner="w0")
    worker.run_once()

    row = queue.get(job_id)
    assert row["status"] == "failed", "a declined job was queued for retry"
    assert row["error_code"] == "Refused"
    assert queue.get(job_id)["attempt"] == 1


@requires_db
def test_a_canceled_job_stops_the_render(queue):
    """The point of the whole feature: minutes of CPU that stop.

    Not merely "the row says canceled" -- a worker that kept encoding and
    quietly dropped the result would pass that and still burn the time. So
    this cancels from another thread while the renderer is working, and the
    renderer has to notice through the same callback the real one uses.
    """
    from videosynx_worker.worker import Canceled

    job_id = queue.submit("a long one")
    reached, told = threading.Event(), threading.Event()

    def render(job, beat, should_stop=None):
        beat("rendering", 0.7)
        reached.set()
        # Stand in for the frame loop. The stopper *raises* -- that is how a
        # render abandons itself -- so this records the raise and re-raises,
        # exactly as render_plan's loop does.
        for _ in range(1000):
            try:
                should_stop()
            except Canceled:
                told.set()
                raise
            time.sleep(0.01)
        raise AssertionError("the render was never stopped")

    worker = Worker(queue, render, owner="w0", poll_seconds=0.01)
    thread = threading.Thread(target=worker.run_once, daemon=True)
    thread.start()
    assert reached.wait(timeout=5), "the render never started"

    assert queue.cancel(job_id) is True
    thread.join(timeout=15)
    assert told.is_set(), "the renderer was never told to stop"

    row = queue.get(job_id)
    assert row["status"] == "canceled", "a canceled job was recorded as failed"
    assert row["error_code"] is None, "a cancel was written up as a failure"


@requires_db
def test_a_worker_that_ignores_the_cancel_cannot_undo_it(queue):
    """The signal is cooperative, so the writes have to be defended too.

    A renderer that never polls -- or one mid-way through a model call when
    the cancel lands -- will run to the end and try to finish. The lease is
    deliberately left in place by `cancel`, so `finish`'s lease check is not
    what stops it; this is.
    """
    job_id = queue.submit("a long one")
    job = queue.claim("w0")
    assert queue.cancel(job_id) is True

    assert queue.finish(job_id, "w0", artifact_key="k", duration_s=1.0) is False, \
        "a canceled job was completed anyway"
    assert queue.get(job_id)["status"] == "canceled"

    assert queue.fail(job_id, "w0", code="RuntimeError", message="boom") is False, \
        "a cancel was written up as a failure"
    assert queue.get(job_id)["status"] == "canceled"
    # The failure path would have requeued it; canceled is terminal.
    assert queue.get(job_id)["error_code"] is None


@requires_db
def test_a_canceled_job_is_not_claimed_or_reaped(queue):
    """Terminal, without any code saying so.

    `canceled` is not in ACTIVE_STATUSES and is not `queued`, so the claim
    query and the reaper both skip it as written -- this pins that, because it
    is the sort of thing a future status list could quietly change.
    """
    job_id = queue.submit("never run")
    assert queue.cancel(job_id) is True

    assert queue.claim("w0") is None, "a canceled job was handed out again"
    assert queue.reap() == [], "a canceled job was reaped"
    assert queue.get(job_id)["status"] == "canceled"


@requires_db
def test_retry_reuses_the_cached_work(queue):
    """The coupling that makes lease-and-reap safe: a retry must not re-spend.

    Phase 6 adds the TTS cache; this asserts the property that makes retrying
    acceptable -- attempt counts advance, so a job cannot loop forever.
    """
    job_id = queue.submit("transient")
    renderer = FakeRenderer(fail_times=1)
    worker = Worker(queue, renderer, owner="w0")

    worker.run_once()
    assert queue.get(job_id)["attempt"] == 1
    make_due(queue, job_id)
    worker.run_once()
    row = queue.get(job_id)
    assert row["status"] == "succeeded"
    assert row["attempt"] == 2


# ------------------------------------------------------------------- heartbeat

@requires_db
def test_heartbeat_extends_the_lease(queue):
    queue.submit("t")
    job = queue.claim("w0")
    before = queue.get(job.id)["lease_expires_at"]
    time.sleep(0.05)
    assert queue.heartbeat(job.id, "w0", stage="rendering", progress=0.5) == "leased"
    row = queue.get(job.id)
    assert row["lease_expires_at"] > before
    assert row["stage"] == "rendering"
    assert row["progress"] == pytest.approx(0.5)


@requires_db
def test_heartbeat_from_another_worker_is_refused(queue):
    queue.submit("t")
    job = queue.claim("w0")
    assert queue.heartbeat(job.id, "someone-else") is None


# ---------------------------------------------------------------- the big one

@requires_db
def test_lost_lease_stops_the_render(queue):
    """If the lease is gone, another worker may be rendering this job.

    Finishing anyway would render the same video twice and bill for it twice,
    so the worker must abandon rather than complete.
    """
    job_id = queue.submit("t")
    worker = Worker(queue, FakeRenderer(), owner="w0")
    job = queue.claim(worker.owner)
    worker._current = job.id

    # Simulate the reaper having taken it away.
    with queue._connect() as conn:
        conn.execute("update jobs set lease_owner = 'other' where id = %s", (job_id,))

    with pytest.raises(LeaseLost):
        worker.beat("rendering", 0.5)


@requires_db
def test_finish_is_refused_when_the_lease_moved(queue):
    job_id = queue.submit("t")
    queue.claim("w0")
    assert queue.finish(job_id, "w1", artifact_key="x", duration_s=1.0) is False
    assert queue.get(job_id)["status"] != "succeeded"


# ------------------------------------------------------------------ the charge

@requires_db
def test_finishing_a_paid_job_charges_it_by_the_minute(queue):
    """The whole of the per-minute price, in one place.

    Twelve seconds is a minute, because there is no fraction of one to sell.
    The charge lands in the same statement that records the video, so a job
    cannot be marked finished and left unbilled by a process that died in
    between -- and it is that statement, not two, because this connection is
    in autocommit and a second one would be a second commit.
    """
    job_id = queue.submit("paid", user_id="payer", options={"credits_per_minute": 1})
    queue.claim("w0")
    assert queue.finish(job_id, "w0", artifact_key="k", duration_s=372.0) is True

    with queue._connect() as conn:
        rows = conn.execute(
            "select delta, reason from credit_ledger where user_id = 'payer'"
        ).fetchall()
    assert [r["delta"] for r in rows] == [-7], "372 seconds is seven minutes"
    assert rows[0]["reason"] == f"job:{job_id}"


@requires_db
def test_a_job_inside_its_allowance_is_finished_and_not_charged(queue):
    """No rate on the job means no charge -- and still a finished video. The
    result reports the update rather than the insert, or every free job would
    look like a failed finish."""
    job_id = queue.submit("free", user_id="nonpayer")
    queue.claim("w0")
    assert queue.finish(job_id, "w0", artifact_key="k", duration_s=372.0) is True

    with queue._connect() as conn:
        rows = conn.execute(
            "select 1 from credit_ledger where user_id = 'nonpayer'"
        ).fetchall()
    assert rows == []
    assert queue.get(job_id)["status"] == "succeeded"


@requires_db
def test_finishing_twice_charges_once(queue):
    """A second finish cannot land: the lease is cleared by the first, so the
    update matches nothing, and the charge rides on the update."""
    job_id = queue.submit("once", user_id="payer2", options={"credits_per_minute": 1})
    queue.claim("w0")
    assert queue.finish(job_id, "w0", artifact_key="k", duration_s=60.0) is True
    assert queue.finish(job_id, "w0", artifact_key="k", duration_s=60.0) is False

    with queue._connect() as conn:
        n = conn.execute(
            "select count(*) as n from credit_ledger where user_id = 'payer2'"
        ).fetchone()["n"]
    assert n == 1


@requires_db
def test_a_failed_job_is_not_charged(queue):
    """A render nobody received is free, which is the whole reason the charge
    sits on `finish` and not on `submit`."""
    job_id = queue.submit("doomed", user_id="unlucky",
                          options={"credits_per_minute": 1})
    queue.claim("w0")
    queue.fail(job_id, "w0", code="RenderFailed", message="no")

    with queue._connect() as conn:
        rows = conn.execute(
            "select 1 from credit_ledger where user_id = 'unlucky'"
        ).fetchall()
    assert rows == []


@requires_db
def test_the_balance_says_what_the_ledger_says(queue):
    queue.submit("a", user_id="reader", options={"credits_per_minute": 1})
    with queue._connect() as conn:
        conn.execute(
            "insert into credit_ledger (user_id, delta, reason)"
            " values ('reader', 10, 'test'), ('reader', -3, 'test')"
        )
    assert queue.balance("reader") == 7
    assert queue.balance("nobody") == 0


# -------------------------------------------------------------------- the reaper

@requires_db
def test_reaper_requeues_an_abandoned_job(queue):
    """A worker that is SIGKILLed leaves a job it can never finish."""
    job_id = queue.submit("t")
    queue.claim("w0")
    with queue._connect() as conn:
        conn.execute(
            "update jobs set lease_expires_at = now() - interval '1 hour' where id = %s",
            (job_id,),
        )
    assert queue.reap() == [job_id]
    row = queue.get(job_id)
    assert row["status"] == "queued"
    assert row["lease_owner"] is None


@requires_db
def test_reaper_fails_a_job_out_of_attempts(queue):
    job_id = queue.submit("t")
    queue.claim("w0")
    with queue._connect() as conn:
        conn.execute(
            """
            update jobs set lease_expires_at = now() - interval '1 hour',
                            attempt = max_attempts
             where id = %s
            """,
            (job_id,),
        )
    assert queue.reap() == [job_id]
    row = queue.get(job_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "lease_expired"


@requires_db
def test_reaper_leaves_a_live_lease_alone(queue):
    """A worker between heartbeats must not have its job stolen."""
    job_id = queue.submit("t")
    queue.claim("w0")
    assert queue.reap() == []
    assert queue.get(job_id)["status"] == "leased"


@requires_db
def test_reaper_does_not_touch_the_queue_of_running_jobs(queue):
    for i in range(3):
        queue.submit(f"t{i}")
    queue.claim("w0")
    assert queue.reap() == []
    assert queue.counts().get("queued") == 2
