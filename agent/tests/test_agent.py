"""What the agent layer promises, checked with no server running.

The tools are thin HTTP calls, so nearly everything that can go wrong is
in the shape of the request rather than in logic: a credential never sent,
an id that builds the wrong path, a refusal the model cannot read. Those
are what is checked here. Whether a render actually happens is the API's
question, and its own tests answer it.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from videosynx_agent import client, server

JOB = "3f1c9d2e-0a4b-4c6d-8e2f-1a2b3c4d5e6f"

EXPECTED_TOOLS = {
    "my_account",
    "create_video",
    "make_voiceover",
    "video_status",
    "list_videos",
    "read_script",
    "download_video",
    "list_voices",
    "cancel_video",
    "retry_video",
}


def fake_api(monkeypatch, handler, **env) -> list[httpx.Request]:
    """A stand-in API, and the log of what it was asked.

    The environment is cleared first so a developer's own exported token
    cannot change what these tests observe.
    """
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    for key in ("VIDEOSYNX_TOKEN", "VIDEOSYNX_DEV_USER", "VIDEOSYNX_API_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VIDEOSYNX_API_URL", "http://api.test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(record))
    return seen


def ok(body: object = None):
    return lambda request: httpx.Response(200, json=body if body is not None else {})


def tool(name: str, arguments: dict) -> object:
    return asyncio.run(server.build().call_tool(name, arguments))


def test_every_tool_is_discoverable_with_a_description():
    """A tool a client cannot describe is a tool the model cannot choose."""
    tools = asyncio.run(server.build().list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for found in tools:
        assert found.description and len(found.description) > 40, found.name
        assert found.input_schema["type"] == "object", found.name


def test_a_job_id_reaches_the_path_that_names_it(monkeypatch):
    seen = fake_api(monkeypatch, ok(), VIDEOSYNX_TOKEN="tok")
    tool("video_status", {"job_id": JOB})
    assert len(seen) == 1
    assert seen[0].url.path == f"/jobs/{JOB}"
    # The credential travels as a bearer token when one is configured.
    assert seen[0].headers["authorization"] == "Bearer tok"


def test_the_development_header_is_used_only_without_a_token(monkeypatch):
    """Sending both would say two different things about one caller."""
    seen = fake_api(monkeypatch, ok(), VIDEOSYNX_DEV_USER="web-user:free")
    tool("list_videos", {})
    assert seen[0].headers["x-dev-user"] == "web-user:free"
    assert "authorization" not in seen[0].headers


def test_an_empty_api_url_falls_back_instead_of_becoming_no_host(monkeypatch):
    """What an unset ${VIDEOSYNX_API_URL} in a client's config actually sends."""
    monkeypatch.setenv("VIDEOSYNX_API_URL", "")
    assert client.base_url() == client.DEFAULT_URL

    seen = fake_api(monkeypatch, ok(), VIDEOSYNX_TOKEN="tok")
    monkeypatch.setenv("VIDEOSYNX_API_URL", "")
    tool("list_videos", {})
    assert str(seen[0].url).startswith(f"{client.DEFAULT_URL}/jobs")


def test_an_id_that_is_not_one_never_reaches_the_wire(monkeypatch):
    """Otherwise it builds /jobs/../../health and answers a different question."""
    seen = fake_api(monkeypatch, ok(), VIDEOSYNX_TOKEN="tok")
    with pytest.raises(ToolError):
        tool("video_status", {"job_id": "../../health"})
    assert seen == []


def test_create_video_leaves_out_what_was_not_asked_for(monkeypatch):
    """An omitted setting must stay omitted, never be filled in with a default.

    The API merges this body over the account's saved settings. Sending
    `generation: "auto"` because nothing was said about it would therefore
    overwrite a saved "local" -- quietly turning "never call a model" into
    "may spend", which is the one preference that exists to prevent exactly
    that. Same for a null mode, or an empty topic.
    """
    seen = fake_api(monkeypatch, ok({"id": JOB, "status": "queued"}),
                    VIDEOSYNX_TOKEN="tok")

    tool("create_video", {"topic": "Python generators"})
    assert json.loads(seen[0].content) == {"topic": "Python generators"}

    # The second call is the one that catches a body built unconditionally:
    # it has no topic, so a stray `"topic": ""` shows up here and nowhere else.
    tool("create_video", {"script": "### Heading\n\nProse.", "generation": "local"})
    assert json.loads(seen[1].content) == {
        "script": "### Heading\n\nProse.", "generation": "local"}


def test_a_lesson_object_is_passed_through_untouched(monkeypatch):
    """An agent authoring a diagram sends structure, not escaped JSON.

    The tool must not reshape it on the way: the shapes, the arrows and the
    reveal order are the whole content, and a wrapper that "helpfully"
    normalised any of them would be redrawing someone else's diagram.
    """
    seen = fake_api(monkeypatch, ok({"id": JOB, "status": "queued"}),
                    VIDEOSYNX_TOKEN="tok")
    lesson = {
        "title": "How a loop runs",
        "sections": [{
            "name": "Cycle",
            "explain": {
                "heading": "One pass round the loop",
                "diagram": {
                    "layout": "cycle",
                    "nodes": [{"id": "i", "label": "item"}, {"id": "b", "label": "body"}],
                    "edges": [["i", "b"], ["b", "i"]],
                },
                "sequence": ["i", "b"],
            },
            "narration": ["Take the next item.", "Run the body."],
        }],
    }
    tool("create_video", {"lesson": lesson})
    assert json.loads(seen[0].content) == {"lesson": lesson}


def test_a_refusal_is_reported_in_the_services_own_words(monkeypatch):
    """The quota refusal carries the numbers, and the model needs them."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": {
            "error": "quota_exceeded", "tier": "free", "kind": "model",
            "limit": 3, "used": 3,
        }})

    fake_api(monkeypatch, refuse, VIDEOSYNX_TOKEN="tok")
    with pytest.raises(ToolError) as refused:
        tool("create_video", {"topic": "Python generators"})
    message = str(refused.value)
    assert "quota_exceeded" in message and '"limit": 3' in message


def test_an_unreachable_api_says_where_it_looked(monkeypatch):
    """The likeliest failure there is, and the one a traceback explains least."""

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    fake_api(monkeypatch, down, VIDEOSYNX_TOKEN="tok")
    with pytest.raises(ToolError) as unreachable:
        tool("list_videos", {})
    assert "http://api.test" in str(unreachable.value)
    assert "VIDEOSYNX_API_URL" in str(unreachable.value)


def test_a_missing_credential_names_the_variable_that_fixes_it(monkeypatch):
    """No token and no dev user: the API's 401 is about its side of the wire."""
    fake_api(monkeypatch, lambda request: httpx.Response(401, json={"detail": "missing bearer token"}))
    with pytest.raises(ToolError) as refused:
        tool("my_account", {})
    assert "VIDEOSYNX_TOKEN" in str(refused.value)
