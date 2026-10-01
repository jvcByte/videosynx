"""Billing: signatures, entitlement, and replay.

No Stripe account is needed. Stripe's webhook signature is an HMAC over
"{timestamp}.{payload}", so a valid one can be constructed here -- which is
what makes it possible to test the delivery-twice case, the one that grants a
second month for free if it is wrong.

Checkout itself needs a live Stripe key and is not exercised; everything after
it is.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from videosynx_api.app import create_app
from videosynx_api.billing import (
    SignatureError,
    apply_event,
    balance,
    tier_for_user,
    verify_signature,
)
from videosynx_worker.db import Queue

DSN = os.environ.get("VIDEOSYNX_TEST_DSN", "postgresql:///videosynx_test")
SECRET = "whsec_test_secret"


def _reachable() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _reachable(), reason=f"no Postgres at {DSN}. create it with: createdb videosynx_test"
)


@pytest.fixture(autouse=True)
def clean():
    if not _reachable():
        return
    Queue(DSN).install()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("truncate jobs, subscriptions, credit_ledger")


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    with TestClient(create_app(DSN)) as c:
        yield c


def sign(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    stamp = timestamp if timestamp is not None else int(time.time())
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + payload,
                      hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def event(kind: str, obj: dict, event_id: str = "evt_1") -> bytes:
    return json.dumps(
        {"id": event_id, "type": kind, "data": {"object": obj}}
    ).encode()


def post(client: TestClient, payload: bytes, signature: str | None = None):
    return client.post(
        "/billing/webhook",
        content=payload,
        headers={"stripe-signature": signature if signature is not None else sign(payload)},
    )


def subscription_row(user_id: str) -> dict | None:
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        return conn.execute(
            "select * from subscriptions where user_id = %s", (user_id,)
        ).fetchone()


# ----------------------------------------------------------------- signatures

def test_a_valid_signature_verifies():
    payload = b'{"hello":"world"}'
    verify_signature(payload, sign(payload), SECRET)


def test_a_wrong_secret_is_rejected():
    payload = b'{"hello":"world"}'
    with pytest.raises(SignatureError):
        verify_signature(payload, sign(payload, "not-the-secret"), SECRET)


def test_a_tampered_payload_is_rejected():
    """Verifying a re-serialised body would accept this."""
    signature = sign(b'{"amount":1}')
    with pytest.raises(SignatureError):
        verify_signature(b'{"amount":1000000}', signature, SECRET)


def test_a_stale_signature_is_rejected():
    """A captured request must not be replayable later."""
    payload = b'{"a":1}'
    old = sign(payload, timestamp=int(time.time()) - 3600)
    with pytest.raises(SignatureError):
        verify_signature(payload, old, SECRET)


def test_a_malformed_header_is_rejected():
    for header in ("", "garbage", "t=123", "v1=abc", "t=notanumber,v1=x"):
        with pytest.raises(SignatureError):
            verify_signature(b"{}", header, SECRET)


def test_no_configured_secret_refuses_everything():
    """Better to reject every webhook than to accept unsigned ones."""
    with pytest.raises(SignatureError):
        verify_signature(b"{}", "t=1,v1=x", "")


# ------------------------------------------------------------------- webhook

@requires_db
def test_a_credit_pack_grants_the_credits_it_advertised(client):
    """The number on the price list, not the number on the receipt.

    `amount_total` is in the currency's smallest unit, and granting it as
    credits meant the pack advertised as a hundred granted nine hundred --
    which the pricing change would have turned into fifteen hundred minutes
    for the price of a hundred. The pack size is written into the metadata
    when the session is created; this is the half that reads it.
    """
    payload = event("checkout.session.completed", {
        "id": "cs_1", "client_reference_id": "user-1",
        "metadata": {"user_id": "user-1", "credits": "100"},
        "amount_total": 1500, "customer": "cus_1",
    }, event_id="evt_checkout_1")

    body = post(client, payload).json()
    assert body["handled"] and body["credits"] == 100

    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        assert balance(conn, "user-1") == 100, "1500 cents is not 1500 minutes"


def test_a_subscription_grants_the_tier_and_no_credits(client):
    """A subscription buys a tier. The checkout that starts one carries no
    pack, so it must not mint credits out of its own price."""
    payload = event("checkout.session.completed", {
        "id": "cs_2", "client_reference_id": "user-1",
        "metadata": {"user_id": "user-1", "tier": "paid"},
        "amount_total": 1900, "customer": "cus_1", "subscription": "sub_1",
    }, event_id="evt_checkout_2")

    body = post(client, payload).json()
    assert body["handled"] and body["tier"] == "paid"
    assert body["credits"] == 0

    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        assert tier_for_user(conn, "user-1") == "paid"
        assert balance(conn, "user-1") == 0


@requires_db
def test_the_same_event_delivered_twice_grants_once(client):
    """Stripe delivers at least once. A second grant is free money out."""
    payload = event("checkout.session.completed", {
        "id": "cs_1", "client_reference_id": "user-1",
        "metadata": {"user_id": "user-1", "credits": "500"},
        "amount_total": 6500, "customer": "cus_1",
    }, event_id="evt_replayed")

    first = post(client, payload).json()
    second = post(client, payload).json()

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        assert balance(conn, "user-1") == 500, "the replay granted again"


@requires_db
def test_an_invalid_signature_never_reaches_the_database(client):
    payload = event("checkout.session.completed", {
        "metadata": {"user_id": "attacker", "tier": "paid"}, "amount_total": 99999,
    })
    r = post(client, payload, signature=sign(payload, "wrong-secret"))
    assert r.status_code == 400
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        assert tier_for_user(conn, "attacker") == "free"
        assert balance(conn, "attacker") == 0


@requires_db
def test_an_unsigned_request_is_rejected(client):
    payload = event("checkout.session.completed", {"metadata": {"user_id": "x"}})
    assert client.post("/billing/webhook", content=payload).status_code == 400


@requires_db
def test_subscription_lifecycle_moves_the_tier(client):
    user = "user-2"
    post(client, event("customer.subscription.created", {
        "id": "sub_9", "customer": "cus_9", "status": "active",
        "metadata": {"user_id": user, "tier": "paid"},
        "current_period_end": int(time.time()) + 30 * 86400,
    }, event_id="evt_sub_1"))
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        assert tier_for_user(conn, user) == "paid"

    post(client, event("customer.subscription.deleted", {
        "id": "sub_9", "customer": "cus_9", "metadata": {"user_id": user},
    }, event_id="evt_sub_2"))
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        assert tier_for_user(conn, user) == "free"


@requires_db
def test_an_unknown_event_is_ignored_not_rejected(client):
    """Returning an error would make Stripe retry something we never handle."""
    r = post(client, event("customer.discount.created", {"id": "di_1"},
                           event_id="evt_ignored"))
    assert r.status_code == 200
    assert r.json()["handled"] is False


@requires_db
def test_an_event_without_a_user_is_ignored(client):
    r = post(client, event("checkout.session.completed", {"id": "cs_x"},
                           event_id="evt_nouser"))
    assert r.status_code == 200 and r.json()["handled"] is False


# -------------------------------------------------------- entitlement source

@requires_db
def test_the_tier_comes_from_the_database_not_the_token(client):
    """A token minted before an upgrade must not keep granting the old tier."""
    # The token still says "free"; the database says otherwise.
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "insert into subscriptions (user_id, tier, status) values (%s,%s,%s)",
            ("user-3", "paid", "active"),
        )
    quota = client.get("/me/quota", headers={"X-Dev-User": "user-3:free"}).json()
    assert quota["tier"] == "paid"
    assert quota["tts"] == "elevenlabs"


@requires_db
def test_a_paid_account_can_submit_more_before_the_cap(client):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "insert into subscriptions (user_id, tier, status) values (%s,%s,%s)",
            ("user-4", "paid", "active"),
        )
    r = client.post("/jobs", json={"topic": "a paid job"},
                    headers={"X-Dev-User": "user-4:free"})
    assert r.status_code == 202
    assert r.json()["tier"] == "paid"


@requires_db
def test_subscription_endpoint_reports_credits_and_history(client):
    post(client, event("checkout.session.completed", {
        "metadata": {"user_id": "user-5", "credits": "100"},
        "amount_total": 1500, "client_reference_id": "user-5",
    }, event_id="evt_hist"))
    body = client.get("/me/subscription", headers={"X-Dev-User": "user-5:free"}).json()
    assert body["tier"] == "paid"
    assert body["credits"] == 100
    assert body["recent"][0]["reason"] == "purchase"


@requires_db
def test_checkout_is_unavailable_without_stripe_configured(client, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    r = client.post("/billing/checkout", headers={"X-Dev-User": "user-6:free"})
    assert r.status_code == 503


@requires_db
def test_checkout_requires_a_token(client):
    assert client.post("/billing/checkout").status_code == 401


# ------------------------------------------------------------- ledger directly

@requires_db
def test_apply_event_is_idempotent_at_the_ledger():
    payload = json.loads(event("checkout.session.completed", {
        "metadata": {"user_id": "u", "credits": "500"}, "amount_total": 6500,
    }, event_id="evt_direct"))
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        first = apply_event(conn, payload)
        second = apply_event(conn, payload)
        assert first.duplicate is False
        assert second.duplicate is True
        assert balance(conn, "u") == 500
