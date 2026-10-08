"""Tier limits, enforced before anything is spent.

The check and the job insert are one transaction behind an advisory lock on
the user. Two concurrent submits from the same account must not both pass a
count that only has room for one -- the whole point is that the expensive work
never starts, so a quota that can be raced is not a quota.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

# For the credit balance. billing.py imports nothing from here, so there is no
# cycle -- the tiers are the only thing both know. The ledger *write* for a job
# is no longer here: it moved to the worker, which is the only thing that knows
# how long the video turned out to be.
from .billing import balance


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
    # What it costs, and what to say about it. Here rather than on a pricing
    # page, so the number a visitor reads and the number a job is allowed
    # against can only be edited in one place.
    price_usd: int = 0
    tagline: str = ""
    # What a visitor is sold, which is the video and the narration -- never
    # the parts they are assembled from. Naming the voice engine or the model
    # on a pricing page prices the shopping list rather than the meal, and it
    # dates the moment either one is swapped out.
    features: tuple[str, ...] = ()


# Narration is the cost driver, and on ElevenLabs it is roughly $0.60 a video
# against ~$0.01 for the code. Free runs Kokoro, which costs nothing per
# character, so a free tier with narration is affordable -- it was only
# unaffordable while every voice was a paid API.
TIERS: dict[str, Tier] = {
    "free": Tier(
        "free", jobs_per_day=3, max_snippets=4, tts="kokoro",
        narration=True, watermark=True, resolution=(1280, 720),
        local_jobs_per_day=10, price_usd=0,
        tagline="Everything you need to make one.",
        features=("3 videos a day", "720p, watermarked",
                  "Natural narration", "54 voices, 9 languages",
                  "10 local renders a day"),
    ),
    "byok": Tier(
        "byok", jobs_per_day=10, max_snippets=8, tts="auto",
        narration=True, watermark=False, resolution=(1920, 1080),
        local_jobs_per_day=30, price_usd=7,
        tagline="More of it, and no watermark.",
        # "Bring your own API key" was here. It was never true: there is no
        # field to accept a key, nowhere to keep one, and nothing that reads
        # one -- the tier was named after a capability that did not exist, and
        # the price page promised it to buyers for as long as it took anyone
        # to look. It is not coming back until there is a place to put a key
        # and somewhere safe to keep it, which is a decision about storing
        # credentials rather than a line of copy. Until then this plan sells
        # only what it does.
        features=("10 videos a day", "1080p, no watermark",
                  "Longer videos", "30 local renders a day"),
    ),
    "paid": Tier(
        "paid", jobs_per_day=50, max_snippets=12, tts="elevenlabs",
        narration=True, watermark=False, resolution=(1920, 1080),
        local_jobs_per_day=100, price_usd=19,
        tagline="For making them properly.",
        features=("50 videos a day", "1080p, no watermark",
                  "Premium narration", "Priority in the queue",
                  "100 local renders a day"),
    ),
}

# Sold on top of a plan. A credit is a minute of video, so a pack is a number
# of minutes -- which is the thing a buyer can actually count, and the only
# unit the service produces.
#
# Priced at $0.15 and $0.13 a minute against the roughly $0.12 a minute an
# ElevenLabs render costs. At the old $9/100 a minute of the Studio tier was
# sold below what it cost to make.
CREDIT_PACKS: tuple[dict, ...] = (
    {"credits": 100, "usd": 15, "note": "A hundred minutes, whenever."},
    {"credits": 500, "usd": 65, "note": "Cheaper by the minute, if you go through them."},
)

DEFAULT_TIER = "free"

# What a minute of video costs once the day's allowance is spent.
#
# Charged when the render finishes rather than when it is asked for, because
# nothing knows how long a lesson is until it has been written: measured over
# the renders in the database, a topic produced anything from one second to
# thirteen minutes. `submit_within_quota` decides whether a job may run;
# `Queue.finish` decides what it cost.
CREDITS_PER_MINUTE = 1

# Characters a minute of narration covers, for pricing a job whose input is
# text we already have.
#
# From the measured rate for the calm pace: about 157 words a minute at about
# 5.5 characters a word. A script's narration is the script, so this is a real
# estimate rather than a guess -- which is exactly what it is not when the
# input is a bare topic, and why the exact check lives in the worker instead.
CHARS_PER_MINUTE = 860


def estimate_minutes(text: str) -> int:
    """Minutes of narration *text* is likely to become, rounded up."""
    return max(1, ceil(len(text) / CHARS_PER_MINUTE))


def unsettled(conn: psycopg.Connection, user_id: str) -> int:
    """Jobs this account has started and not yet been billed for.

    Past the allowance, a credit is what a job costs -- but it is taken when
    the job finishes, so a balance on its own does not say how much of it is
    already spoken for. Without this, one credit submits ten videos: every
    submission sees the same balance, every render finishes, and the account
    lands wherever ten of them put it.

    `finished_at is null` rather than a list of statuses. It is set by a
    finished job, by a failure that will not be retried, and by the reaper on
    both of its paths, and it is cleared again by `POST /jobs/{id}/retry` -- so
    a retried job is correctly spoken for twice over, and no fourth copy of the
    job-status list has to be kept in step with the three that exist.
    """
    row = conn.execute(
        """
        select count(*) as n from jobs
         where user_id = %s and finished_at is null
           and options ? 'credits_per_minute'
        """,
        (user_id,),
    ).fetchone()
    return int(row["n"])


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
            # Past the day's allowance, which is not the same as refused -- a
            # credit is what one more costs. It is charged at the end, though,
            # so what has to hold here is that the balance covers this job
            # *and* everything already started and not yet paid for.
            #
            # `unsettled` is the half that is easy to leave out and expensive
            # to leave out: without it one credit submits ten videos, because
            # every submission sees the same untouched balance.
            spendable = balance(conn, user_id) - unsettled(conn, user_id)
            if spendable < CREDITS_PER_MINUTE:
                raise QuotaExceeded(tier, used, local=local)

            # A speech clip is read from text that is already here and
            # synthesised on this machine; Kokoro costs nothing per character.
            # Charging a minute for it would put a price on the one thing in
            # the product that has no marginal cost, so clips stay on the
            # allowance and are never stamped.
            #
            # A video is priced by the minute, and the rate rides on the job
            # the way `tts` and `resolution` do: the API owns the price, the
            # worker applies it, and neither has to import the other.
            if merged.get("kind") != "speech":
                # Priced from the input where there is one. A script's
                # narration is the script, so its length is a real number. A
                # topic's is not -- the model decides -- and that case is
                # caught by the worker, which knows the answer exactly.
                if merged.get("script") or merged.get("lesson"):
                    need = estimate_minutes(
                        str(merged.get("script") or merged.get("lesson") or ""))
                    if spendable < need:
                        raise QuotaExceeded(tier, used, local=local)
                merged["credits_per_minute"] = CREDITS_PER_MINUTE

        row = conn.execute(
            """
            insert into jobs (user_id, topic, tier, options, idempotency_key)
            values (%s, %s, %s, %s, %s)
            returning id
            """,
            (user_id, topic, tier.name, Json(merged), idempotency_key),
        ).fetchone()

    return str(row["id"]), tier
