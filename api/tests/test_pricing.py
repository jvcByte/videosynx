"""What things cost, and the credit that buys one.

Two properties. The price a visitor reads and the allowance a job gets come
from the same place, so a pricing page cannot drift from what the service
actually does. And a credit is spendable -- a balance nothing decrements is
decoration wearing the clothes of money.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from videosynx_api.app import create_app
from videosynx_api.quota import (CREDIT_PACKS, CREDITS_PER_MINUTE,
                                 TIERS, estimate_minutes)
from videosynx_api.settings import KOKORO_VOICES, VOICE_LANGUAGES

DSN = "postgresql:///videosynx_test"


@pytest.fixture
def client():
    with TestClient(create_app(DSN)) as c:
        yield c


@pytest.fixture
def auth() -> dict[str, str]:
    """A fresh account, with its own allowance and its own balance."""
    return {"X-Dev-User": f"pricing-{uuid.uuid4().hex[:8]}:free"}


def grant(user_id: str, credits: int) -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "insert into credit_ledger (user_id, delta, reason) values (%s, %s, %s)",
            (user_id, credits, "test"),
        )


def user_of(headers: dict[str, str]) -> str:
    return headers["X-Dev-User"].split(":")[0]


# ------------------------------------------------------------------ the page

def test_the_prices_are_public_and_come_from_the_tiers(client):
    body = client.get("/plans").json()
    assert {p["id"] for p in body["plans"]} == set(TIERS)
    for plan in body["plans"]:
        assert plan["price_usd"] == TIERS[plan["id"]].price_usd
        # The limits shown are the limits enforced, not a second copy.
        assert plan["jobs_per_day"] == TIERS[plan["id"]].jobs_per_day
        assert plan["features"] and plan["tagline"]


def test_the_pricing_sells_the_video_not_the_parts():
    """What a plan lists is what a visitor gets, not what it is built from.

    The voice engine and the model are suppliers. Naming one on a price tag
    prices the shopping list rather than the meal, it means nothing to most
    of the people reading it, and it dates the page the day it is swapped.
    """
    tools = ("kokoro", "elevenlabs", "byok", "ffmpeg", "gemini", "pillow")
    for tier in TIERS.values():
        copy = " ".join((tier.tagline, *tier.features)).lower()
        for tool in tools:
            assert tool not in copy, f"the {tier.name} plan sells {tool}"


def test_the_voice_count_on_the_price_list_is_the_real_one():
    """A visitor can count it in the picker, so it had better be right.

    Written out rather than derived: a plan is a tuple of strings, and a tier
    that counted voices to describe itself would have to reach into the
    engine's catalogue to do it. This is what keeps the two honest instead.
    """
    languages = {VOICE_LANGUAGES[name[:2]][0] for name in KOKORO_VOICES}
    assert f"{len(KOKORO_VOICES)} voices, {len(languages)} languages" \
        in TIERS["free"].features


def test_two_tiers_do_not_promise_the_same_narration():
    """`Natural` and `Premium` have to be two different things, because one
    of them is what the upgrade is for."""
    assert TIERS["free"].tts != TIERS["paid"].tts


def test_the_credit_packs_are_public(client):
    body = client.get("/plans").json()
    assert [p["credits"] for p in body["credit_packs"]] == [p["credits"] for p in CREDIT_PACKS]
    assert body["credits_per_minute"] == CREDITS_PER_MINUTE


# --------------------------------------------------------------- the spending

def test_submitting_past_the_allowance_does_not_spend_anything_yet(client, auth):
    """The charge moved to the finish, so the balance must be untouched here.

    Nothing knows how long a lesson is until it has been written -- measured
    over the renders in this database, a topic produced anything from one
    second to thirteen minutes -- so a job cannot be priced when it is asked
    for. What submitting decides is whether it may run at all.
    """
    user = user_of(auth)
    grant(user, 5)

    for _ in range(3):
        assert client.post("/jobs", headers=auth, json={"topic": "within"}).status_code == 202
    assert client.get("/me/subscription", headers=auth).json()["credits"] == 5

    over = client.post("/jobs", headers=auth, json={"topic": "past the cap"})
    assert over.status_code == 202
    assert client.get("/me/subscription", headers=auth).json()["credits"] == 5

    from videosynx_worker.db import Queue
    options = Queue(DSN).get(over.json()["id"])["options"]
    assert options["credits_per_minute"] == CREDITS_PER_MINUTE, \
        "the job carries the price the worker will charge"


def test_the_ledger_is_only_written_when_the_video_finishes(client, auth):
    """Submitting alone writes nothing: the charge belongs to the render.

    Where the money actually moves is `worker/tests/test_queue.py`, next to
    `Queue.finish`, because that is the statement it happens in. This is the
    API half of the same fact -- a job that was merely asked for has cost
    nothing yet.
    """
    user = user_of(auth)
    grant(user, 2)
    for _ in range(3):
        client.post("/jobs", headers=auth, json={"topic": "within"})
    job_id = client.post("/jobs", headers=auth, json={"topic": "priced"}).json()["id"]

    with psycopg.connect(DSN, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute(
            "select delta from credit_ledger where user_id = %s and reason = %s",
            (user, f"job:{job_id}"),
        ).fetchone()
    assert row is None, "a job was charged before it produced anything"
    assert client.get("/me/subscription", headers=auth).json()["credits"] == 2


def test_with_no_credits_the_allowance_still_holds(client, auth):
    """The refusal has to survive: a balance of zero is a limit, not a
    suggestion."""
    for _ in range(3):
        client.post("/jobs", headers=auth, json={"topic": "within"})
    refused = client.post("/jobs", headers=auth, json={"topic": "one too many"})
    assert refused.status_code == 429


def test_a_balance_is_spent_once_per_video_already_started(client, auth):
    """One credit is one video in flight, not one video forever.

    This is what stops a single credit queueing ten renders and leaving the
    account wherever ten of them put it: nothing is charged until a job ends,
    so the balance on its own says nothing about how much of it is already
    spoken for.
    """
    grant(user_of(auth), 1)
    for _ in range(3):
        client.post("/jobs", headers=auth, json={"topic": "within"})

    assert client.post("/jobs", headers=auth, json={"topic": "the one it pays for"}).status_code == 202
    # The credit is still in the balance -- it is spent at the finish -- and
    # the job holding it is what refuses the next one.
    assert client.get("/me/subscription", headers=auth).json()["credits"] == 1
    assert client.post("/jobs", headers=auth, json={"topic": "and one more"}).status_code == 429

    # Settle it the way the worker would, and the credit is free to be spent.
    from videosynx_worker.db import Queue
    queue = Queue(DSN)
    job_id = client.post("/jobs", headers=auth, json={"topic": "after"})
    assert job_id.status_code == 429


def test_a_script_longer_than_the_balance_is_refused_before_it_starts(client, auth):
    """A script is the one input whose length is knowable up front, because
    its narration is the text itself. Four credits of balance and forty
    minutes of script is a refusal, not a debt."""
    grant(user_of(auth), 4)
    for _ in range(3):
        client.post("/jobs", headers=auth, json={"topic": "within"})

    long_script = "word " * 8000  # ~40,000 characters, about 47 minutes
    assert estimate_minutes(long_script) > 4
    refused = client.post("/jobs", headers=auth, json={"script": long_script})
    assert refused.status_code == 429

    # A short one on the same balance goes through.
    assert client.post("/jobs", headers=auth,
                       json={"script": "A short one. " * 20}).status_code == 202


# ---------------------------------------------------------------- the buying

def test_an_unconfigured_server_does_not_name_its_variables_to_a_buyer(
        client, auth, monkeypatch, caplog):
    """The variable names are vital -- to the operator, in the log.

    They were in the response body, where the reader is a browser: a person
    who cannot set STRIPE_CREDIT_PRICES was being told to, which is our
    shopping list printed on their screen.
    """
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_CREDIT_PRICES", raising=False)

    with caplog.at_level("WARNING", logger="videosynx.api"):
        response = client.post("/billing/credits", headers=auth,
                               json={"credits": 100})

    assert response.status_code == 503
    body = response.json()["detail"]
    for leaked in ("STRIPE", "SECRET_KEY", "PRICE"):
        assert leaked not in body, f"the response names {leaked}"

    # And the operator still gets it.
    assert "STRIPE_SECRET_KEY" in caplog.text
    assert "STRIPE_CREDIT_PRICES" in caplog.text


def test_unconfigured_billing_does_not_name_its_variables_either(
        client, auth, monkeypatch, caplog):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)

    with caplog.at_level("WARNING", logger="videosynx.api"):
        response = client.post("/billing/checkout", headers=auth)

    assert response.status_code == 503
    assert "STRIPE" not in response.json()["detail"]
    assert "STRIPE_PRICE_ID" in caplog.text


def test_a_pack_size_nobody_sells_is_refused(client, auth, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    monkeypatch.setenv("STRIPE_CREDIT_PRICES", '{"100": "price_x"}')
    response = client.post("/billing/credits", headers=auth, json={"credits": 7})
    assert response.status_code == 503, "an unlisted size must not be guessed at"


def test_buying_needs_an_account(client):
    assert client.post("/billing/credits", json={"credits": 100}).status_code == 401
