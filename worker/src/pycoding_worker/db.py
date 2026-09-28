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
  max_attempts     integer not null default 2,

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

create index if not exists jobs_claim_idx on jobs (created_at)
  where status = 'queued';
create index if not exists jobs_lease_idx on jobs (lease_expires_at)
  where status in ({", ".join(f"'{s}'" for s in ACTIVE_STATUSES)});
create index if not exists jobs_user_idx on jobs (user_id, created_at desc);
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
                   where status = 'queued'
                      or (status in ({active}) and lease_expires_at < now())
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
                  stage: str | None = None, progress: float | None = None) -> bool:
        """Extend the lease. Returns False if it is no longer ours.

        The return value is the point: if this comes back False the lease was
        reaped and another worker may be running the same job, so the caller
        must stop immediately rather than finish and spend a second time.
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
                returning id
                """,
                (LEASE_SECONDS, status, stage, progress, job_id, owner),
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
                returning id
                """,
                (artifact_key, duration_s, job_id, owner),
            ).fetchone()
        return row is not None

    def fail(self, job_id: str, owner: str, *, code: str, message: str) -> bool:
        """Mark a job failed. Requeues instead if attempts remain."""
        with self._connect() as conn:
            row = conn.execute(
                """
                update jobs
                   set status = case when attempt < max_attempts
                                     then 'queued'::job_status
                                     else 'failed'::job_status end,
                       lease_owner = null,
                       lease_expires_at = null,
                       error_code = %s,
                       error_message = %s,
                       finished_at = case when attempt < max_attempts
                                          then null else now() end
                 where id = %s and lease_owner = %s
                returning status
                """,
                (code, message[:2000], job_id, owner),
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
