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
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator, model_validator

from pycoding_worker.db import Queue

from .auth import User, current_user
from .billing import SignatureError, balance, handle_webhook, ledger, tier_for_user
from .quota import (
    QuotaExceeded,
    Tier,
    limit_for,
    submit_within_quota,
    tier_for,
    used_today,
)
from pycoding_worker.storage import (
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

DEFAULT_DSN = os.environ.get("PYCODING_DSN", "postgresql:///pycoding")


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
        title="pycoding",
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

    @app.get("/health")
    def health() -> dict:
        with _connect(dsn) as conn:
            conn.execute("select 1")
        return {"ok": True}

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
        return {
            "tier": tier.name,
            "jobs_per_day": tier.jobs_per_day,
            "tts": tier.tts,
            "credits": credits,
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
        return [JobResponse(**row) for row in rows]

    @app.get("/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str, user: User = Depends(current_user)) -> JobResponse:
        row = _job_or_404(job_id)
        if row["user_id"] != user.id:
            # Same response as a missing job: whether someone else's id exists
            # is not this caller's business.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
        return JobResponse(**row)

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
