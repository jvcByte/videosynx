"""Who is calling.

Supabase issues the tokens; this verifies them. Two generations of that exist
and both have to work: a current project signs with an asymmetric key published
at a JWKS endpoint, and an older one signs with a shared secret. Supabase calls
the second legacy and does not recommend it for new projects, and its CLI now
issues ES256 by default -- so a project created today sends tokens the
secret-based path cannot verify at all.

The verification is separated from the app so the tests can exercise every
route without a live Supabase project, and so nothing here ever constructs a
spending client on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, status

def _setting(name: str, default: str = "") -> str:
    """The environment, read when it is asked for rather than at import.

    These were module constants, and that made them empty. `videosynx_api.app`
    is imported at the top of `__main__`, and `load()` -- which is what opens
    the .env file -- is called inside `main()`, so every one of them was read
    from a process that had not read its configuration yet. Nothing noticed:
    a developer who exports the variables by hand has them present from the
    first instruction, so only the documented way of running this, from a
    .env file, lost them.
    """
    return os.environ.get(name, default)


def jwt_secret() -> str:
    return _setting("SUPABASE_JWT_SECRET")


def jwt_audience() -> str:
    return _setting("SUPABASE_JWT_AUDIENCE", "authenticated")


def jwks_url() -> str:
    """Where the signing keys are published.

    Supabase serves them from a fixed path under the project URL, so naming
    the project is enough; the explicit variable is there for a self-hosted
    deployment that serves them from somewhere else.
    """
    explicit = _setting("SUPABASE_JWKS_URL")
    if explicit:
        return explicit
    base = _setting("SUPABASE_URL").rstrip("/")
    return f"{base}/auth/v1/.well-known/jwks.json" if base else ""

# What a current project signs with. HS256 is deliberately absent: it is only
# accepted on the secret path, where the key that verifies is the key that
# would forge.
ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")


@dataclass(frozen=True)
class User:
    id: str
    tier: str
    email: str = ""


def configured() -> bool:
    """Whether this server can check a token at all."""
    return bool(jwks_url() or jwt_secret())


_jwks_client: jwt.PyJWKClient | None = None


def _signing_keys() -> jwt.PyJWKClient:
    """The key-set client, kept between requests so its cache is worth having.

    Built on first use rather than at import, so importing this module never
    opens a connection and a test can point it somewhere of its own.
    """
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(jwks_url())
    return _jwks_client


def _verified(token: str, key, algorithms: list[str]) -> dict:
    try:
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=jwt_audience(),
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired") from None
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}"
        ) from None


def _decode_asymmetric(token: str) -> dict:
    try:
        key = _signing_keys().get_signing_key_from_jwt(token)
    except jwt.PyJWKClientConnectionError as exc:
        # We could not reach the key set. The caller's token may be perfectly
        # good, and a 401 here would sign someone out of a working session
        # because Supabase had a bad minute. This is our problem, and 503 is
        # how a client is told to try again rather than to log in again.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"cannot reach the signing keys: {exc}",
        ) from None
    except jwt.PyJWKClientError as exc:
        # The key set was reachable and no key in it matches this token, which
        # is the caller's problem after all.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}"
        ) from None
    except jwt.InvalidTokenError as exc:
        # The token is not even shaped like one -- "not.a.token" fails here,
        # decoding the header, before any key is chosen. A 500 for something
        # the caller sent is wrong twice over: it reads as the server being
        # broken, and it is the answer a fuzzer gets for every junk string.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}"
        ) from None
    return _verified(token, key.key, list(ASYMMETRIC_ALGORITHMS))


def _decode(token: str) -> dict:
    if jwks_url():
        return _decode_asymmetric(token)
    secret = jwt_secret()
    if secret:
        return _verified(token, secret, ["HS256"])
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "auth is not configured on this server "
        "(set SUPABASE_URL or SUPABASE_JWT_SECRET)",
    )


def current_user(
    authorization: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None),
) -> User:
    """Resolve the caller.

    ``X-Dev-User`` is honoured only when nothing is configured to check a token
    with, i.e. local development. It exists so the API is runnable and testable
    without a Supabase project, and it cannot be used to bypass auth in a
    deployed server because configuring either credential disables it.
    """
    if authorization and authorization.lower().startswith("bearer "):
        claims = _decode(authorization.split(" ", 1)[1].strip())
        metadata = claims.get("user_metadata") or {}
        return User(
            id=str(claims["sub"]),
            # Nothing sets a tier claim yet, so every account is on the free
            # plan. Billing is separate and not wired to this.
            tier=str(claims.get("tier") or metadata.get("tier") or "free"),
            email=str(claims.get("email") or ""),
        )

    if x_dev_user and not configured():
        # Development only: "<id>" or "<id>:<tier>".
        user_id, _, tier = x_dev_user.partition(":")
        return User(id=user_id, tier=tier or "free")

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
