"""Tier limits, enforced before anything is spent.

The check and the job insert are one transaction behind an advisory lock on
the user. Two concurrent submits from the same account must not both pass a
count that only has room for one -- the whole point is that the expensive work
never starts, so a quota that can be raced is not a quota.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json


@dataclass(frozen=True)
class Tier:
    name: str
    jobs_per_day: int
    max_snippets: int
    tts: str
    narration: bool
    watermark: bool
    resolution: tuple[int, int]


# Narration is the cost driver, and on ElevenLabs it is roughly $0.60 a video
# against ~$0.01 for the code. Free runs Kokoro, which costs nothing per
# character, so a free tier with narration is affordable -- it was only
# unaffordable while every voice was a paid API.
TIERS: dict[str, Tier] = {
    "free": Tier("free", jobs_per_day=3, max_snippets=4, tts="kokoro",
                 narration=True, watermark=True, resolution=(1280, 720)),
    "byok": Tier("byok", jobs_per_day=10, max_snippets=8, tts="auto",
                 narration=True, watermark=False, resolution=(1920, 1080)),
    "paid": Tier("paid", jobs_per_day=50, max_snippets=12, tts="elevenlabs",
                 narration=True, watermark=False, resolution=(1920, 1080)),
}

DEFAULT_TIER = "free"


def tier_for(name: str | None) -> Tier:
    return TIERS.get(name or DEFAULT_TIER, TIERS[DEFAULT_TIER])


class QuotaExceeded(Exception):
    def __init__(self, tier: Tier, used: int):
        self.tier = tier
        self.used = used
        super().__init__(
            f"{tier.name} tier allows {tier.jobs_per_day} jobs per day; "
            f"{used} already used"
        )


def used_today(conn: psycopg.Connection, user_id: str) -> int:
    row = conn.execute(
        """
        select count(*) as n from jobs
         where user_id = %s and created_at >= date_trunc('day', now())
        """,
        (user_id,),
    ).fetchone()
    return int(row["n"])


def submit_within_quota(
    dsn: str,
    *,
    user_id: str,
    topic: str,
    tier_name: str | None = None,
    options: dict | None = None,
    idempotency_key: str | None = None,
) -> tuple[str, Tier]:
    """Queue a job, or raise QuotaExceeded without creating one.

    Returns (job_id, tier). The tier is resolved here and stored on the row so
    a plan change mid-queue cannot alter what the job is allowed to spend.
    """
    if not topic or not topic.strip():
        raise ValueError("topic must not be empty")

    tier = tier_for(tier_name)
    merged = dict(options or {})
    # These are what the worker reads; a client cannot override them.
    merged.update({
        "tts": tier.tts,
        "max_snippets": tier.max_snippets,
        "resolution": list(tier.resolution),
        "watermark": tier.watermark,
    })

    with psycopg.connect(dsn, row_factory=dict_row) as conn:  # a transaction
        # Serialise submissions per user, so the count cannot be read twice
        # before either insert lands.
        conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (user_id,))

        if idempotency_key:
            existing = conn.execute(
                "select id from jobs where idempotency_key = %s",
                (idempotency_key,),
            ).fetchone()
            if existing:
                # A retried request must not consume quota a second time.
                return str(existing["id"]), tier

        used = used_today(conn, user_id)
        if used >= tier.jobs_per_day:
            raise QuotaExceeded(tier, used)

        row = conn.execute(
            """
            insert into jobs (user_id, topic, tier, options, idempotency_key)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (user_id, topic, tier.name, Json(merged), idempotency_key),
        ).fetchone()

    return str(row["id"]), tier
