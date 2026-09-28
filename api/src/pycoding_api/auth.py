"""Who is calling.

Supabase issues the tokens; this verifies them. The verification is separated
from the app so the tests can exercise every route without a live Supabase
project, and so nothing here ever constructs a spending client on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, status

JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
JWT_AUDIENCE = os.environ.get("SUPABASE_JWT_AUDIENCE", "authenticated")


@dataclass(frozen=True)
class User:
    id: str
    tier: str
    email: str = ""


def _decode(token: str) -> dict:
    if not JWT_SECRET:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "auth is not configured on this server (SUPABASE_JWT_SECRET unset)",
        )
    try:
        return jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired") from None
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from None


def current_user(
    authorization: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None),
) -> User:
    """Resolve the caller.

    ``X-Dev-User`` is honoured only when no JWT secret is configured, i.e.
    local development. It exists so the API is runnable and testable without a
    Supabase project, and it cannot be used to bypass auth in a deployed
    server because configuring the secret disables it.
    """
    if authorization and authorization.lower().startswith("bearer "):
        claims = _decode(authorization.split(" ", 1)[1].strip())
        metadata = claims.get("user_metadata") or {}
        return User(
            id=str(claims["sub"]),
            tier=str(claims.get("tier") or metadata.get("tier") or "free"),
            email=str(claims.get("email") or ""),
        )

    if x_dev_user and not JWT_SECRET:
        # Development only: "<id>" or "<id>:<tier>".
        user_id, _, tier = x_dev_user.partition(":")
        return User(id=user_id, tier=tier or "free")

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
