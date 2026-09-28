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

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator

from pycoding_worker.db import Queue

from .auth import User, current_user
from .billing import SignatureError, balance, handle_webhook, ledger, tier_for_user
from .quota import QuotaExceeded, Tier, tier_for, used_today, submit_within_quota
from pycoding_worker.storage import DEFAULT_URL_TTL, LocalStorage, Storage, from_env


def _looks_like_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True

DEFAULT_DSN = os.environ.get("PYCODING_DSN", "postgresql:///pycoding")


class SubmitRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    lesson: str | None = Field(
        default=None,
        description="Path to a lesson JSON. Renders authored content instead "
                    "of generating code from the topic.",
    )


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
    stage: str | None = None
    progress: float = 0.0
    duration_s: float | None = None
    artifact_key: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt: int = 0
    created_at: datetime | None = None
    finished_at: datetime | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _uuid_to_str(cls, value):
        # Postgres hands back a UUID object; the wire format is a string.
        return str(value)


class QuotaResponse(BaseModel):
    tier: str
    jobs_per_day: int
    used_today: int
    remaining: int
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
        return QuotaResponse(
            tier=tier.name,
            jobs_per_day=tier.jobs_per_day,
            used_today=used,
            remaining=max(0, tier.jobs_per_day - used),
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

    # ------------------------------------------------------------------ jobs

    @app.post("/jobs", response_model=SubmitResponse,
              status_code=status.HTTP_202_ACCEPTED)
    def submit(
        body: SubmitRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        user: User = Depends(current_user),
    ) -> SubmitResponse:
        try:
            job_id, tier = submit_within_quota(
                dsn,
                user_id=user.id,
                topic=body.topic,
                tier_name=_effective_tier(user),
                options={"lesson": body.lesson} if body.lesson else {},
                idempotency_key=idempotency_key,
            )
        except QuotaExceeded as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "quota_exceeded",
                    "tier": exc.tier.name,
                    "limit": exc.tier.jobs_per_day,
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
    def serve_artifact(key: str, user: User = Depends(current_user)):
        """Serve a local artifact. Only exists for the local backend.

        With object storage the client already has a presigned URL, so this
        route is closed rather than being an unauthenticated file server.
        """
        if not isinstance(store, LocalStorage):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        if not key.startswith("artifacts/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        body = store.read(key)
        if body is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        # Ownership: the key encodes the job, and the job has an owner.
        job_id = Path(key).stem
        row = Queue(dsn).get(job_id) if _looks_like_uuid(job_id) else None
        if row is None or row["user_id"] != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        return Response(content=body, media_type="video/mp4")

    @app.get("/jobs/{job_id}/script")
    def get_script(job_id: str, user: User = Depends(current_user)) -> dict:
        row = _job_or_404(job_id)
        if row["user_id"] != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
        options: dict = row["options"] or {}
        return {
            "id": row["id"],
            "topic": row["topic"],
            "lesson": options.get("lesson"),
            "tts": options.get("tts"),
        }

    return app


app = create_app()
