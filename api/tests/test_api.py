"""API behaviour, against a real Postgres.

The two assertions that matter most are both about *not* spending: a rejected
submission must leave no row behind, and nothing on this path may construct a
client that costs money.
"""

from __future__ import annotations

import os
import threading

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from pycoding_api.app import create_app
from pycoding_api.quota import TIERS, QuotaExceeded, submit_within_quota, used_today
from pycoding_worker.db import Queue

DSN = os.environ.get("PYCODING_TEST_DSN", "postgresql:///pycoding_test")


def _reachable() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _reachable(),
    reason=f"no Postgres at {DSN}. create it with: createdb pycoding_test",
)


@pytest.fixture
def client() -> TestClient:
    app = create_app(DSN)
    with TestClient(app) as c:  # the context manager runs startup/install
        yield c


@pytest.fixture(autouse=True)
def clean() -> None:
    if not _reachable():
        return
    Queue(DSN).install()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("truncate jobs")


def auth(user: str = "user-1", tier: str = "free") -> dict:
    # Development auth: enabled because no JWT secret is configured in tests.
    return {"X-Dev-User": f"{user}:{tier}"}


# ------------------------------------------------------------------- preamble

@requires_db
def test_health(client):
    assert client.get("/health").json() == {"ok": True}


@requires_db
def test_jobs_require_a_token(client):
    assert client.post("/jobs", json={"topic": "decorators"}).status_code == 401
    assert client.get("/jobs").status_code == 401
    assert client.get("/jobs/anything").status_code == 401


@requires_db
def test_an_invalid_authorization_header_is_rejected(client):
    r = client.get("/jobs", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code in (401, 503)


# ------------------------------------------------------------------- submit

@requires_db
def test_submit_queues_a_job(client):
    r = client.post("/jobs", json={"topic": "Python decorators"}, headers=auth())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["tier"] == "free"
    assert body["used_today"] == 1

    job = client.get(f"/jobs/{body['id']}", headers=auth()).json()
    assert job["topic"] == "Python decorators"


@requires_db
def test_topic_is_validated(client):
    assert client.post("/jobs", json={"topic": "ab"}, headers=auth()).status_code == 422
    assert client.post("/jobs", json={}, headers=auth()).status_code == 422


# -------------------------------------------------------------------- quota

@requires_db
def test_free_tier_is_capped_and_leaves_nothing_behind(client):
    """The point of the cap is that the expensive work never starts."""
    limit = TIERS["free"].jobs_per_day
    for i in range(limit):
        r = client.post("/jobs", json={"topic": f"topic number {i}"}, headers=auth())
        assert r.status_code == 202, r.text

    r = client.post("/jobs", json={"topic": "one too many"}, headers=auth())
    assert r.status_code == 429
    assert r.json()["detail"]["error"] == "quota_exceeded"

    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        n = conn.execute("select count(*) as n from jobs").fetchone()["n"]
    assert n == limit, "a rejected submission created a row"


@requires_db
def test_nothing_on_the_submit_path_constructs_a_spending_client(client, monkeypatch):
    """Submission must not be able to cost money.

    Even a rejected request has to get as far as validating before it can be
    refused, so this asserts the whole path is free of the paid clients rather
    than trusting that the rejection happens first.
    """
    import pycoding.providers as providers

    built: list[str] = []
    monkeypatch.setattr(providers.GeminiWriter, "__init__",
                        lambda *a, **k: built.append("gemini"))
    monkeypatch.setattr(providers.ElevenLabsSpeaker, "__init__",
                        lambda *a, **k: built.append("elevenlabs"))

    client.post("/jobs", json={"topic": "an accepted job"}, headers=auth())
    for i in range(TIERS["free"].jobs_per_day + 2):
        client.post("/jobs", json={"topic": f"job number {i}"}, headers=auth())

    assert built == [], f"submitting constructed {built}"


@requires_db
def test_quota_is_per_user(client):
    for i in range(TIERS["free"].jobs_per_day):
        assert client.post("/jobs", json={"topic": f"mine {i}"},
                           headers=auth("user-a")).status_code == 202
    assert client.post("/jobs", json={"topic": "mine too many"},
                       headers=auth("user-a")).status_code == 429
    # Someone else is unaffected.
    assert client.post("/jobs", json={"topic": "theirs"},
                       headers=auth("user-b")).status_code == 202


@requires_db
def test_quota_endpoint_reports_remaining(client):
    client.post("/jobs", json={"topic": "one"}, headers=auth())
    body = client.get("/me/quota", headers=auth()).json()
    assert body["tier"] == "free"
    assert body["used_today"] == 1
    assert body["remaining"] == TIERS["free"].jobs_per_day - 1
    assert body["tts"] == "kokoro"


@requires_db
def test_paid_tier_gets_a_bigger_allowance(client):
    body = client.get("/me/quota", headers=auth(tier="paid")).json()
    assert body["jobs_per_day"] == TIERS["paid"].jobs_per_day
    assert body["tts"] == "elevenlabs"


@requires_db
def test_the_tier_is_frozen_onto_the_job(client):
    r = client.post("/jobs", json={"topic": "a paid job"},
                    headers=auth(tier="paid"))
    job_id = r.json()["id"]
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        row = conn.execute("select tier, options from jobs where id = %s",
                           (job_id,)).fetchone()
    # A plan change while the job waits in the queue must not change what it
    # is allowed to spend.
    assert row["tier"] == "paid"
    assert row["options"]["tts"] == "elevenlabs"


@requires_db
def test_a_client_cannot_override_the_tts_the_tier_allows(client):
    r = client.post("/jobs", json={"topic": "sneaky"},
                    headers=auth(tier="free"))
    job_id = r.json()["id"]
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        options = conn.execute("select options from jobs where id = %s",
                               (job_id,)).fetchone()["options"]
    assert options["tts"] == "kokoro", "a client raised its own tier's voice"


# -------------------------------------------------------------- idempotency

@requires_db
def test_a_repeated_submit_does_not_consume_quota_twice(client):
    key = {"Idempotency-Key": "same-request", **auth()}
    first = client.post("/jobs", json={"topic": "decorators"}, headers=key)
    again = client.post("/jobs", json={"topic": "decorators"}, headers=key)
    assert first.json()["id"] == again.json()["id"]
    assert client.get("/me/quota", headers=auth()).json()["used_today"] == 1


@requires_db
def test_concurrent_submits_cannot_both_pass_a_full_quota():
    """Two requests racing a check with room for one: exactly one may win."""
    user = "racer"
    limit = TIERS["free"].jobs_per_day
    for i in range(limit - 1):
        submit_within_quota(DSN, user_id=user, topic=f"filler {i}")

    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(n: int) -> None:
        try:
            submit_within_quota(DSN, user_id=user, topic=f"racing {n}")
            with lock:
                outcomes.append("accepted")
        except QuotaExceeded:
            with lock:
                outcomes.append("rejected")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("accepted") == 1, outcomes
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        assert used_today(conn, user) == limit


# ------------------------------------------------------------------ ownership

@requires_db
def test_another_users_job_is_not_visible(client):
    job_id = client.post("/jobs", json={"topic": "private"},
                         headers=auth("owner")).json()["id"]
    assert client.get(f"/jobs/{job_id}", headers=auth("someone-else")).status_code == 404


@requires_db
def test_listing_only_returns_your_own_jobs(client):
    client.post("/jobs", json={"topic": "mine"}, headers=auth("user-a"))
    client.post("/jobs", json={"topic": "theirs"}, headers=auth("user-b"))
    mine = client.get("/jobs", headers=auth("user-a")).json()
    assert [j["topic"] for j in mine] == ["mine"]


@requires_db
def test_unknown_job_is_404(client):
    assert client.get("/jobs/00000000-0000-0000-0000-000000000000",
                      headers=auth()).status_code == 404
