"""The job queue, in Postgres.

Deliberately not Celery. A render takes 30 seconds to 5 minutes and spends real
money, so every job has to reach a terminal state exactly once. Celery's
visibility timeout redelivers a job that runs long -- two workers render the
same video and the ElevenLabs bill doubles -- and shortening it means crashed
jobs stall instead. There is no correct value, because the mechanism is wrong
for work of this length.

``FOR UPDATE SKIP LOCKED`` gives the same thing with none of that: the claim is
transactional with the job state, so there is no broker message to lose and no
row/message dual-write to reconcile.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

ACTIVE_STATUSES = (
    "leased", "generating", "narrating", "executing", "rendering", "uploading",
)

LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 20
# Beyond the lease, so a worker that is merely between heartbeats is not
# declared dead and reaped out from under itself.
REAP_GRACE_SECONDS = 300

SCHEMA = f"""
do $$ begin
  create type job_status as enum (
    'queued','leased','generating','narrating','executing',
    'rendering','uploading','succeeded','failed','canceled'
  );
exception when duplicate_object then null;
end $$;

create table if not exists jobs (
  id               uuid primary key default gen_random_uuid(),
  user_id          text not null,
  status           job_status not null default 'queued',
  topic            text not null,
  options          jsonb not null default '{{}}',

  -- Frozen at submit time. A job must not change what it is allowed to spend
  -- because a tier changed while it sat in the queue.
  tier             text not null default 'free',

  lease_owner      text,
  lease_expires_at timestamptz,
  heartbeat_at     timestamptz,
  attempt          integer not null default 0,
  max_attempts     integer not null default 7,

  -- Earliest a retry may run. A failed job is requeued but not claimable
  -- until this passes, which is what makes the retry a retry: the attempt
  -- that follows a failure one second later finds whatever was wrong exactly
  -- as it was.
  not_before       timestamptz not null default now(),

  stage            text,
  progress         real not null default 0,

  artifact_key     text,
  duration_s       real,
  error_code       text,
  error_message    text,

  idempotency_key  text unique,
  created_at       timestamptz not null default now(),
  started_at       timestamptz,
  finished_at      timestamptz
);

-- Adding to the create-table body above does nothing to a database that
-- already exists, so a new column needs both. These are idempotent, which is
-- what lets install() keep running on every API start and before every test.
alter table jobs add column if not exists not_before timestamptz not null default now();
alter table jobs alter column max_attempts set default 7;

create index if not exists jobs_claim_idx on jobs (created_at)
  where status = 'queued';
create index if not exists jobs_lease_idx on jobs (lease_expires_at)
  where status in ({", ".join(f"'{s}'" for s in ACTIVE_STATUSES)});
create index if not exists jobs_user_idx on jobs (user_id, created_at desc);

-- Billing. The tier a user is entitled to lives here rather than in their
-- token: a token issued before an upgrade would otherwise keep granting the
-- old tier until it expired, and "I paid and nothing changed" is the worst
-- possible first impression.
create table if not exists subscriptions (
  user_id                text primary key,
  tier                   text not null default 'free',
  stripe_customer_id     text,
  stripe_subscription_id text,
  status                 text,
  current_period_end     timestamptz,
  updated_at             timestamptz not null default now()
);

-- What a user *chose*, as opposed to what their tier *allows*. The tier owns
-- anything that costs money or is promised by the plan -- voice engine,
-- resolution, watermark, snippet cap -- and those are merged over the top at
-- submit time, so nothing saved here can grant more than the tier gives.
create table if not exists user_settings (
  user_id    text primary key,
  settings   jsonb not null default '{{}}',
  updated_at timestamptz not null default now()
);

-- Every grant and charge, append-only. stripe_event_id is unique, which is
-- what makes a webhook that Stripe delivers twice grant once.
create table if not exists credit_ledger (
  id              bigserial primary key,
  user_id         text not null,
  delta           integer not null,
  reason          text not null,
  stripe_event_id text unique,
  created_at      timestamptz not null default now()
);

create index if not exists ledger_user_idx on credit_ledger (user_id, created_at desc);
"""


@dataclass(frozen=True)
class Job:
    id: str
    user_id: str
    topic: str
    tier: str
    options: dict[str, Any]
    attempt: int
    max_attempts: int
    # Set when the job was claimed, so a retry knows how long it waited. Only
    # the queue reads it; the renderer has no use for it.
    not_before: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> "Job":
        return cls(
            id=str(row["id"]),
            user_id=row["user_id"],
            topic=row["topic"],
            tier=row["tier"],
            options=row["options"] or {},
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
            not_before=row.get("not_before"),
        )


class Queue:
    """Submit, claim, heartbeat, finish, and reap."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def install(self) -> None:
        """Create the type, table and indexes. Safe to run repeatedly."""
        with self._connect() as conn:
            conn.execute(SCHEMA)

    # ------------------------------------------------------------------ submit

    def submit(self, topic: str, *, user_id: str = "local", tier: str = "free",
               options: dict | None = None,
               idempotency_key: str | None = None) -> str:
        """Queue a job and return its id.

        A repeated submit with the same idempotency key returns the original
        job rather than queueing a second render -- a double-clicked button
        must not cost twice.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                insert into jobs (user_id, topic, tier, options, idempotency_key)
                values (%s, %s, %s, %s, %s)
                on conflict (idempotency_key) do update set topic = jobs.topic
                returning id
                """,
                (user_id, topic, tier, psycopg.types.json.Json(options or {}),
                 idempotency_key),
            ).fetchone()
            return str(row["id"])

    # ------------------------------------------------------------------- claim

    def claim(self, owner: str) -> Job | None:
        """Take the oldest available job, or return None.

        Two workers running this concurrently cannot get the same row: SKIP
        LOCKED steps over anything already locked, and the claim and the state
        change are one statement, so there is no window in between.
        """
        active = ", ".join(f"'{s}'" for s in ACTIVE_STATUSES)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                with candidate as (
                  select id from jobs
                   where (status = 'queued'
                          or (status in ({active}) and lease_expires_at < now()))
                     -- A job waiting out its retry delay is queued but not
                     -- yet due. This is the whole of the backoff.
                     and not_before <= now()
                   order by created_at
                   for update skip locked
                   limit 1
                )
                update jobs j
                   set status           = 'leased',
                       lease_owner      = %s,
                       lease_expires_at = now() + interval '{LEASE_SECONDS} seconds',
                       heartbeat_at     = now(),
                       attempt          = j.attempt + 1,
                       started_at       = coalesce(j.started_at, now())
                  from candidate
                 where j.id = candidate.id
                returning j.*
                """,
                (owner,),
            ).fetchone()
        return Job.from_row(row) if row else None

    # --------------------------------------------------------------- heartbeat

    def heartbeat(self, job_id: str, owner: str, *, status: str | None = None,
                  stage: str | None = None, progress: float | None = None) -> str | None:
        """Extend the lease. Returns the job's status, or None if it is gone.

        The return value is the point, and it is the status rather than a bare
        bool because there are two different reasons to stop. None means the
        lease was reaped and another worker may be running the same job, so
        the caller must stop rather than finish and spend a second time.
        ``"canceled"`` means whoever submitted it changed their mind, which is
        not a failure and must not be recorded as one.

        This is also the only channel a running job has for learning anything
        the world decided after it was claimed: nothing else reads the row
        between claiming it and finishing it.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                update jobs
                   set heartbeat_at     = now(),
                       lease_expires_at = now() + %s * interval '1 second',
                       status           = coalesce(%s, status)::job_status,
                       stage            = coalesce(%s, stage),
                       progress         = coalesce(%s, progress)
                 where id = %s and lease_owner = %s
                returning status
                """,
                (LEASE_SECONDS, status, stage, progress, job_id, owner),
            ).fetchone()
        return str(row["status"]) if row is not None else None

    def cancel(self, job_id: str) -> bool:
        """Stop a job that has not finished, whoever is running it.

        The lease is deliberately **left alone**. Clearing it looks tidier and
        is wrong: the worker holding it matches on ``lease_owner`` to read its
        own heartbeat back, so a nulled lease comes back empty and the worker
        concludes it was reaped -- which is the one thing a cancel is not. It
        would log an abandoned job and leave the row saying canceled with
        nobody having meant it.

        ``canceled`` is not in ``ACTIVE_STATUSES``, so a canceled row is
        invisible to both ``claim`` and ``reap`` -- it cannot be picked up
        again and it cannot be resurrected as an abandoned lease. ``finish``
        and ``fail`` refuse it separately, because they gate on the lease and
        the lease is still there.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                update jobs
                   set status = 'canceled'::job_status,
                       finished_at = now()
                 where id = %s
                   and status <> 'succeeded'
                   and status <> 'failed'
                   and status <> 'canceled'
                returning id
                """,
                (job_id,),
            ).fetchone()
        return row is not None

    # ---------------------------------------------------------------- terminal

    def finish(self, job_id: str, owner: str, *, artifact_key: str,
               duration_s: float) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                update jobs
                   set status = 'succeeded', artifact_key = %s, duration_s = %s,
                       finished_at = now(), lease_owner = null,
                       lease_expires_at = null, progress = 1
                 where id = %s and lease_owner = %s
                   -- A worker that finished anyway must not un-cancel a job
                   -- and hand back a video nobody asked for.
                   and status <> 'canceled'
                returning id
                """,
                (artifact_key, duration_s, job_id, owner),
            ).fetchone()
        return row is not None

    def fail(self, job_id: str, owner: str, *, code: str, message: str,
             delay_seconds: float | None = None) -> bool:
        """Mark a job failed, or requeue it to be tried again later.

        *delay_seconds* is how long to wait before the retry becomes
        claimable, or None to stop here however many attempts remain -- which
        is for work that was declined rather than attempted, where running it
        again declines in exactly the same way.

        The wait is the point. This requeued immediately, so the next attempt
        started a second after the one that failed and found conditions
        unchanged, which made a second attempt worth nothing.
        """
        retrying = delay_seconds is not None
        with self._connect() as conn:
            row = conn.execute(
                """
                update jobs
                   set status = case when %(retrying)s and attempt < max_attempts
                                     then 'queued'::job_status
                                     else 'failed'::job_status end,
                       lease_owner = null,
                       lease_expires_at = null,
                       -- Kept on the requeue path too: the page shows what
                       -- went wrong while it is still being retried.
                       error_code = %(code)s,
                       error_message = %(message)s,
                       not_before = case when %(retrying)s and attempt < max_attempts
                                         then now() + make_interval(secs => %(delay)s)
                                         else not_before end,
                       finished_at = case when %(retrying)s and attempt < max_attempts
                                          then null else now() end
                 where id = %(job_id)s and lease_owner = %(owner)s
                   -- Nor must a failure, on either path: a canceled job is
                   -- not a job that went wrong, and it must not be requeued.
                   and status <> 'canceled'
                returning status
                """,
                {"retrying": retrying, "delay": float(delay_seconds or 0.0),
                 "code": code, "message": message[:2000],
                 "job_id": job_id, "owner": owner},
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------- reap

    def reap(self) -> list[str]:
        """Recover jobs whose lease expired and whose worker never came back.

        Returns the ids that were requeued or failed, so a test (or a metric)
        can assert exactly once semantics rather than trusting the SQL.
        """
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                update jobs
                   set status = case when attempt < max_attempts
                                     then 'queued'::job_status
                                     else 'failed'::job_status end,
                       lease_owner = null,
                       lease_expires_at = null,
                       error_code = case when attempt < max_attempts
                                         then null else 'lease_expired' end,
                       finished_at = case when attempt < max_attempts
                                          then null else now() end
                 where status in ({", ".join(f"'{s}'" for s in ACTIVE_STATUSES)})
                   and lease_expires_at < now() - interval '{REAP_GRACE_SECONDS} seconds'
                returning id
                """
            ).fetchall()
        return [str(r["id"]) for r in rows]

    # ------------------------------------------------------------------ query

    def get(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            return conn.execute(
                "select * from jobs where id = %s", (job_id,)
            ).fetchone()

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "select status, count(*) as n from jobs group by status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def new_owner() -> str:
    """A stable-ish identity for this worker process."""
    import os
    import socket

    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
