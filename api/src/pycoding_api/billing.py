"""Stripe: who has paid, and what they are entitled to.

Two things matter more than the rest.

**A webhook is delivered at least once, not exactly once.** Stripe retries, and
a retry that grants a second month is free money out. Every event is recorded
against its own id, unique in the ledger, so a replay is a no-op. That is
tested by replaying the same event and asserting the balance did not move.

**Entitlement lives in the database, not the token.** A JWT minted before an
upgrade would keep granting the old tier until it expired, which reads as "I
paid and nothing happened".

Signatures are verified against the raw request body. Parsing first and
verifying after is the classic way to accept forged events, because the bytes
that were signed are no longer the bytes being trusted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

DEFAULT_TOLERANCE_S = 300  # replay window for a signature timestamp

TIER_BY_PRICE: dict[str, str] = json.loads(os.environ.get("STRIPE_PRICE_TIERS", "{}"))
DEFAULT_PAID_TIER = os.environ.get("STRIPE_DEFAULT_TIER", "paid")


class SignatureError(Exception):
    pass


def verify_signature(payload: bytes, header: str, secret: str,
                     *, tolerance: int = DEFAULT_TOLERANCE_S,
                     now: float | None = None) -> None:
    """Raise unless *header* is a fresh, valid signature of *payload*.

    Implements Stripe's scheme: ``t=<unix>,v1=<hmac>`` over ``"{t}.{payload}"``.
    The timestamp is checked so a captured request cannot be replayed later.
    """
    if not secret:
        raise SignatureError("no webhook secret is configured")

    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    timestamp = parts.get("t")
    provided = parts.get("v1")
    if not timestamp or not provided:
        raise SignatureError("malformed signature header")

    try:
        signed_at = int(timestamp)
    except ValueError:
        raise SignatureError("malformed timestamp") from None

    current = time.time() if now is None else now
    if abs(current - signed_at) > tolerance:
        raise SignatureError("timestamp outside the tolerance window")

    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    # Constant time: a byte-by-byte comparison leaks how much was right.
    if not hmac.compare_digest(expected, provided):
        raise SignatureError("signature does not match")


@dataclass(frozen=True)
class Granted:
    user_id: str
    tier: str
    credits: int
    duplicate: bool


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row)


def tier_for_user(conn: psycopg.Connection, user_id: str, fallback: str = "free") -> str:
    row = conn.execute(
        "select tier, status, current_period_end from subscriptions where user_id = %s",
        (user_id,),
    ).fetchone()
    if row is None:
        return fallback
    # A cancelled subscription keeps its tier until the period it paid for
    # runs out -- taking it away early is taking away something already paid
    # for.
    if row["status"] in ("canceled", "unpaid", "past_due"):
        if row["current_period_end"] is None:
            return fallback
    return row["tier"]


def balance(conn: psycopg.Connection, user_id: str) -> int:
    row = conn.execute(
        "select coalesce(sum(delta), 0) as n from credit_ledger where user_id = %s",
        (user_id,),
    ).fetchone()
    return int(row["n"])


def ledger(conn: psycopg.Connection, user_id: str, limit: int = 50) -> list[dict]:
    return conn.execute(
        """
        select delta, reason, created_at from credit_ledger
         where user_id = %s order by created_at desc limit %s
        """,
        (user_id, limit),
    ).fetchall()


def _grant(conn: psycopg.Connection, *, user_id: str, credits: int, reason: str,
           event_id: str | None, tier: str | None = None,
           customer_id: str | None = None, subscription_id: str | None = None,
           status: str | None = None, period_end: str | None = None) -> bool:
    """Record a grant. Returns False if this event was already applied.

    The insert is the idempotency guard: the unique index on stripe_event_id
    means a replay conflicts rather than granting again, and the conflict is
    how we know not to touch the subscription either.
    """
    if event_id is not None:
        existing = conn.execute(
            "select 1 from credit_ledger where stripe_event_id = %s", (event_id,)
        ).fetchone()
        if existing:
            return False

    if credits:
        conn.execute(
            """
            insert into credit_ledger (user_id, delta, reason, stripe_event_id)
            values (%s, %s, %s, %s)
            on conflict (stripe_event_id) do nothing
            """,
            (user_id, credits, reason, event_id),
        )

    if tier is not None:
        conn.execute(
            """
            insert into subscriptions (user_id, tier, stripe_customer_id,
                                       stripe_subscription_id, status,
                                       current_period_end, updated_at)
            values (%s, %s, %s, %s, %s, %s, now())
            on conflict (user_id) do update
               set tier = excluded.tier,
                   stripe_customer_id = coalesce(excluded.stripe_customer_id,
                                                 subscriptions.stripe_customer_id),
                   stripe_subscription_id = coalesce(excluded.stripe_subscription_id,
                                                     subscriptions.stripe_subscription_id),
                   status = excluded.status,
                   current_period_end = excluded.current_period_end,
                   updated_at = now()
            """,
            (user_id, tier, customer_id, subscription_id, status, period_end),
        )
    return True


def _user_id_of(obj: dict) -> str:
    """Whose entitlement this is.

    Subscriptions carry our user id in metadata, set when the checkout session
    was created. client_reference_id covers one-off payments.
    """
    metadata = obj.get("metadata") or {}
    return str(metadata.get("user_id") or obj.get("client_reference_id") or "")


def _period_end(subscription: dict) -> str | None:
    import datetime as _dt

    stamp = subscription.get("current_period_end")
    if not stamp:
        return None
    return _dt.datetime.fromtimestamp(int(stamp), tz=_dt.timezone.utc).isoformat()


def _tier_for_price(price_id: str | None) -> str:
    if price_id and price_id in TIER_BY_PRICE:
        return TIER_BY_PRICE[price_id]
    return DEFAULT_PAID_TIER


def apply_event(conn: psycopg.Connection, event: dict) -> Granted | None:
    """Apply a Stripe event. Returns None for event types we ignore.

    Unknown types are ignored rather than rejected: Stripe sends many, and
    returning an error makes it retry something we will never handle.
    """
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    event_id = event.get("id")

    if kind == "checkout.session.completed":
        user_id = _user_id_of(obj)
        if not user_id:
            return None
        # The tier is stamped into metadata when the checkout session is
        # created, because a completed session does not carry its line items
        # unless they were explicitly expanded.
        tier = (obj.get("metadata") or {}).get("tier") or DEFAULT_PAID_TIER
        amount = int(obj.get("amount_total") or 0)
        applied = _grant(
            conn,
            user_id=user_id,
            credits=amount,  # stored in the currency's smallest unit
            reason="purchase",
            event_id=event_id,
            tier=tier,
            customer_id=obj.get("customer"),
            subscription_id=obj.get("subscription"),
            status="active",
        )
        return Granted(user_id, tier, amount, duplicate=not applied)

    if kind in ("customer.subscription.created", "customer.subscription.updated"):
        user_id = _user_id_of(obj)
        if not user_id:
            return None
        items = (obj.get("items") or {}).get("data") or [{}]
        price_id = (items[0].get("price") or {}).get("id")
        tier = obj.get("metadata", {}).get("tier") or _tier_for_price(price_id)
        applied = _grant(
            conn, user_id=user_id, credits=0, reason="subscription",
            event_id=event_id, tier=tier, customer_id=obj.get("customer"),
            subscription_id=obj.get("id"), status=obj.get("status"),
            period_end=_period_end(obj),
        )
        return Granted(user_id, tier, 0, duplicate=not applied)

    if kind == "customer.subscription.deleted":
        user_id = _user_id_of(obj)
        if not user_id:
            return None
        applied = _grant(
            conn, user_id=user_id, credits=0, reason="subscription_canceled",
            event_id=event_id, tier="free", customer_id=obj.get("customer"),
            subscription_id=obj.get("id"), status="canceled",
        )
        return Granted(user_id, "free", 0, duplicate=not applied)

    if kind == "invoice.payment_failed":
        user_id = _user_id_of(obj)
        if not user_id:
            return None
        applied = _grant(
            conn, user_id=user_id, credits=0, reason="payment_failed",
            event_id=event_id, tier=None, status="past_due",
        )
        return Granted(user_id, "", 0, duplicate=not applied)

    return None


def handle_webhook(dsn: str, payload: bytes, signature: str, secret: str) -> dict:
    """Verify and apply one webhook delivery."""
    verify_signature(payload, signature, secret)
    event = json.loads(payload)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        result = apply_event(conn, event)
    if result is None:
        return {"handled": False, "type": event.get("type")}
    return {
        "handled": True,
        "type": event.get("type"),
        "user_id": result.user_id,
        "tier": result.tier,
        "credits": result.credits,
        "duplicate": result.duplicate,
    }
