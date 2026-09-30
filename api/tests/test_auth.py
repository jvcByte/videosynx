"""Verifying a Supabase token.

Nothing exercised this before: every other test authenticates through the
``X-Dev-User`` bypass, so the whole of the token path -- expiry, audience,
signature -- was code nobody had run. These do not need a Supabase project.
A keypair and a key set are generated here, which is the point: the tokens are
real ES256 tokens signed by a real key, and the only thing missing is Supabase
publishing them.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from videosynx_api import auth

AUDIENCE = "authenticated"
LEGACY_SECRET = "a-shared-secret-long-enough-for-hs256-fixture"
ISSUER = "https://test.supabase.co/auth/v1"


# --------------------------------------------------------------- key material

def keypair():
    """An ES256 key and the public half as a JWK, the way Supabase publishes it.

    PyJWT can do this conversion itself, which keeps the test from having to
    know the private key's encoding.
    """
    private = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    public_jwk.update({"kid": "test-key", "alg": "ES256", "use": "sig"})
    return private, public_jwk


class JwksServer:
    """Serves a key set over HTTP, because PyJWKClient fetches it by URL."""

    def __init__(self, jwk: dict):
        self.jwk = jwk
        self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests += 1
                body = json.dumps({"keys": [outer.jwk]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/jwks.json"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def jwks(monkeypatch):
    """A server, a key, and `auth` pointed at them."""
    private, jwk = keypair()
    server = JwksServer(jwk)
    monkeypatch.setattr(auth, "JWKS_URL", server.url)
    monkeypatch.setattr(auth, "JWT_SECRET", "")
    monkeypatch.setattr(auth, "_jwks_client", None)
    monkeypatch.setattr(auth, "JWT_AUDIENCE", AUDIENCE)
    yield private, server
    server.stop()


def token(private, **claims) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "user-1",
        "aud": AUDIENCE,
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(hours=1),
        **claims,
    }
    return jwt.encode(payload, private, algorithm="ES256", headers={"kid": "test-key"})


# ---------------------------------------------------------------- asymmetric

def test_a_valid_es256_token_is_accepted(jwks):
    """The generation Supabase issues now, and the one this used to reject."""
    private, _ = jwks
    user = auth.current_user(authorization=f"Bearer {token(private, email='a@b.co')}")
    assert user.id == "user-1"
    assert user.email == "a@b.co"
    # Nothing sets a tier claim yet, so everyone is on the free plan.
    assert user.tier == "free"


def test_an_expired_token_is_refused(jwks):
    private, _ = jwks
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    stale = token(private, exp=past, iat=past - timedelta(hours=1))
    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization=f"Bearer {stale}")
    assert caught.value.status_code == 401
    assert "expired" in caught.value.detail


def test_a_token_for_another_audience_is_refused(jwks):
    private, _ = jwks
    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization=f"Bearer {token(private, aud='something-else')}")
    assert caught.value.status_code == 401


def test_a_token_signed_by_someone_else_is_refused(jwks):
    """The whole point of the key set: a forged token does not verify."""
    _, server = jwks
    attacker, _ = keypair()
    forged = token(attacker)
    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization=f"Bearer {forged}")
    assert caught.value.status_code == 401


def test_an_unsigned_token_is_refused(jwks):
    """`alg: none` is the oldest trick there is."""
    unsigned = jwt.encode({"sub": "user-1", "aud": AUDIENCE,
                           "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                          key="", algorithm="none")
    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization=f"Bearer {unsigned}")
    assert caught.value.status_code == 401


def test_an_hs256_token_is_refused_when_the_project_signs_asymmetrically(jwks):
    """Otherwise anyone holding the legacy secret could mint tokens for a
    project that no longer uses it."""
    _, server = jwks
    secret_signed = jwt.encode(
        {"sub": "user-1", "aud": AUDIENCE,
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        LEGACY_SECRET, algorithm="HS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization=f"Bearer {secret_signed}")
    assert caught.value.status_code == 401


def test_an_unreachable_key_set_is_503_not_401(monkeypatch):
    """A Supabase blip must not sign a user out of a working session.

    401 tells a client its token is bad, so it throws the token away. The token
    may be perfectly good -- we simply could not check it -- and the honest
    answer is "try again shortly".
    """
    monkeypatch.setattr(auth, "JWT_SECRET", "")
    # A port nothing is listening on.
    monkeypatch.setattr(auth, "JWKS_URL", "http://127.0.0.1:9/jwks.json")
    monkeypatch.setattr(auth, "_jwks_client", None)

    private, _ = keypair()
    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization=f"Bearer {token(private)}")
    assert caught.value.status_code == 503
    assert "signing keys" in caught.value.detail


def test_the_key_set_is_fetched_once_for_many_requests(jwks):
    """It is on the path of every request; refetching it each time would put
    Supabase in the critical path of the whole API."""
    private, server = jwks
    for _ in range(5):
        auth.current_user(authorization=f"Bearer {token(private)}")
    assert server.requests == 1


# ------------------------------------------------------------------- legacy

def test_a_legacy_hs256_project_still_works(monkeypatch):
    """Older projects sign with the shared secret and must keep working."""
    monkeypatch.setattr(auth, "JWKS_URL", "")
    monkeypatch.setattr(auth, "JWT_SECRET", LEGACY_SECRET)
    monkeypatch.setattr(auth, "_jwks_client", None)

    good = jwt.encode(
        {"sub": "user-9", "aud": AUDIENCE, "tier": "paid",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        LEGACY_SECRET, algorithm="HS256")
    user = auth.current_user(authorization=f"Bearer {good}")
    assert user.id == "user-9"
    assert user.tier == "paid"


# ------------------------------------------------------------ the dev bypass

def test_the_dev_bypass_is_off_once_a_credential_is_configured(jwks):
    """It cannot be used to walk into a deployed server."""
    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization=None, x_dev_user="user-1:paid")
    assert caught.value.status_code == 401


def test_the_dev_bypass_works_when_nothing_is_configured(monkeypatch):
    monkeypatch.setattr(auth, "JWKS_URL", "")
    monkeypatch.setattr(auth, "JWT_SECRET", "")

    assert auth.current_user(authorization=None, x_dev_user="user-1:paid").tier == "paid"
    assert auth.current_user(authorization=None, x_dev_user="user-1").tier == "free"


def test_no_credential_anywhere_is_503_rather_than_401(monkeypatch):
    """A server that cannot check anything is not the caller's fault either."""
    monkeypatch.setattr(auth, "JWKS_URL", "")
    monkeypatch.setattr(auth, "JWT_SECRET", "")
    monkeypatch.setattr(auth, "_jwks_client", None)

    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization="Bearer whatever", x_dev_user=None)
    assert caught.value.status_code == 503


def test_the_dev_bypass_is_not_reachable_while_signed_out(jwks):
    """No header at all is a 401, which is what a signed-out browser sends."""
    with pytest.raises(HTTPException) as caught:
        auth.current_user(authorization=None, x_dev_user=None)
    assert caught.value.status_code == 401
    assert caught.value.headers["WWW-Authenticate"] == "Bearer"
