"""Text to speech, as the API sees it.

The job is queued rather than answered, and the reason is a measurement:
synthesis runs at about 0.8x realtime, so a five-minute clip is four minutes
of CPU. What matters here is that it is queued as the *right kind* of job --
one that spends no model call, and therefore draws on the local renders
rather than on the allowance that exists to bound spending.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from videosynx_api.app import create_app, label_for_speech

DSN = "postgresql:///videosynx_test"


@pytest.fixture
def client():
    with TestClient(create_app(DSN)) as c:
        yield c


@pytest.fixture
def auth() -> dict[str, str]:
    """A fresh account per test.

    Speech draws on the local allowance, and there are ten of those a day --
    so a fixed user means the eleventh test in a day, or the second run of
    this file, starts with nothing left and fails for a reason that has
    nothing to do with what it is testing. Its own account is its own
    allowance.
    """
    return {"X-Dev-User": f"speaker-{uuid.uuid4().hex[:8]}:free"}


def test_a_clip_is_queued_not_answered(client, auth):
    response = client.post("/speech", headers=auth,
                           json={"text": "The first sentence. And a second one."})
    assert response.status_code == 202, response.text
    assert response.json()["id"]


def test_it_is_a_local_job_and_carries_no_model_call(client, auth):
    """The whole point of the allowance split: this must not draw on the
    three-a-day that exists to stop spending."""
    job_id = client.post("/speech", headers=auth,
                         json={"text": "Say this."}).json()["id"]
    before = client.get("/me/quota", headers=auth).json()
    assert before["remaining"] == before["jobs_per_day"]

    rows = client.get("/jobs", headers=auth).json()
    row = next(r for r in rows if r["id"] == job_id)
    assert row["status"] == "queued"

    after = client.get("/me/quota", headers=auth).json()
    assert after["remaining"] == after["jobs_per_day"], "it spent a model job"
    assert after["local_remaining"] == after["local_jobs_per_day"] - 1, \
        "it did not draw on the local renders"


def test_the_worker_can_tell_it_apart_from_a_video(client, auth):
    from videosynx_worker.db import Queue

    job_id = client.post("/speech", headers=auth,
                         json={"text": "Mark me.", "voice": "af_heart"}).json()["id"]
    job = Queue(DSN).get(job_id)
    assert job["options"]["kind"] == "speech"
    assert job["options"]["generation"] == "local"
    assert job["options"]["text"] == "Mark me."
    assert job["options"]["voice"] == "af_heart"


def test_the_row_is_named_from_what_it_says(client, auth):
    job_id = client.post("/speech", headers=auth, json={
        "text": "A variable is a name you give to a value. Python does the rest."
    }).json()["id"]
    rows = client.get("/jobs", headers=auth).json()
    row = next(r for r in rows if r["id"] == job_id)
    # The first sentence, not the opening hundred and twenty characters of a
    # paragraph -- a row you cannot tell from the next one is not a label.
    assert row["topic"] == "A variable is a name you give to a value."


def test_saying_nothing_is_refused(client, auth):
    assert client.post("/speech", headers=auth, json={"text": ""}).status_code == 422


def test_the_waitlist_is_not_the_only_thing_behind_a_sign_in(client):
    assert client.post("/speech", json={"text": "hello"}).status_code == 401


# --------------------------------------------------------------- the label

def test_labels_take_the_first_line_that_says_something():
    assert label_for_speech("# Chapter 1\n\nBody.") == "Chapter 1"
    assert label_for_speech("   \n\n") == "untitled"
    assert label_for_speech("One. Two. Three.") == "One."
    assert len(label_for_speech("word " * 200)) <= 120


def test_the_saved_pace_is_carried_onto_the_job(client, auth):
    """It is the account's, not the request's: a video and a clip made from
    the same settings should read at the same speed."""
    client.put("/me/settings", headers=auth, json={"pace": "fast"})
    job_id = client.post("/speech", headers=auth, json={"text": "Say it."}).json()["id"]

    from videosynx_worker.db import Queue

    assert Queue(DSN).get(job_id)["options"]["pace"] == "fast"


def test_the_default_pace_is_the_middle_one(client, auth):
    """Not the fast one, which sounds like the voice wants the video over
    with, and not the slow one, which is slow enough to notice."""
    from videosynx_api.settings import Settings

    assert Settings().pace == "normal"
    assert client.get("/me/settings", headers=auth).json()["pace"] == "normal"

    # And it reaches the job without anyone choosing it, which is the part
    # that would break quietly: the default is only worth anything if a
    # submission that never mentions pace still carries it.
    from videosynx_worker.db import Queue

    job_id = client.post("/speech", headers=auth, json={"text": "Say it."}).json()["id"]
    assert Queue(DSN).get(job_id)["options"]["pace"] == "normal"
