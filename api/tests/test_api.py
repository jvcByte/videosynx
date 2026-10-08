"""API behaviour, against a real Postgres.

The two assertions that matter most are both about *not* spending: a rejected
submission must leave no row behind, and nothing on this path may construct a
client that costs money.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Json

from videosynx_api.app import (
    Mode,
    SubmitRequest,
    check_lesson,
    create_app,
    can_render_locally,
    job_label,
    submitted_lesson,
)
from videosynx_api.quota import TIERS, QuotaExceeded, submit_within_quota, used_today
from videosynx_api.settings import KOKORO_VOICES as KOKORO_VOICE_IDS
from videosynx_api.settings import Settings
from videosynx_worker.db import Queue
from videosynx_worker.storage import LocalStorage, video_key

DSN = os.environ.get("VIDEOSYNX_TEST_DSN", "postgresql:///videosynx_test")


def _reachable() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _reachable(),
    reason=f"no Postgres at {DSN}. create it with: createdb videosynx_test",
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
        # Every table a test writes, or run order decides pass/fail. This bit
        # once already: a saved setting leaked into the next test's defaults.
        conn.execute("truncate jobs, user_settings")


def auth(user: str = "user-1", tier: str = "free") -> dict:
    # Development auth: enabled because no JWT secret is configured in tests.
    return {"X-Dev-User": f"{user}:{tier}"}


# --------------------------------------------------------- request validation
# These need no database: they are the schema, and the schema is the contract.

def test_a_topic_on_its_own_is_enough():
    assert SubmitRequest(topic="decorators").script is None


def test_a_script_on_its_own_is_enough():
    """A caller pasting a script has no topic to give."""
    body = SubmitRequest(script="Scene 1. Someone explains a thing.")
    assert body.topic == ""
    assert job_label(body).startswith("Scene 1.")


def test_a_lesson_path_on_its_own_is_enough():
    assert SubmitRequest(lesson="/tmp/lesson.json").topic == ""


def test_a_request_with_nothing_to_render_is_refused():
    with pytest.raises(ValueError, match="provide a topic, a script, or a lesson"):
        SubmitRequest()


def test_a_topic_that_is_too_short_is_still_refused():
    with pytest.raises(ValueError, match="at least 3 characters"):
        SubmitRequest(topic="ab")


def test_the_mode_must_be_one_the_engine_knows():
    with pytest.raises(ValueError):
        SubmitRequest(topic="decorators", mode="podcast")


def test_an_oversized_script_is_refused():
    """There is no body limit anywhere else, so this field is the only bound."""
    from videosynx_api.app import MAX_SCRIPT_CHARS

    with pytest.raises(ValueError):
        SubmitRequest(script="x" * (MAX_SCRIPT_CHARS + 1))


def test_the_api_and_the_engine_agree_on_the_modes():
    """The API owns the wire contract and does not import the engine.

    Copying three strings is cheap; letting them drift is not, so this is the
    test that keeps them together.
    """
    from typing import get_args

    from videosynx.script import MODES as ENGINE_MODES

    assert set(get_args(Mode)) == set(ENGINE_MODES)


def test_a_script_names_the_job_when_there_is_no_topic():
    body = SubmitRequest(script="# A lesson about queues\n\nScene 1.")
    assert job_label(body) == "A lesson about queues"


def test_a_script_wins_over_a_topic_in_the_label():
    """Because it wins in the renderer too.

    Submitting a fine-tuning script with "dictionary in python" still in the
    topic box produced a row labelled "dictionary in python" containing the
    fine-tuning lesson.
    """
    body = SubmitRequest(topic="dictionary in python",
                         script="# When to Fine-Tune\n\nScene 1.")
    assert job_label(body) == "When to Fine-Tune"


# ---------------------------------------------------------------- the voices

@requires_db
def test_every_voice_is_listed_with_a_language(client):
    voices = client.get("/voices", headers=auth()).json()
    assert len(voices) > 20
    assert all(v["id"] and v["language"] and v["gender"] for v in voices)
    # English first: it is what almost every caller wants, and fifty-four
    # entries in alphabetical order bury it.
    assert voices[0]["language"].startswith("English")


def test_the_voice_catalogue_matches_the_model_it_describes():
    """KOKORO_VOICES is a copy of what voices-v1.0.bin holds.

    Copying is fine; a copy that has drifted is not -- the dropdown would
    offer a voice that fails at synthesis. This loads the real model and
    compares. Skipped where the model is not installed, since a checkout
    without it cannot narrate at all.
    """
    pytest.importorskip("kokoro_onnx")
    from pathlib import Path

    from videosynx.providers import KOKORO_MODEL, KOKORO_VOICES, KokoroSpeaker

    if not Path(KOKORO_MODEL).exists() or not Path(KOKORO_VOICES).exists():
        pytest.skip("the Kokoro model files are not installed")

    real = set(KokoroSpeaker()._kokoro.get_voices())
    assert set(KOKORO_VOICE_IDS) == real


def test_a_missing_topic_and_an_empty_script_fall_back_to_untitled():
    assert job_label(SubmitRequest(script="   \n\n  ")) == "untitled"


# ------------------------------------------------------------- what it costs
# Rendering is always local and free. The only thing that costs anything is
# *writing* the content, and lesson JSON does not need writing.

LESSON_JSON = json.dumps({
    "title": "Authored",
    "sections": [{"name": "s", "explain": {"heading": "H",
                                           "columns": [["A", ["a"]]]},
                  "narration": ["One.", "Two.", "Three."]}],
})


def test_a_topic_leaves_local_mode_nothing_to_work_from():
    assert can_render_locally(SubmitRequest(topic="decorators")) is False


def test_local_mode_takes_prose():
    """It structures prose on this machine, which is the point: a script is
    what someone actually has to hand when the allowance is gone."""
    assert can_render_locally(SubmitRequest(script="Scene 1. A manager speaks."))


def test_local_mode_takes_lesson_json():
    assert can_render_locally(SubmitRequest(script=LESSON_JSON))


def test_local_mode_takes_a_lesson_path():
    assert can_render_locally(SubmitRequest(lesson="/tmp/lesson.json"))


def test_an_empty_script_leaves_nothing_to_work_from():
    assert can_render_locally(SubmitRequest(topic="decorators", script="   ")) is False


@requires_db
def test_local_mode_refuses_a_topic_instead_of_spending(client):
    """The whole promise is that nothing is spent, so it is refused before it
    is queued rather than queued and failed."""
    response = client.post("/jobs", headers=auth(), json={
        "topic": "decorators", "generation": "local"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "local_mode_needs_content"
    # A tag for the client to branch on, and a sentence for the person reading
    # it. Both, because either alone is useless to the other.
    assert "never calls a model" in detail["message"]
    # Nothing was queued, so nothing was counted against the allowance.
    assert client.get("/me/quota", headers=auth()).json()["used_today"] == 0


@requires_db
def test_local_mode_accepts_lesson_json(client):
    response = client.post("/jobs", headers=auth(), json={
        "script": LESSON_JSON, "generation": "local"})

    assert response.status_code == 202
    assert stored_options(response.json()["id"])["generation"] == "local"


@requires_db
def test_local_mode_accepts_pasted_prose(client):
    """The case the mode exists for: a script in hand, no allowance left."""
    response = client.post("/jobs", headers=auth(), json={
        "script": "Scene 1 — The Deadline: A manager wants this in two weeks.\n"
                  "Scene 2 — The Answer: Start from a model that knows language.",
        "generation": "local"})

    assert response.status_code == 202


@requires_db
def test_the_generation_preference_is_saved_and_used(client):
    client.put("/me/settings", headers=auth(), json={"generation": "local"})

    refused = client.post("/jobs", headers=auth(), json={"topic": "decorators"})
    assert refused.status_code == 422

    allowed = client.post("/jobs", headers=auth(), json={"script": LESSON_JSON})
    assert allowed.status_code == 202


# ------------------------------------------------------------- what is stored

def stored_options(job_id: str) -> dict:
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        row = conn.execute("select options from jobs where id = %s",
                           (job_id,)).fetchone()
    return row["options"]


@requires_db
def test_the_mode_and_script_ride_with_the_job(client):
    """They travel in options, so the worker needs no shared filesystem."""
    response = client.post("/jobs", headers=auth(), json={
        "script": "Scene 1. Someone explains a thing.", "mode": "illustration"})
    assert response.status_code == 202

    options = stored_options(response.json()["id"])
    assert options["mode"] == "illustration"
    assert options["script"].startswith("Scene 1.")


# ----------------------------------------------------------------- settings

@requires_db
def test_settings_round_trip(client):
    before = client.get("/me/settings", headers=auth()).json()
    assert before["mode"] == "both" and before["captions"] is True

    changed = {**before, "mode": "illustration", "style": "clean",
               "voice": "am_michael"}
    assert client.put("/me/settings", headers=auth(), json=changed).status_code == 200

    after = client.get("/me/settings", headers=auth()).json()
    assert (after["mode"], after["style"], after["voice"]) == \
        ("illustration", "clean", "am_michael")


@requires_db
def test_settings_are_per_user(client):
    client.put("/me/settings", headers=auth("alice"), json={"mode": "coding"})
    assert client.get("/me/settings", headers=auth("alice")).json()["mode"] == "coding"
    assert client.get("/me/settings", headers=auth("bob")).json()["mode"] == "both"


@requires_db
def test_an_unknown_setting_is_refused(client):
    response = client.put("/me/settings", headers=auth(),
                          json={"mode": "podcast"})
    assert response.status_code == 422


@requires_db
def test_saved_settings_reach_the_job(client):
    client.put("/me/settings", headers=auth(),
               json={"mode": "illustration", "style": "clean"})
    response = client.post("/jobs", headers=auth(), json={"topic": "decorators"})

    options = stored_options(response.json()["id"])
    assert options["mode"] == "illustration"
    assert options["style"] == "clean"


@requires_db
def test_a_request_mode_overrides_the_saved_one(client):
    """One job can differ from the saved default without changing it."""
    client.put("/me/settings", headers=auth(), json={"mode": "illustration"})
    response = client.post("/jobs", headers=auth(),
                           json={"topic": "decorators", "mode": "coding"})

    assert stored_options(response.json()["id"])["mode"] == "coding"
    assert client.get("/me/settings", headers=auth()).json()["mode"] == "illustration"


def test_settings_have_no_field_for_a_tier_key():
    """The tier-owned keys are not settable, so they cannot be overridden.

    They are merged over the top at submit time regardless; this is the second
    lock on the same door, and the cheap one.
    """
    smuggled = Settings(**{"tts": "elevenlabs", "max_snippets": 99,
                           "resolution": [3840, 2160], "watermark": False})
    saved = smuggled.model_dump()
    for key in ("tts", "max_snippets", "resolution", "watermark"):
        assert key not in saved


@requires_db
def test_a_job_reports_its_mode(client):
    """A library entry has to say what kind of video it is."""
    created = client.post("/jobs", headers=auth(),
                          json={"topic": "decorators", "mode": "illustration"})
    job_id = created.json()["id"]

    listed = client.get("/jobs", headers=auth()).json()
    assert next(j for j in listed if j["id"] == job_id)["mode"] == "illustration"
    assert client.get(f"/jobs/{job_id}", headers=auth()).json()["mode"] == "illustration"


@requires_db
def test_a_client_cannot_smuggle_a_tier_key(client):
    """Tier-owned keys are merged over whatever the client sent.

    SubmitRequest also has no extra=allow, so an unknown key never even reaches
    that merge. Between the two, a free-tier job cannot ask for the paid voice.
    """
    response = client.post("/jobs", headers=auth(tier="free"), json={
        "topic": "decorators", "tts": "elevenlabs", "resolution": [3840, 2160],
        "watermark": False})
    assert response.status_code == 202

    options = stored_options(response.json()["id"])
    assert options["tts"] == "kokoro"
    assert options["watermark"] is True
    assert list(options["resolution"]) == [1280, 720]


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
def test_the_job_row_carries_its_retry_state(client):
    """The page can only say "Retrying" instead of "Queued" -- and hold off
    reporting a failure -- if it is told how many attempts there are and when
    the next one is due."""
    body = client.post("/jobs", json={"topic": "a topic"}, headers=auth()).json()
    job = client.get(f"/jobs/{body['id']}", headers=auth()).json()

    assert job["attempt"] == 0, "a fresh job has spent no attempts"
    assert job["max_attempts"] > 1, (
        "one attempt is not a retry budget; the default is not reaching the row"
    )
    assert job["not_before"] is not None, "a job is claimable from the start"


def _fail(job_id: str, *, code: str = "provider_quota",
          message: str = "every model failed") -> None:
    """Stand in for the worker giving up on a job."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "update jobs set status='failed', attempt=max_attempts, "
            "error_code=%s, error_message=%s, finished_at=now() where id=%s",
            (code, message, job_id),
        )


@requires_db
def test_health_answers_to_both_spellings(client):
    """/healthz is what a load balancer probes by default, and a 404 there
    reads as a broken service rather than a wrong path."""
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/healthz").json() == {"ok": True}


@requires_db
def test_a_failed_job_can_be_retried_from_the_page(client):
    """A job that has given up is the one thing a user can see and do nothing
    about."""
    job_id = client.post("/jobs", json={"topic": "a topic"},
                         headers=auth()).json()["id"]
    _fail(job_id)

    r = client.post(f"/jobs/{job_id}/retry", headers=auth())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["attempt"] == 0, "a retry that keeps its spent attempts"
    assert body["error_code"] is None and body["error_message"] is None
    assert body["finished_at"] is None
    # And it is claimable straight away rather than waiting out a backoff.
    assert body["not_before"] is not None


@requires_db
def test_retrying_does_not_cost_an_allowance(client):
    """The row already exists and the work is the same work. Charging again
    would make recovering from a failure cost another of three daily jobs."""
    job_id = client.post("/jobs", json={"topic": "a topic"},
                         headers=auth()).json()["id"]
    before = client.get("/me/quota", headers=auth()).json()["used_today"]

    _fail(job_id)
    assert client.post(f"/jobs/{job_id}/retry",
                       headers=auth()).status_code == 202

    assert client.get("/me/quota", headers=auth()).json()["used_today"] == before


def test_stats_are_open_to_anyone(client):
    """It is the landing page's numbers, so it cannot require a login."""
    r = client.get("/stats")           # no auth header at all
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"tutorials_rendered", "minutes_of_video",
                             "topics_covered"}


@requires_db
def test_stats_count_what_actually_happened(client):
    """The point of measuring rather than asserting: these have to agree with
    the rows, and a landing page cannot inflate them."""
    job_id = client.post("/jobs", json={"topic": "a measured topic"},
                         headers=auth()).json()["id"]
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "update jobs set status='succeeded', duration_s=90 where id=%s",
            (job_id,))

    body = client.get("/stats").json()
    assert body["tutorials_rendered"] == 1
    assert body["minutes_of_video"] == 1.5
    assert body["topics_covered"] == 1


@requires_db
def test_stats_ignore_jobs_that_did_not_finish(client):
    """A failed or canceled render is not a tutorial, and its duration is a
    measurement of work nobody got."""
    job_id = client.post("/jobs", json={"topic": "a topic"},
                         headers=auth()).json()["id"]
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "update jobs set status='failed', duration_s=90 where id=%s", (job_id,))

    body = client.get("/stats").json()
    assert body["tutorials_rendered"] == 0
    assert body["minutes_of_video"] == 0


@requires_db
def test_a_running_job_can_be_canceled(client):
    job_id = client.post("/jobs", json={"topic": "a topic"},
                         headers=auth()).json()["id"]

    r = client.post(f"/jobs/{job_id}/cancel", headers=auth())
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "canceled"

    # The lease is left alone on purpose: the running worker matches on it to
    # read its own heartbeat, so a nulled lease would come back empty and read
    # as "reaped" rather than "canceled".
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        row = conn.execute("select * from jobs where id = %s", (job_id,)).fetchone()
    assert row["status"] == "canceled"
    assert row["finished_at"] is not None

    # And it does not come back: canceled is not in ACTIVE_STATUSES and is not
    # queued, so neither the claim query nor the reaper will pick it up.
    assert Queue(DSN).claim("w0") is None


@requires_db
def test_a_finished_job_cannot_be_canceled(client):
    """It has already produced a video; there is nothing left to stop."""
    job_id = client.post("/jobs", json={"topic": "a topic"},
                         headers=auth()).json()["id"]
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("update jobs set status='succeeded' where id=%s", (job_id,))

    r = client.post(f"/jobs/{job_id}/cancel", headers=auth())
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "not_cancelable"
    # Which state it is in, not just that it refused.
    assert r.json()["detail"]["status"] == "succeeded"


@requires_db
def test_canceling_twice_is_refused_the_second_time(client):
    job_id = client.post("/jobs", json={"topic": "a topic"},
                         headers=auth()).json()["id"]
    assert client.post(f"/jobs/{job_id}/cancel", headers=auth()).status_code == 202
    second = client.post(f"/jobs/{job_id}/cancel", headers=auth())
    assert second.status_code == 409
    assert second.json()["detail"]["status"] == "canceled"


@requires_db
def test_a_job_that_is_not_yours_cannot_be_canceled(client):
    job_id = client.post("/jobs", json={"topic": "mine"},
                         headers=auth("user-a")).json()["id"]
    r = client.post(f"/jobs/{job_id}/cancel", headers=auth("user-b"))
    assert r.status_code == 404


@requires_db
def test_only_a_failed_job_can_be_retried(client):
    job_id = client.post("/jobs", json={"topic": "a topic"},
                         headers=auth()).json()["id"]

    # Still queued, so there is nothing to retry -- and the refusal says so
    # rather than leaving the caller to guess what it is now.
    r = client.post(f"/jobs/{job_id}/retry", headers=auth())
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "not_failed"
    assert r.json()["detail"]["status"] == "queued"

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("update jobs set status='succeeded' where id=%s", (job_id,))
    r = client.post(f"/jobs/{job_id}/retry", headers=auth())
    assert r.status_code == 409, "a finished video was put back in the queue"


@requires_db
def test_a_job_that_is_not_yours_cannot_be_retried(client):
    """Same answer as a job that does not exist, so the route cannot be used
    to find out whose job ids are real."""
    job_id = client.post("/jobs", json={"topic": "mine"},
                         headers=auth("user-a")).json()["id"]
    _fail(job_id)

    r = client.post(f"/jobs/{job_id}/retry", headers=auth("user-b"))
    assert r.status_code == 404


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
def test_a_spent_allowance_still_leaves_local_mode_open(client):
    """The moment the allowance is gone is exactly when someone reaches for
    the mode that spends nothing. Counting a local render against that same
    allowance locks the door from the inside -- which is what it did.
    """
    for i in range(TIERS["free"].jobs_per_day):
        assert client.post("/jobs", json={"topic": f"topic {i}"},
                           headers=auth()).status_code == 202
    assert client.post("/jobs", json={"topic": "one too many"},
                       headers=auth()).status_code == 429

    def local():
        return client.post("/jobs", headers=auth(), json={
            "script": LESSON_JSON, "generation": "local"})

    # Still open, because it spends nothing.
    assert local().status_code == 202, "local mode was blocked by a spent allowance"

    # But not unbounded: it still renders on this machine, so it has a limit
    # of its own rather than none.
    for _ in range(TIERS["free"].local_jobs_per_day - 1):
        assert local().status_code == 202

    refused = local()
    assert refused.status_code == 429
    detail = refused.json()["detail"]
    assert detail["kind"] == "local", "the message must name which limit was hit"
    assert detail["limit"] == TIERS["free"].local_jobs_per_day


@requires_db
def test_the_two_allowances_are_counted_apart(client):
    """Local renders must not eat the allowance for jobs that cost money."""
    client.post("/jobs", headers=auth(), json={"topic": "a paid job"})
    for _ in range(3):
        client.post("/jobs", headers=auth(), json={
            "script": LESSON_JSON, "generation": "local"})

    body = client.get("/me/quota", headers=auth()).json()
    assert body["used_today"] == 1, "a local render was counted as a model job"
    assert body["remaining"] == TIERS["free"].jobs_per_day - 1
    assert body["local_used_today"] == 3
    assert body["local_remaining"] == TIERS["free"].local_jobs_per_day - 3


@requires_db
def test_an_old_job_with_no_generation_counts_as_a_model_job():
    """Jobs written before `generation` existed have no key to read. They were
    model jobs, and the two counts have to partition the table -- a row that
    falls into neither would be free work nobody could see."""
    user = "old-user"
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        conn.execute(
            "insert into jobs (user_id, topic, tier, options) "
            "values (%s, %s, %s, %s)",
            (user, "before generation existed", "free", Json({})),
        )
        conn.commit()
        assert used_today(conn, user) == 1
        assert used_today(conn, user, local=True) == 0


@requires_db
def test_nothing_on_the_submit_path_constructs_a_spending_client(client, monkeypatch):
    """Submission must not be able to cost money.

    Even a rejected request has to get as far as validating before it can be
    refused, so this asserts the whole path is free of the paid clients rather
    than trusting that the rejection happens first.
    """
    import videosynx.providers as providers

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


# ------------------------------------------------------------------- delivery

@pytest.fixture
def stored(tmp_path) -> tuple[TestClient, LocalStorage]:
    """An app backed by local storage, so the whole loop is exercisable."""
    from videosynx_api.app import create_app

    store = LocalStorage(tmp_path / "artifacts")
    with TestClient(create_app(DSN, storage=store)) as c:
        yield c, store


# ---------------------------------------------------------------- examples

def _real_mp4(path) -> bytes:
    """A video ffmpeg will actually open, for the tests that need a frame.

    Longer than the 12.5s `_succeed` records on the row, because the still is
    taken from a third of the way in and seeking past the end of a shorter
    file yields no frame at all -- a fixture that cannot produce a still would
    pass this test for the wrong reason. Never smaller than roughly 100px on a
    side either: the JPEG encoder refuses a frame below that.
    """
    import subprocess

    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=black:s=320x180:r=10:d=20", "-pix_fmt", "yuv420p",
         "-y", str(path)],
        check=True, timeout=60,
    )
    return path.read_bytes()


@requires_db
def test_examples_are_public_but_only_what_was_published(stored, monkeypatch):
    """The landing page is public, so this has to be -- and it must show only
    videos someone deliberately published. A user's video becoming public
    because it was recent would be a privacy bug dressed as a feature."""
    client, store = stored
    mine = client.post("/jobs", json={"topic": "published"},
                       headers=auth()).json()["id"]
    theirs = client.post("/jobs", json={"topic": "private"},
                         headers=auth()).json()["id"]
    _succeed(mine, store)
    _succeed(theirs, store)

    monkeypatch.setenv("VIDEOSYNX_EXAMPLES", mine)
    body = client.get("/examples").json()          # no auth header

    assert [e["title"] for e in body] == ["published"]
    assert body[0]["duration_s"] == 12.5
    assert body[0]["video_url"]


@requires_db
def test_an_example_gets_a_still_from_the_video(stored, monkeypatch):
    """Frame zero is a blank editor, so the still is taken from further in --
    this checks one is actually made rather than the card falling back."""
    client, store = stored
    job_id = client.post("/jobs", json={"topic": "has a poster"},
                         headers=auth()).json()["id"]
    _succeed(job_id, store, body=_real_mp4(store.root / "src.mp4"))

    monkeypatch.setenv("VIDEOSYNX_EXAMPLES", job_id)
    example = client.get("/examples").json()[0]

    assert example["poster_url"], "no still was made from a readable video"
    poster_key = video_key("free", job_id, ".poster.jpg")
    assert store.exists(poster_key)


@requires_db
def test_an_unreadable_video_loses_its_still_not_the_example(stored, monkeypatch):
    """A thumbnail is not worth failing a public page over."""
    client, store = stored
    job_id = client.post("/jobs", json={"topic": "broken bytes"},
                         headers=auth()).json()["id"]
    _succeed(job_id, store)                        # b"mp4 bytes", not a video

    monkeypatch.setenv("VIDEOSYNX_EXAMPLES", job_id)
    r = client.get("/examples")

    assert r.status_code == 200
    assert r.json()[0]["poster_url"] is None, "a still appeared from nowhere"
    assert r.json()[0]["video_url"], "the example itself was dropped"


@requires_db
def test_a_job_that_never_finished_is_not_published(stored, monkeypatch):
    """A queued or failed job has no video to show."""
    client, store = stored
    job_id = client.post("/jobs", json={"topic": "still going"},
                         headers=auth()).json()["id"]

    monkeypatch.setenv("VIDEOSYNX_EXAMPLES", f"{job_id},not-a-real-id")
    assert client.get("/examples").json() == []


def _succeed(job_id: str, store: LocalStorage, *, tier: str = "free",
             body: bytes = b"mp4 bytes") -> str:
    """Stand in for the worker: render, upload, mark the job succeeded."""
    key = video_key(tier, job_id)
    source = store.root / "tmp_render.mp4"
    source.write_bytes(body)
    store.put(key, source)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "update jobs set status='succeeded', artifact_key=%s, duration_s=12.5 "
            "where id=%s",
            (key, job_id),
        )
    return key


@requires_db
def test_the_script_endpoint_serves_the_plan_the_renderer_made(stored):
    """The generated code, narration and diagrams exist nowhere else.

    Nothing else keeps a record of what was generated, so a video that came
    out wrong cannot be diagnosed without this.
    """
    client, store = stored
    job_id = client.post("/jobs", headers=auth(),
                         json={"topic": "decorators"}).json()["id"]

    # Before a worker has run there is nothing saved, and the endpoint says so
    # rather than returning an empty object that looks like a real plan.
    early = client.get(f"/jobs/{job_id}/script", headers=auth()).json()
    assert early["note"] == "no plan was saved for this job"

    plan = {
        "topic": "decorators",
        "total_frames": 4242,
        "sections": [{
            "kind": "explain",
            "heading": "What a decorator is",
            "diagram": {"layout": "row", "nodes": [{"id": "a", "label": "Call"}]},
        }],
    }
    source = store.root / "plan.json"
    source.write_text(json.dumps(plan))
    store.put(video_key("free", job_id, ".script.json"), source)

    body = client.get(f"/jobs/{job_id}/script", headers=auth()).json()
    assert body["total_frames"] == 4242
    assert body["sections"][0]["heading"] == "What a decorator is"


@requires_db
def test_a_saved_plan_is_not_readable_by_another_account(stored):
    client, store = stored
    job_id = client.post("/jobs", headers=auth("owner"),
                         json={"topic": "decorators"}).json()["id"]
    source = store.root / "plan.json"
    source.write_text(json.dumps({"total_frames": 1}))
    store.put(video_key("free", job_id, ".script.json"), source)

    response = client.get(f"/jobs/{job_id}/script", headers=auth("stranger"))
    assert response.status_code == 404


@requires_db
def test_video_is_not_offered_until_the_job_succeeds(stored):
    client, _ = stored
    job_id = client.post("/jobs", json={"topic": "still queued"},
                         headers=auth()).json()["id"]
    r = client.get(f"/jobs/{job_id}/video", headers=auth())
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "not_ready"


@requires_db
def test_the_owner_can_fetch_the_video(stored):
    client, store = stored
    job_id = client.post("/jobs", json={"topic": "a finished job"},
                         headers=auth()).json()["id"]
    _succeed(job_id, store)

    body = client.get(f"/jobs/{job_id}/video", headers=auth()).json()
    assert body["duration_s"] == pytest.approx(12.5)
    fetched = client.get(body["url"], headers=auth())
    assert fetched.status_code == 200
    assert fetched.content == b"mp4 bytes"
    assert fetched.headers["content-type"] == "video/mp4"


@requires_db
def test_a_video_whose_file_has_gone_says_so_instead_of_404ing(stored):
    """The default store is a /tmp directory, and /tmp gets cleared.

    Signing a URL for a file that is not there hands the browser an address
    that answers 404, which reads as a broken page rather than as a video
    that is no longer kept. The job is still real, so it is a 410 with a
    reason, not a 404 saying the job does not exist.
    """
    client, store = stored
    job_id = client.post("/jobs", json={"topic": "a job that outlives its video"},
                         headers=auth()).json()["id"]
    key = _succeed(job_id, store)

    assert client.get(f"/jobs/{job_id}/video", headers=auth()).status_code == 200

    store.delete(key)

    r = client.get(f"/jobs/{job_id}/video", headers=auth())
    assert r.status_code == 410
    assert r.json()["detail"]["error"] == "artifact_missing"


@requires_db
def test_a_finished_job_reports_whether_its_file_is_still_there(stored):
    """The row has to know before it is clicked.

    Watch and Download are only offered while there is something to play, and
    the list is the only thing that can tell the page that.
    """
    client, store = stored
    job_id = client.post("/jobs", json={"topic": "listed before and after"},
                         headers=auth()).json()["id"]

    # Nothing finished yet, so there is no file to be missing.
    listed = next(j for j in client.get("/jobs", headers=auth()).json()
                  if j["id"] == job_id)
    assert listed["available"] is None

    key = _succeed(job_id, store)
    listed = next(j for j in client.get("/jobs", headers=auth()).json()
                  if j["id"] == job_id)
    assert listed["available"] is True
    assert client.get(f"/jobs/{job_id}", headers=auth()).json()["available"] is True

    store.delete(key)
    listed = next(j for j in client.get("/jobs", headers=auth()).json()
                  if j["id"] == job_id)
    assert listed["available"] is False


@requires_db
def test_the_video_url_is_signed_and_expires(stored):
    """Handing out a URL must not hand out the video forever.

    The link is authorised by its signature, not a header, because a <video>
    element cannot send one -- so the signature is the whole protection.
    """
    client, store = stored
    job_id = client.post("/jobs", json={"topic": "private"}, headers=auth()).json()["id"]
    _succeed(job_id, store)

    url = client.get(f"/jobs/{job_id}/video", headers=auth()).json()["url"]
    assert client.get(url).status_code == 200

    # Tampered: the signature no longer matches the key.
    forged = url.replace("sig=", "sig=0")
    assert client.get(forged).status_code == 403
    # Unsigned: nothing to verify against.
    assert client.get(url.split("?")[0]).status_code == 403
    # Expired.
    assert client.get(f"{url.split('?')[0]}?expires=1&sig=x").status_code == 403


@requires_db
def test_another_user_cannot_fetch_the_video(stored):
    client, store = stored
    job_id = client.post("/jobs", json={"topic": "mine"},
                         headers=auth("owner")).json()["id"]
    _succeed(job_id, store)
    url = client.get(f"/jobs/{job_id}/video", headers=auth("owner")).json()["url"]

    # The URL itself is not a capability to enumerate others with: it can
    # only be obtained from the owner's job.
    assert client.get(f"/jobs/{job_id}/video",
                      headers=auth("intruder")).status_code == 404


@requires_db
def test_the_artifact_route_is_closed_with_object_storage():
    """With R2 the client already has a presigned URL.

    The file route must not survive as an unauthenticated file server.
    """
    from videosynx_api.app import create_app

    class _ObjectStore:
        def url(self, key, *, expires_in=3600):
            return f"https://r2.example/{key}"

        def put(self, key, path): ...
        def delete(self, key): ...
        def exists(self, key): return True
        def read(self, key): return b"should never be served"

    with TestClient(create_app(DSN, storage=_ObjectStore())) as c:
        job_id = c.post("/jobs", json={"topic": "object stored"},
                        headers=auth()).json()["id"]
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(
                "update jobs set status='succeeded', artifact_key=%s where id=%s",
                (video_key("free", job_id), job_id),
            )
        assert c.get(f"/artifacts/artifacts/free/{job_id}.mp4",
                     headers=auth()).status_code == 404
        # ...but the presigned URL is still issued.
        assert c.get(f"/jobs/{job_id}/video", headers=auth()).json()["url"].startswith(
            "https://r2.example/"
        )


# --------------------------------------------------------------- the badges

def _given(conn, user_id: str, **cols) -> None:
    """A row, written straight in, because these are facts about history and
    history is not something a test should have to render."""
    fields = {"user_id": user_id, "topic": "t", "tier": "free", "status": "succeeded"}
    fields.update(cols)
    columns = ", ".join(fields)
    marks = ", ".join(["%s"] * len(fields))
    conn.execute(f"insert into jobs ({columns}) values ({marks})",
                 tuple(Json(v) if key == "options" else v
                       for key, v in fields.items()))


def test_a_voice_over_is_not_a_tutorial_rendered(client):
    """They share a queue and a row shape and are not the same thing.

    Counted together, "Prolific: twenty-five tutorials rendered" was reachable
    with twenty-five voice-overs, and the XP for them came with it. The clip
    has its own number and its own badge.
    """
    user = "badge-clips"
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as conn:
        for _ in range(5):
            _given(conn, user, status="succeeded",
                   options={"kind": "speech"}, duration_s=30)

    progress = client.get("/me/progress",
                          headers={"X-Dev-User": f"{user}:free"}).json()
    earned = {a["id"] for a in progress["achievements"] if a["earned"]}
    assert "voiced" in earned
    assert "first" not in earned, "a clip counted as a tutorial"
    assert progress["xp"] == 0, "a clip earned tutorial XP"


def test_the_badges_are_earned_by_the_work_that_names_them(client):
    """One row per badge, each asking for one different thing."""
    user = "badge-mixed"
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as conn:
        # A long video, made without a model, from a script of its own.
        _given(conn, user, duration_s=700,
               options={"script": "x", "generation": "local"})
        # One that took two tries.
        _given(conn, user, duration_s=10, attempt=2, options={})
        # And a failed one that should earn nothing at all.
        _given(conn, user, status="failed", duration_s=9999, attempt=9,
               options={"kind": "speech", "script": "x"})

    progress = client.get("/me/progress",
                          headers={"X-Dev-User": f"{user}:free"}).json()
    earned = {a["id"] for a in progress["achievements"] if a["earned"]}
    assert earned == {"first", "epic", "scripted", "local", "undeterred"}, earned
    # The failed row is enormous and must not have counted toward the hour.
    assert progress["xp"] == 2 * 100 + int(710 / 60) * 10


# ------------------------------------------------ structured lesson validation
# Lesson JSON is rendered exactly as written, so it is the one input where this
# side can tell, before anything is queued, whether the render will succeed.
# A broken lesson used to be accepted with a 202, counted against the day's
# allowance and then failed in the worker -- the caller paying a job for a
# message they could have had immediately.

def broken_lesson() -> str:
    """A lesson that starts right and is wrong further in: an explanation with
    nothing said over it."""
    return json.dumps({
        "title": "Broken",
        "sections": [{"name": "s", "explain": {"heading": "H"}, "narration": []}],
    })


def test_prose_is_not_mistaken_for_lesson_json():
    """Most scripts are prose, and prose is structured by a model downstream.
    Refusing it here would refuse the ordinary case."""
    assert submitted_lesson(SubmitRequest(script="Scene 1. A manager speaks.")) is None


def test_lesson_json_sent_as_text_is_recognised():
    lesson = submitted_lesson(SubmitRequest(script=LESSON_JSON))
    assert lesson is not None and lesson.title == "Authored"


def test_a_lesson_object_is_recognised():
    lesson = submitted_lesson(SubmitRequest(lesson=json.loads(LESSON_JSON)))
    assert lesson is not None and lesson.title == "Authored"


def test_a_lesson_with_no_sections_is_refused():
    with pytest.raises(ValueError, match="sections"):
        check_lesson(SubmitRequest(lesson={"title": "Nothing here"}))


def test_a_truncated_lesson_is_refused_rather_than_read_as_prose():
    """The case the startswith check exists for.

    A truncated object parses as nothing at all, so read as prose it would
    spend a model call restructuring a broken blob and then render something
    the caller never wrote.
    """
    with pytest.raises(ValueError, match="starts like lesson JSON"):
        check_lesson(SubmitRequest(script='{"title": "Half", "sections": ['))


def test_a_lesson_that_parses_but_is_unusable_names_the_problem():
    """Not "unrecognised". The caller wants to know which part is wrong, and
    parse_lesson already knows."""
    with pytest.raises(ValueError, match="narration"):
        check_lesson(SubmitRequest(script=broken_lesson()))


def test_prose_that_merely_contains_braces_is_still_prose():
    """It does not start with one, so it is not treated as a broken lesson."""
    check_lesson(SubmitRequest(script="Use a dict, like {'a': 1}, to map keys."))


def test_the_job_is_named_after_the_lesson_and_not_its_first_line():
    """Otherwise the library entry for lesson JSON is called `{`."""
    assert job_label(SubmitRequest(lesson=json.loads(LESSON_JSON))) == "Authored"
    assert job_label(SubmitRequest(script=LESSON_JSON)) == "Authored"


@requires_db
def test_a_broken_lesson_is_refused_before_it_is_counted(client):
    before = client.get("/me/quota", headers=auth()).json()["used_today"]

    response = client.post("/jobs", headers=auth(), json={"script": broken_lesson()})

    assert response.status_code == 422
    assert "narration" in response.json()["detail"]
    # Nothing was queued, so the allowance is untouched -- which is the whole
    # point of checking here rather than in the worker.
    assert client.get("/me/quota", headers=auth()).json()["used_today"] == before


@requires_db
def test_a_lesson_object_rides_with_the_job(client):
    response = client.post("/jobs", headers=auth(),
                           json={"lesson": json.loads(LESSON_JSON)})

    assert response.status_code == 202
    stored = stored_options(response.json()["id"])["lesson"]
    assert stored["title"] == "Authored"
    assert stored["sections"][0]["explain"]["columns"] == [["A", ["a"]]]


def test_the_lesson_example_the_error_points_at_is_a_lesson():
    """The 422 for a malformed lesson tells the caller to look at /llms.txt.

    An error message that names a document is making a promise about what is
    in it, and a worked example inside prose is the thing most likely to rot
    quietly -- nothing reads it, so nothing notices when the shape moves
    underneath it. This runs the one in llms.txt through the same parser a
    submitted request would go through, which is the only claim that matters:
    that an agent can copy it and have it work.
    """
    source = (Path(__file__).resolve().parents[2]
              / "web" / "app" / "llms.txt" / "route.ts").read_text()

    block = re.search(r'^    (\{\n      "title".*?\n    \})$', source, re.M | re.S)
    assert block, "the worked lesson example is gone from llms.txt"

    example = json.loads(block.group(1))
    check_lesson(SubmitRequest(lesson=example))
    lesson = submitted_lesson(SubmitRequest(lesson=example))
    assert lesson.title == "How a loop runs"
    # The arrows are the reason the section is there at all.
    assert lesson.sections[0].slide.diagram.edges
    assert lesson.sections[0].slide.pieces == 2
