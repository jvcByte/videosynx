"""Talking to the videosynx API on somebody else's behalf.

A client of the service, deliberately, and not a second implementation of
it. Quota, credits and ownership are each enforced in one place already;
an agent that read the database directly would be a second place to get
those right, and therefore the first place to get them wrong the day one
of them changes. So every capability downstream of this is a route that
already exists, called with the caller's own credentials -- which means an
agent can do exactly what the account it holds can do, and nothing needs
to re-check that.

Nothing here imports the MCP SDK. The tools are one binding of this; the
agent pane in the product will be another.
"""

from __future__ import annotations

import json
import os

import httpx

DEFAULT_URL = "http://localhost:8000"

# Submitting returns immediately -- the render happens elsewhere -- so no
# call here is long. The generous number is for a cold Postgres and a
# script read back out of object storage, not for a render.
TIMEOUT = 60.0

# Where tests put an httpx.MockTransport, so the request that would have
# gone out can be read back. Nothing sets it on a real run, and httpx
# builds its own client when it is None.
TRANSPORT: httpx.BaseTransport | None = None


class ApiError(Exception):
    """The service answered, and said no.

    Raised rather than returned, because every caller is a tool and the MCP
    layer turns a tool error into something the model reads and acts on. A
    tool that handed its failures back as ordinary data would look exactly
    like one that had succeeded.
    """


def base_url() -> str:
    """Where the service is. Unset and empty are the same thing here.

    They are not the same thing to `os.environ.get`, and that difference is
    not academic: an MCP client's config passes `${VIDEOSYNX_API_URL}`
    through, which arrives as an empty string rather than as an absent
    variable when nothing is set. Read literally, that builds a base of
    nothing and every request goes out relative to no host at all.
    """
    return (os.environ.get("VIDEOSYNX_API_URL") or DEFAULT_URL).rstrip("/")


def credentials() -> dict[str, str]:
    """Who to call as, from the environment this server was started with.

    One or the other, never both. A token is the real thing; the dev header
    is what a local server that has no sign-in configured accepts, and the
    API ignores it the moment one is. Sending both would be a confusing way
    to say the same thing.
    """
    token = os.environ.get("VIDEOSYNX_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    dev_user = os.environ.get("VIDEOSYNX_DEV_USER", "").strip()
    return {"X-Dev-User": dev_user} if dev_user else {}


def said(response: httpx.Response) -> str:
    """The service's own words for why it refused.

    FastAPI puts a string here for a plain refusal and an object for one
    with detail in it -- the quota 429 carries the tier, the limit and what
    was used. Both are worth passing on rather than flattening: the object
    is a sentence a model can act on ("three model jobs a day and three are
    used" beats "429").
    """
    try:
        body = response.json()
    except ValueError:
        # Not JSON at all -- a proxy's error page, most likely.
        return response.text.strip()[:400] or response.reason_phrase

    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return detail["message"]
    return json.dumps(detail, sort_keys=True)


def call(
    method: str,
    path: str,
    *,
    json_body: object = None,
    params: dict[str, object] | None = None,
) -> object:
    """One request. Returns the decoded body, or raises ApiError."""
    try:
        with httpx.Client(
            timeout=TIMEOUT, headers=credentials(), transport=TRANSPORT
        ) as http:
            response = http.request(
                method, f"{base_url()}{path}", json=json_body, params=params
            )
    except httpx.HTTPError as exc:
        # The likeliest failure by a wide margin, and the one where a
        # traceback tells the reader the least: the server simply is not
        # there yet.
        raise ApiError(
            f"cannot reach the videosynx API at {base_url()} ({exc.__class__.__name__}). "
            "Is it running, and is VIDEOSYNX_API_URL right?"
        ) from None

    if response.status_code == 401 and not credentials():
        # The API's own message for this is about its side of the wire. The
        # fix is on this side, in the environment this server was started
        # with, so that is what the message names.
        raise ApiError(
            "not signed in. Set VIDEOSYNX_TOKEN to a Supabase access token, "
            "or VIDEOSYNX_DEV_USER to an account on a local server that has "
            "no sign-in configured."
        )
    if response.status_code >= 400:
        raise ApiError(said(response))
    if not response.content:
        return None
    return response.json()
