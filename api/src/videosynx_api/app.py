"""The HTTP API.

Two rules shape this:

Submitting a job must not spend anything. It validates the request, checks the
quota against a row count, and inserts a queued row. The code generation, the
sandbox and the speech all happen in the worker. Nothing here constructs a
Gemini or ElevenLabs client, which is why a submission that is going to be
rejected is rejected before it can cost money.

Quota is enforced in the same transaction as the insert, so a rejected request
leaves no row behind and two concurrent submits cannot both pass a check with
room for one.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator, model_validator

from videosynx_worker.db import Queue

from .auth import User, current_user
from .billing import SignatureError, balance, handle_webhook, ledger, tier_for_user
from .progress import progress_for, streak_from
from .quota import (
    QuotaExceeded,
    Tier,
    limit_for,
    submit_within_quota,
    tier_for,
    used_today,
)
from videosynx_worker.storage import (
    DEFAULT_URL_TTL,
    LocalStorage,
    Storage,
    from_env,
    video_key,
)

from .settings import Mode, Settings, load_settings, save_settings, voice_options


def _looks_like_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True

DEFAULT_DSN = os.environ.get("VIDEOSYNX_DSN", "postgresql:///videosynx")


# A script is sent as text, so this is the only bound on request size: there is
# no body limit anywhere else.
MAX_SCRIPT_CHARS = 60_000


class SubmitRequest(BaseModel):
    topic: str = Field(default="", max_length=300,
                       description="What to teach. May be left out when a "
                                   "script or a lesson is supplied instead.")
    script: str | None = Field(
        default=None, max_length=MAX_SCRIPT_CHARS,
        description="A written script to render. Travels with the job as "
                    "text, so the API and the worker need not share a disk.",
    )
    mode: Mode | None = Field(
        default=None,
        description="'both' explains the idea then writes code; 'coding' is "
                    "code alone; 'illustration' is drawn explanation alone, "
                    "with no code. Left out, the saved setting is used.")
    generation: Literal["auto", "local"] | None = Field(
        default=None,
        description="'local' promises never to call a model. It renders what "
                    "you supply -- lesson JSON, or prose it structures on this "
                    "machine -- so a bare topic is refused rather than "
                    "quietly spending.")
    lesson: str | None = Field(
        default=None,
        description="Path to a lesson JSON on the worker's machine. Kept for "
                    "local runs; a hosted job should send `script` instead.",
    )

    @model_validator(mode="after")
    def _needs_a_subject(self) -> "SubmitRequest":
        if not (self.topic.strip() or self.script or self.lesson):
            raise ValueError("provide a topic, a script, or a lesson to render")
        if self.topic.strip() and len(self.topic.strip()) < 3:
            raise ValueError("topic must be at least 3 characters")
        return self


def can_render_locally(body: SubmitRequest) -> bool:
    """Whether local mode has anything to work from.

    It takes lesson JSON, a lesson file, or prose -- which it structures on
    this machine. Everything except a topic, which only exists once something
    has written it, and writing is the one thing local mode will not do.
    """
    return bool((body.script or "").strip() or body.lesson)


def job_label(body: SubmitRequest) -> str:
    """What to call this job in the list.

    A script wins over a topic, because that is what the renderer does too:
    the script decides the video's content, so naming the row after the topic
    would describe a video that was never made. Submitting a fine-tuning
    script with "dictionary in python" still in the topic box produced a row
    labelled "dictionary in python" containing the fine-tuning lesson.
    """
    for line in (body.script or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    if body.topic.strip():
        return body.topic.strip()
    return "untitled"


class SubmitResponse(BaseModel):
    id: str
    status: str
    tier: str
    jobs_per_day: int
    used_today: int


class JobResponse(BaseModel):
    id: str
    status: str
    topic: str
    # Which kind of video this is. Lifted out of the options blob because a
    # library entry has to be able to say what it is without unpacking jsonb.
    mode: str = "both"
    stage: str | None = None
    progress: float = 0.0
    duration_s: float | None = None
    artifact_key: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt: int = 0
    max_attempts: int = 1
    # When a requeued job becomes claimable again. A queued job with an
    # attempt already spent is waiting out a retry, and the page says so
    # rather than reporting a failure the user can do nothing about.
    not_before: datetime | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    # Whether the finished file is still there to serve.
    #
    # A job keeps its place in the library when its object is gone -- the
    # render happened, and the row is the record of it -- but it must not go
    # on offering to play something that is not there. The default local
    # store is a `/tmp` directory, and `/tmp` is cleared; a bucket can be
    # emptied; either way the row is the only place that knows.
    #
    # None when it does not apply: nothing that has not finished has a file
    # to be missing.
    available: bool | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _uuid_to_str(cls, value):
        # Postgres hands back a UUID object; the wire format is a string.
        return str(value)

    @model_validator(mode="before")
    @classmethod
    def _mode_from_options(cls, data):
        if isinstance(data, dict) and "mode" not in data:
            options = data.get("options")
            if isinstance(options, dict):
                return {**data, "mode": str(options.get("mode") or "both")}
        return data


class QuotaResponse(BaseModel):
    """Two allowances, counted apart.

    A job that calls a model spends money. A local job spends none, so it is
    not counted against the same limit -- it is what someone reaches for when
    that limit is gone, and counting it would make it unavailable exactly when
    it is wanted. It still renders on this machine, so it has its own limit
    rather than none.
    """

    tier: str
    jobs_per_day: int
    used_today: int
    remaining: int
    local_jobs_per_day: int
    local_used_today: int
    local_remaining: int
    narration: bool
    tts: str
    max_snippets: int


def _published_example_ids() -> list[str]:
    """Which videos the operator has chosen to show on the landing page.

    An environment variable rather than a schema column, because publishing
    one is a decision someone makes about a particular video, not a property
    a video accumulates.
    """
    # Checked as UUIDs here rather than left to the database, which rejects a
    # malformed one with an error rather than an empty result -- a typo in the
    # variable would take the public landing page down instead of quietly
    # showing one example fewer.
    ids: list[str] = []
    for part in os.environ.get("VIDEOSYNX_EXAMPLES", "").split(","):
        try:
            ids.append(str(uuid.UUID(part.strip())))
        except ValueError:
            continue
    return ids


def _with_availability(store, row: dict) -> dict:
    """Say whether a finished job's file can still be served.

    Deliberately a question asked at read time rather than a column: a row
    that recorded "present" when it finished would be wrong the moment
    anything emptied the store, and nothing would ever correct it.

    It costs one ``exists()`` per finished job per call -- a stat locally, a
    HEAD on object storage. At twenty rows that is twenty of them on every
    poll, which is why this is the first thing to memoise if a list ever
    feels slow; the answer for a given key does not change back.
    """
    if row["status"] != "succeeded":
        return row
    # Finished with nothing to show for it counts as gone, not as unknown: a
    # succeeded row with no key is not a file the page can play either.
    return {
        **row,
        "available": bool(row["artifact_key"]) and store.exists(row["artifact_key"]),
    }


def _poster_for(store, row: dict) -> str:
    """A still for the video, made once and kept.

    Frame zero is a blank editor with nothing typed in it, which is a
    thumbnail of nothing, so this takes a frame from a fifth of the way in --
    far enough in that the code or the slide is established rather than still
    arriving, and short of the end where the summary is.
    """
    key = video_key(row["tier"], str(row["id"]), ".poster.jpg")
    if store.exists(key):
        return key

    body = store.read(video_key(row["tier"], str(row["id"])))
    if body is None:
        return key  # the caller checks exists() and falls back to no poster
    at = max(1.0, float(row["duration_s"] or 0) * 0.45)

    with tempfile.TemporaryDirectory(prefix="videosynx_poster_") as td:
        source = Path(td) / "in.mp4"
        out = Path(td) / "poster.jpg"
        source.write_bytes(body)
        try:
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-ss", f"{at:.1f}",
                 "-i", str(source), "-frames:v", "1", "-q:v", "4",
                 "-y", str(out)],
                check=True, timeout=60,
            )
        except (subprocess.SubprocessError, OSError):
            # A thumbnail is not worth failing a public page over.
            return key
        if out.exists():
            store.put(key, out)
    return key


def _achievement_dates(conn, user_id: str) -> dict:
    """The day each milestone was first reached, where it can be known.

    Read from the history rather than recorded when it happened: these are
    derived facts about rows that already exist, and a table of award times
    would be a second source of truth for something the jobs table already
    answers. Anything not reached is absent.
    """
    dates: dict[str, "datetime | None"] = {}
    row = conn.execute(
        """
        select min(created_at) filter (where running_rendered >= 1) as first,
               min(created_at) filter (where running_rendered >= 5) as runner,
               min(created_at) filter (where running_seconds >= 3600) as marathon,
               min(created_at) filter (where running_rendered >= 25) as prolific
          from (
            select created_at, status,
                   count(*) filter (where status = 'succeeded')
                     over (order by created_at) as running_rendered,
                   coalesce(sum(duration_s) filter (where status = 'succeeded')
                     over (order by created_at), 0) as running_seconds
              from jobs where user_id = %s
          ) history
        """,
        (user_id,),
    ).fetchone()
    for key in ("first", "runner", "marathon", "prolific"):
        when = row[key] if row else None
        dates[key] = when.date() if when else None
    return dates


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


def create_app(dsn: str | None = None, storage: Storage | None = None) -> FastAPI:
    dsn = dsn or DEFAULT_DSN
    store = storage or from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Queue(dsn).install()  # idempotent
        yield

    app = FastAPI(
        title="videosynx",
        version="0.1.0",
        description="Generate narrated coding tutorial videos.",
        lifespan=lifespan,
    )
    app.state.dsn = dsn
    app.state.storage = store

    # The frontend is served from a different origin, which is the normal
    # arrangement (web on :3000, API on :8000). Without this every request
    # from the browser is blocked before it is even sent, and the symptom is
    # a generic "could not reach the API" that looks like the server is down.
    origins = [
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        # PUT is here for saving settings. Leaving it out is invisible to the
        # test client -- it does not run CORS -- and breaks only in a browser,
        # where the preflight is refused and the save fails silently.
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Idempotency-Key",
                       "X-Dev-User"],
    )

    def _job_or_404(job_id: str) -> dict:
        row = Queue(dsn).get(job_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
        return row

    def _effective_tier(user: User) -> str:
        """What the account is entitled to *now*.

        Read from the database rather than the token: a JWT minted before an
        upgrade would otherwise keep granting the old tier, which reads as
        "I paid and nothing happened".
        """
        with _connect(dsn) as conn:
            return tier_for_user(conn, user.id, fallback=user.tier)

    # ------------------------------------------------------------------ meta

    # Both spellings. /healthz is what a load balancer or an orchestrator
    # probes by default, and a 404 there reads as a broken service rather
    # than a wrong path -- which is exactly how a stray probe on this machine
    # looked for a while.
    @app.get("/health")
    @app.get("/healthz")
    def health() -> dict:
        with _connect(dsn) as conn:
            conn.execute("select 1")
        return {"ok": True}

    @app.get("/me/progress")
    def my_progress(user: User = Depends(current_user)) -> dict:
        """What this account has earned, from what it has actually done.

        Every figure is computed here from the job rows -- no stored total, no
        award path, nothing that can go up without a render finishing. See
        `progress.py` for why that is the constraint rather than an
        implementation detail.
        """
        with _connect(dsn) as conn:
            totals = conn.execute(
                """
                select count(*) filter (where status = 'succeeded') as rendered,
                       coalesce(sum(duration_s)
                                filter (where status = 'succeeded'), 0) as seconds
                  from jobs where user_id = %s
                """,
                (user.id,),
            ).fetchone()
            # Every day this account started something, which is the only
            # activity there is to count.
            days = {
                row["day"]
                for row in conn.execute(
                    "select distinct created_at::date as day from jobs "
                    "where user_id = %s",
                    (user.id,),
                ).fetchall()
            }
            # When each milestone was first reached, for the badges that show
            # a date.
            earned_on = _achievement_dates(conn, user.id)

        rendered = int(totals["rendered"])
        minutes = float(totals["seconds"]) / 60
        streak = streak_from(days, datetime.now(timezone.utc).date())
        return progress_for(rendered=rendered, minutes=minutes, streak=streak,
                            earned_on=earned_on).as_dict()

    @app.get("/examples")
    def examples() -> list[dict]:
        """Published tutorials, for the landing page.

        Public, because the landing page is. Which videos appear is a written
        list rather than a query -- a user's video becoming public because it
        happened to be recent would be a privacy bug dressed as a feature.

        The URLs are signed the same way a private one is, so this works with
        object storage as well as disk, and they expire like any other.
        """
        published: list[dict] = []
        for job_id in _published_example_ids():
            row = Queue(dsn).get(job_id)
            if row is None or row["status"] != "succeeded":
                continue
            poster = _poster_for(store, row)
            published.append({
                "id": str(row["id"]),
                "title": row["topic"],
                "duration_s": row["duration_s"],
                "mode": str((row["options"] or {}).get("mode") or "both"),
                "poster_url": store.url(poster) if store.exists(poster) else None,
                "video_url": store.url(video_key(row["tier"], str(row["id"]))),
            })
        return published

    @app.get("/stats")
    def stats() -> dict:
        """What the service has actually done, for the landing page.

        Public on purpose -- it is counts and nothing about anybody, the same
        shape as /health carrying no user. Every number is measured, which is
        the whole reason this is a query: a landing page claiming totals
        nobody earned is a lie told on the way in, and this one cannot drift
        into one because there is nothing here to edit.
        """
        with _connect(dsn) as conn:
            row = conn.execute(
                """
                select count(*) filter (where status = 'succeeded') as rendered,
                       coalesce(sum(duration_s)
                                filter (where status = 'succeeded'), 0) as seconds,
                       count(distinct topic) filter (where status = 'succeeded')
                         as topics
                  from jobs
                """
            ).fetchone()
        return {
            "tutorials_rendered": int(row["rendered"]),
            "minutes_of_video": round(float(row["seconds"]) / 60, 1),
            "topics_covered": int(row["topics"]),
        }

    @app.get("/me/quota", response_model=QuotaResponse)
    def quota(user: User = Depends(current_user)) -> QuotaResponse:
        tier = tier_for(_effective_tier(user))
        with _connect(dsn) as conn:
            used = used_today(conn, user.id)
            local_used = used_today(conn, user.id, local=True)
        return QuotaResponse(
            tier=tier.name,
            jobs_per_day=tier.jobs_per_day,
            used_today=used,
            remaining=max(0, tier.jobs_per_day - used),
            local_jobs_per_day=tier.local_jobs_per_day,
            local_used_today=local_used,
            local_remaining=max(0, tier.local_jobs_per_day - local_used),
            narration=tier.narration,
            tts=tier.tts,
            max_snippets=tier.max_snippets,
        )

    # --------------------------------------------------------------- billing

    @app.post("/billing/webhook")
    async def stripe_webhook(request: Request) -> dict:
        """Stripe calls this; nobody else should be able to.

        The raw body is verified, not a re-serialised copy of it: the
        signature covers the exact bytes Stripe sent.
        """
        payload = await request.body()
        signature = request.headers.get("stripe-signature", "")
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        try:
            return handle_webhook(dsn, payload, signature, secret)
        except SignatureError as exc:
            # 400 so Stripe does not keep retrying something we will always
            # reject.
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"invalid signature: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed payload") from exc

    @app.get("/me/subscription")
    def subscription(user: User = Depends(current_user)) -> dict:
        tier = tier_for(_effective_tier(user))
        with _connect(dsn) as conn:
            credits = balance(conn, user.id)
            recent = ledger(conn, user.id, limit=10)
            # Counted here rather than by the client. The job list is capped
            # at twenty, so a page counting its own rows would start quietly
            # reporting "20 tutorials" to anyone with more.
            totals = conn.execute(
                """
                select count(*) filter (where status = 'succeeded') as rendered,
                       count(*) as created,
                       coalesce(sum(duration_s)
                                filter (where status = 'succeeded'), 0) as seconds
                  from jobs where user_id = %s
                """,
                (user.id,),
            ).fetchone()
        return {
            "tier": tier.name,
            "jobs_per_day": tier.jobs_per_day,
            "tts": tier.tts,
            "credits": credits,
            "tutorials_created": int(totals["created"]),
            "tutorials_completed": int(totals["rendered"]),
            "minutes_of_video": round(float(totals["seconds"]) / 60, 1),
            "recent": [
                {"delta": r["delta"], "reason": r["reason"],
                 "at": r["created_at"].isoformat() if r["created_at"] else None}
                for r in recent
            ],
        }

    @app.post("/billing/checkout")
    def checkout(user: User = Depends(current_user)) -> dict:
        """Start a subscription. Returns a URL for the client to open."""
        secret = os.environ.get("STRIPE_SECRET_KEY", "")
        price = os.environ.get("STRIPE_PRICE_ID", "")
        if not secret or not price:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "billing is not configured on this server "
                "(STRIPE_SECRET_KEY and STRIPE_PRICE_ID unset)",
            )
        import stripe

        session = stripe.checkout.Session.create(
            api_key=secret,
            mode="subscription",
            line_items=[{"price": price, "quantity": 1}],
            client_reference_id=user.id,
            # Carried onto the subscription so later webhooks know whose
            # entitlement this is without a separate lookup.
            metadata={"user_id": user.id, "tier": "paid"},
            subscription_data={"metadata": {"user_id": user.id, "tier": "paid"}},
            success_url=os.environ.get("STRIPE_SUCCESS_URL", "https://example.com/ok"),
            cancel_url=os.environ.get("STRIPE_CANCEL_URL", "https://example.com/cancel"),
        )
        return {"url": session.url, "id": session.id}

    # --------------------------------------------------------------- settings

    @app.get("/voices")
    def list_voices(user: User = Depends(current_user)) -> list[dict]:
        """The voices a narration can use.

        A catalogue rather than a user setting, hence not under /me -- but it
        still needs an account, because it describes what the service offers.
        """
        return voice_options()

    @app.get("/me/settings", response_model=Settings)
    def read_settings(user: User = Depends(current_user)) -> Settings:
        return load_settings(dsn, user.id)

    @app.put("/me/settings", response_model=Settings)
    def write_settings(body: Settings, user: User = Depends(current_user)) -> Settings:
        """Replace the saved settings.

        A PUT, not a PATCH: the body is validated into a complete Settings with
        defaults filled in, so a field left out returns to its default rather
        than keeping a stale value nobody can see.
        """
        save_settings(dsn, user.id, body.model_dump())
        return body

    # ------------------------------------------------------------------ jobs

    @app.post("/jobs", response_model=SubmitResponse,
              status_code=status.HTTP_202_ACCEPTED)
    def submit(
        body: SubmitRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        user: User = Depends(current_user),
    ) -> SubmitResponse:
        # Saved preferences first, then whatever this one request chose on top
        # of them. Tier-owned keys -- tts, resolution, watermark,
        # max_snippets -- are merged over both by submit_within_quota, so
        # neither layer can ask for more than the tier gives.
        options: dict[str, object] = load_settings(dsn, user.id).as_options()
        if body.mode:
            options["mode"] = body.mode
        if body.generation:
            options["generation"] = body.generation

        # Refused here rather than queued and failed: the whole promise of
        # local mode is that nothing is spent, and an allowance that is
        # already gone is exactly when someone would reach for it.
        if options.get("generation") == "local" and not can_render_locally(body):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "local_mode_needs_content",
                    "message": "Local mode works from content you supply and "
                               "never calls a model, so it cannot write a "
                               "tutorial from a topic. Paste a script, or set "
                               "generation back to auto.",
                },
            )
        if body.script:
            options["script"] = body.script
        if body.lesson:
            options["lesson"] = body.lesson

        try:
            job_id, tier = submit_within_quota(
                dsn,
                user_id=user.id,
                topic=job_label(body),
                tier_name=_effective_tier(user),
                options=options,
                idempotency_key=idempotency_key,
            )
        except QuotaExceeded as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "quota_exceeded",
                    "tier": exc.tier.name,
                    "kind": "local" if exc.local else "model",
                    "limit": limit_for(exc.tier, exc.local),
                    "used": exc.used,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

        with _connect(dsn) as conn:
            used = used_today(conn, user.id)
        return SubmitResponse(id=job_id, status="queued", tier=tier.name,
                              jobs_per_day=tier.jobs_per_day, used_today=used)

    @app.get("/jobs", response_model=list[JobResponse])
    def list_jobs(limit: int = 20, user: User = Depends(current_user)) -> list[JobResponse]:
        limit = max(1, min(limit, 100))
        with _connect(dsn) as conn:
            rows = conn.execute(
                """
                select * from jobs where user_id = %s
                 order by created_at desc limit %s
                """,
                (user.id, limit),
            ).fetchall()
        return [JobResponse(**_with_availability(store, row)) for row in rows]

    @app.get("/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str, user: User = Depends(current_user)) -> JobResponse:
        row = _job_or_404(job_id)
        if row["user_id"] != user.id:
            # Same response as a missing job: whether someone else's id exists
            # is not this caller's business.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
        return JobResponse(**_with_availability(store, row))

    @app.post("/jobs/{job_id}/retry", response_model=JobResponse,
              status_code=status.HTTP_202_ACCEPTED)
    def retry_job(job_id: str, user: User = Depends(current_user)) -> JobResponse:
        """Put a job that has given up back in the queue.

        Retrying does not cost an allowance. It re-runs the work this row
        already describes -- the attempt it repeats was counted when the job
        was submitted, and a job that failed has not produced anything to
        charge for. Charging again would mean the way to recover from a
        failure is to spend another of three daily jobs on the same video.

        The attempt count starts over rather than continuing, because the
        budget is there to stop a job looping on its own, and someone asking
        for it by hand has seen the error and decided it is worth another go.
        """
        row = _job_or_404(job_id)
        if row["user_id"] != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
        if row["status"] != "failed":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"error": "not_failed", "status": row["status"]},
            )
        with _connect(dsn) as conn:
            fresh = conn.execute(
                """
                update jobs
                   set status = 'queued'::job_status,
                       attempt = 0,
                       not_before = now(),
                       error_code = null,
                       error_message = null,
                       stage = null,
                       progress = 0,
                       started_at = null,
                       finished_at = null,
                       lease_owner = null,
                       lease_expires_at = null
                 where id = %s and user_id = %s and status = 'failed'
                returning *
                """,
                (job_id, user.id),
            ).fetchone()
        if fresh is None:
            # The status was read a moment ago and the update is conditional
            # on it, so a worker cannot have taken it: this is another request
            # getting there first.
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail={"error": "not_failed"})
        return JobResponse(**fresh)

    @app.post("/jobs/{job_id}/cancel", response_model=JobResponse,
              status_code=status.HTTP_202_ACCEPTED)
    def cancel_job(job_id: str, user: User = Depends(current_user)) -> JobResponse:
        """Stop a job that has not finished.

        Clearing the lease is what makes this work: the worker holding it sees
        the next heartbeat come back empty and stops. A job that is only
        queued has no worker to tell, and is simply taken out of the queue.

        Latency, honestly two different numbers. While frames are being
        written the render asks roughly once a second, so the encode stops
        within about that. While the model is being called or the voice
        synthesised there is no loop to ask from -- those calls take no
        callback -- so the only check is the heartbeat thread, and it wakes
        every twenty seconds. A cancel during that phase lands within twenty
        seconds and then stops before the render starts.
        """
        row = _job_or_404(job_id)
        if row["user_id"] != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")

        stopped = Queue(dsn).cancel(job_id)
        if not stopped:
            # Already finished, already canceled, or a second request got
            # there first -- and the row itself was conditional on none of
            # those being true, so this cannot race past a completion.
            fresh = _job_or_404(job_id)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"error": "not_cancelable", "status": fresh["status"]},
            )
        return JobResponse(**_job_or_404(job_id))

    @app.get("/jobs/{job_id}/video")
    def get_video(job_id: str, user: User = Depends(current_user)) -> dict:
        """A time-limited download URL.

        The video bytes never pass through this service: the client is handed
        a presigned URL and fetches from the store directly. Proxying 100 MB
        through the API would make it the bottleneck it does not need to be.
        """
        row = _job_or_404(job_id)
        if row["user_id"] != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
        if row["status"] != "succeeded" or not row["artifact_key"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"error": "not_ready", "status": row["status"]},
            )
        # A finished job whose file has since gone. Signing a URL for it would
        # hand the browser an address that answers 404, which reads as a
        # broken page rather than as a video that is no longer kept.
        if not store.exists(row["artifact_key"]):
            raise HTTPException(
                status.HTTP_410_GONE,
                detail={"error": "artifact_missing",
                        "message": "This video is no longer stored. The record "
                                   "of the render is here, but the file is not."},
            )
        return {
            "id": row["id"],
            "url": store.url(row["artifact_key"]),
            "expires_in": DEFAULT_URL_TTL,
            "duration_s": row["duration_s"],
        }

    @app.get("/artifacts/{key:path}")
    def serve_artifact(key: str, expires: int | None = None, sig: str | None = None):
        """Serve a local artifact. Only exists for the local backend.

        Authorised by the signature in the URL rather than a header, the same
        way a presigned S3 URL works. It has to be: a ``<video src=...>``
        fetch carries no Authorization header, so a header-protected route is
        unplayable in a browser no matter how valid the token is.

        With object storage this route is closed entirely, rather than
        lingering as an unauthenticated file server.
        """
        if not isinstance(store, LocalStorage):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        if not key.startswith("artifacts/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        if not store.verify(key, expires, sig):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "link expired or invalid")
        body = store.read(key)
        if body is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        return Response(content=body, media_type="video/mp4")

    @app.get("/jobs/{job_id}/script")
    def get_script(job_id: str, user: User = Depends(current_user)) -> dict:
        """What the renderer actually produced.

        The generated code, the narration and every diagram are made afresh
        each run and exist nowhere else, so a video that came out wrong is
        impossible to diagnose without this. The worker writes it beside the
        video; older jobs predate it, hence the fallback.
        """
        row = _job_or_404(job_id)
        if row["user_id"] != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")

        key = video_key(row["tier"], str(row["id"]), ".script.json")
        if store.exists(key):
            body = store.read(key)
            if body is not None:
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    pass  # unreadable is the same as absent, from out here

        options: dict = row["options"] or {}
        return {
            "id": row["id"],
            "topic": row["topic"],
            "lesson": options.get("lesson"),
            "tts": options.get("tts"),
            "note": "no plan was saved for this job",
        }

    return app


app = create_app()
