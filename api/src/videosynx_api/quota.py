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
    local_jobs_per_day: int = 10


# Narration is the cost driver, and on ElevenLabs it is roughly $0.60 a video
# against ~$0.01 for the code. Free runs Kokoro, which costs nothing per
# character, so a free tier with narration is affordable -- it was only
# unaffordable while every voice was a paid API.
TIERS: dict[str, Tier] = {
    "free": Tier("free", jobs_per_day=3, max_snippets=4, tts="kokoro",
                 narration=True, watermark=True, resolution=(1280, 720),
                 local_jobs_per_day=10),
    "byok": Tier("byok", jobs_per_day=10, max_snippets=8, tts="auto",
                 narration=True, watermark=False, resolution=(1920, 1080),
                 local_jobs_per_day=30),
    "paid": Tier("paid", jobs_per_day=50, max_snippets=12, tts="elevenlabs",
                 narration=True, watermark=False, resolution=(1920, 1080),
                 local_jobs_per_day=100),
}

DEFAULT_TIER = "free"


def tier_for(name: str | None) -> Tier:
    return TIERS.get(name or DEFAULT_TIER, TIERS[DEFAULT_TIER])


class QuotaExceeded(Exception):
    def __init__(self, tier: Tier, used: int, *, local: bool = False):
        self.tier = tier
        self.used = used
        self.local = local
        super().__init__(
            f"{tier.name} tier allows {limit_for(tier, local)} "
            f"{'local renders' if local else 'jobs'} per day; {used} already used"
        )


def limit_for(tier: Tier, local: bool) -> int:
    return tier.local_jobs_per_day if local else tier.jobs_per_day


# IS_LOCAL / IS_MODEL are the two halves of the jobs table, and they have to
# partition it: every row matches exactly one, including rows written before
# `generation` existed. `coalesce` covers a null options column or a missing
# key, so an old job is counted as a model job -- which is what it was.
IS_LOCAL = "options->>'generation' = 'local'"
IS_MODEL = "coalesce(options->>'generation', 'auto') <> 'local'"


def used_today(conn: psycopg.Connection, user_id: str, *,
               local: bool = False) -> int:
    """Jobs started today, counting only one of the two kinds.

    They are counted apart because they are not the same thing to allow. A
    model job spends money, and the daily limit exists to bound that. A local
    job spends none -- and the moment the limit is reached is exactly when
    someone reaches for local mode, so counting it against the same number
    would lock the door from the inside.

    It is not free of *server* cost, though: it still renders on this machine.
    So it has a limit of its own rather than none.
    """
    clause = IS_LOCAL if local else IS_MODEL
    row = conn.execute(
        f"""
        select count(*) as n from jobs
         where user_id = %s and created_at >= date_trunc('day', now())
           and {clause}
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

        # Which limit applies is decided by what this job will spend, and
        # that is known here -- `generation` is resolved from the request and
        # the saved settings long before the job is queued.
        local = merged.get("generation") == "local"
        used = used_today(conn, user_id, local=local)
        if used >= limit_for(tier, local):
            raise QuotaExceeded(tier, used, local=local)

        row = conn.execute(
            """
            insert into jobs (user_id, topic, tier, options, idempotency_key)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (user_id, topic, tier.name, Json(merged), idempotency_key),
        ).fetchone()

    return str(row["id"]), tier
