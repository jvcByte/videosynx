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
from videosynx_api.quota import CREDIT_PACKS, CREDITS_PER_JOB, TIERS
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
    assert body["credits_per_job"] == CREDITS_PER_JOB


# --------------------------------------------------------------- the spending

def test_a_job_past_the_allowance_costs_a_credit(client, auth):
    """The whole point of a wallet. Past the day's three, a credit is what
    one more costs -- and it comes off the balance in the same transaction
    that creates the job."""
    user = user_of(auth)
    grant(user, 5)

    for _ in range(3):
        assert client.post("/jobs", headers=auth, json={"topic": "within"}).status_code == 202
    assert client.get("/me/subscription", headers=auth).json()["credits"] == 5

    over = client.post("/jobs", headers=auth, json={"topic": "past the cap"})
    assert over.status_code == 202
    assert client.get("/me/subscription", headers=auth).json()["credits"] == 5 - CREDITS_PER_JOB


def test_a_credit_is_recorded_in_the_ledger_not_just_subtracted(client, auth):
    user = user_of(auth)
    grant(user, 2)
    for _ in range(3):
        client.post("/jobs", headers=auth, json={"topic": "within"})
    job_id = client.post("/jobs", headers=auth, json={"topic": "charged"}).json()["id"]

    with psycopg.connect(DSN, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute(
            "select delta, reason from credit_ledger "
            " where user_id = %s and reason = %s",
            (user, f"job:{job_id}"),
        ).fetchone()
    assert row is not None, "the charge is not in the ledger"
    assert row["delta"] == -CREDITS_PER_JOB


def test_with_no_credits_the_allowance_still_holds(client, auth):
    """The refusal has to survive: a balance of zero is a limit, not a
    suggestion."""
    for _ in range(3):
        client.post("/jobs", headers=auth, json={"topic": "within"})
    refused = client.post("/jobs", headers=auth, json={"topic": "one too many"})
    assert refused.status_code == 429


def test_one_credit_is_not_half_a_job(client, auth):
    """A credit is a whole job or it is nothing. Charging a fraction and
    refusing anyway would be taking money for a refusal."""
    grant(user_of(auth), 1)
    for _ in range(3):
        client.post("/jobs", headers=auth, json={"topic": "within"})

    assert client.post("/jobs", headers=auth, json={"topic": "the last one"}).status_code == 202
    assert client.get("/me/subscription", headers=auth).json()["credits"] == 0
    assert client.post("/jobs", headers=auth, json={"topic": "and one more"}).status_code == 429


# ---------------------------------------------------------------- the buying

def test_buying_credits_says_so_when_it_is_not_configured(client, auth, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    response = client.post("/billing/credits", headers=auth, json={"credits": 100})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_a_pack_size_nobody_sells_is_refused(client, auth, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    monkeypatch.setenv("STRIPE_CREDIT_PRICES", '{"100": "price_x"}')
    response = client.post("/billing/credits", headers=auth, json={"credits": 7})
    assert response.status_code == 503, "an unlisted size must not be guessed at"


def test_buying_needs_an_account(client):
    assert client.post("/billing/credits", json={"credits": 100}).status_code == 401
