"""The videosynx tool layer, bound to MCP.

Ten tools, every one of them a wrapper over a route that already exists.
Nothing is decided here. Quota, credits and ownership are the API's, and
an agent that re-derived any of them would be a second copy of a rule --
right until the day the rule moves, and wrong from then on.

The tools say "videosynx" and not "Claude", on purpose. Two front ends are
meant to share them: a person's own assistant today (Claude Code, Claude
Desktop, anything speaking MCP), and the agent pane inside the product
later. Describing them twice is how the two would come to disagree about
what the product does.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import client

# Read by the model before it decides anything, so it is worth being
# explicit about the two facts that shape every call: nothing is
# synchronous, and one of these tools spends money.
INSTRUCTIONS = """\
videosynx turns a topic or a written script into a narrated explainer video
with matching subtitles. It can also make audio alone.

Rendering takes minutes, not seconds. `create_video` returns a job id as
soon as the work is queued and the render happens in the background, so use
`video_status` to check on it. A job that is still queued or running has not
failed; a job is only failed when it says so.

Creating a video spends. Each tier has a daily allowance of model-backed
jobs; past that, a video costs one credit per minute of finished video,
taken when the render completes. A render that fails, or that is cancelled,
is free. Call `my_account` when you do not know what is left.

Local mode calls no model at all -- it renders a script or lesson JSON you
supply, and costs nothing but an allowance of its own. A bare topic cannot
be rendered that way: writing the script is the one thing local mode will
not do.
"""


def _version() -> str:
    try:
        return _installed_version("videosynx-agent")
    except PackageNotFoundError:
        return "0"


def _job_id(value: str) -> str:
    """A job id, or an error. These end up in a URL path.

    Not suspicion of the model. An id that arrives mangled -- truncated out
    of a long conversation, or reproduced from memory -- would build a path
    like /jobs/../../health and quietly answer a different question. Saying
    so is more useful than the answer to that question.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ToolError(
            f"{value!r} is not a job id. Use the id create_video returned, "
            "or list_videos to find one."
        ) from None
    return value


def build() -> MCPServer:
    server = MCPServer(
        name="videosynx",
        title="videosynx",
        instructions=INSTRUCTIONS,
        version=_version(),
    )

    def call(method: str, path: str, **kwargs) -> object:
        """client.call, with a refusal turned into something readable.

        The MCP layer reports a ToolError as a failed tool call carrying
        its message, which a model reads and reacts to. Anything else is
        reported as a crash of the server, which it is not: an account
        being out of credits is an ordinary answer.
        """
        try:
            return client.call(method, path, **kwargs)
        except client.ApiError as exc:
            raise ToolError(str(exc)) from None

    # ------------------------------------------------------------ the account

    @server.tool()
    def my_account() -> dict:
        """What this account is and what it has left.

        The tier, the jobs remaining today, the credit balance and how much
        video has been made so far. Worth calling before submitting
        anything, because it is the only way to know whether the next video
        is covered by the daily allowance or will spend credits.
        """
        return {
            "subscription": call("GET", "/me/subscription"),
            "quota": call("GET", "/me/quota"),
        }

    # ------------------------------------------------------------ making one

    @server.tool()
    def create_video(
        topic: Annotated[
            str,
            Field(description="What to teach, e.g. 'Python generators'. May be "
                              "left out when a script is supplied instead."),
        ] = "",
        script: Annotated[
            str | None,
            Field(description="A finished narration script to render, used "
                              "verbatim in place of anything a model would "
                              "write. This is how to keep control of the "
                              "wording."),
        ] = None,
        mode: Annotated[
            Literal["both", "coding", "illustration"] | None,
            Field(description="'both' explains the idea and then writes code; "
                              "'coding' is code alone; 'illustration' is drawn "
                              "explanation with no code."),
        ] = None,
        generation: Annotated[
            Literal["auto", "local"] | None,
            Field(description="'local' promises never to call a model, so it "
                              "needs a script or a lesson and refuses a bare "
                              "topic. 'auto' is the normal choice."),
        ] = None,
    ) -> dict:
        """Queue a video and return its job id immediately.

        The render runs in the background and takes minutes. This does not
        wait for it and does not return the video -- poll `video_status`
        with the returned id, then `download_video` once it has succeeded.

        This is the tool that spends. Past the tier's daily allowance, each
        video costs one credit per minute of finished video, charged when
        the render completes; a render that fails or is cancelled is free.
        """
        body: dict[str, object] = {}
        if topic.strip():
            body["topic"] = topic.strip()
        if script:
            body["script"] = script
        if mode:
            body["mode"] = mode
        if generation:
            body["generation"] = generation
        return call("POST", "/jobs", json_body=body)

    @server.tool()
    def make_voiceover(
        text: Annotated[
            str,
            Field(description="What to read aloud. Read sentence by sentence "
                              "through the same voice the videos use."),
        ],
        voice: Annotated[
            str | None,
            Field(description="A voice id from list_voices. Left out, the "
                              "tier's default is used."),
        ] = None,
    ) -> dict:
        """Queue audio only -- narration with no video.

        Returns a job id, same as create_video: this is asynchronous too,
        and it is polled and downloaded the same way. Costs nothing beyond
        the local allowance, because it is synthesised on the worker rather
        than through a paid voice service.
        """
        body: dict[str, object] = {"text": text}
        if voice:
            body["voice"] = voice
        return call("POST", "/speech", json_body=body)

    # ------------------------------------------------------------ reading back

    @server.tool()
    def video_status(job_id: str) -> dict:
        """How one job is doing.

        `status` is one of queued, running, succeeded, failed or canceled,
        and while it is running `stage` and `progress` say how far along it
        is. A queued or running job has not failed -- check again rather
        than submitting it a second time.
        """
        return call("GET", f"/jobs/{_job_id(job_id)}")

    @server.tool()
    def list_videos(
        limit: Annotated[int, Field(description="How many to return, newest first.")] = 20,
    ) -> list:
        """Recent jobs on this account, newest first.

        Both videos and audio clips: `kind` on each says which. This is the
        way to find a job id that has been forgotten, or to see whether an
        earlier render already answered the same question.
        """
        return call("GET", "/jobs", params={"limit": max(1, min(limit, 100))})

    @server.tool()
    def read_script(job_id: str) -> dict:
        """The narration and diagrams a render actually produced.

        The generated code, the narration and every diagram are made fresh
        on each run and stored nowhere else, which makes this the only way
        to see what a video says without watching it -- and the way to find
        out why one came out wrong.
        """
        return call("GET", f"/jobs/{_job_id(job_id)}/script")

    @server.tool()
    def download_video(job_id: str) -> dict:
        """A time-limited URL for the finished file.

        Only for a job that has succeeded. The file is served by object
        storage rather than through the API, so this returns an address to
        fetch rather than the bytes. It expires, so fetch it when it is
        asked for rather than storing it.
        """
        return call("GET", f"/jobs/{_job_id(job_id)}/video")

    @server.tool()
    def list_voices() -> list:
        """The voices a narration can use, and which tiers may have them.

        Check this rather than inventing a voice name: an id that does not
        exist is rejected at submit, after the job has already been counted.
        """
        return call("GET", "/voices")

    # ------------------------------------------------------------ changing one

    @server.tool()
    def cancel_video(job_id: str) -> dict:
        """Stop a job that has not finished. Free, since nothing was made.

        Takes effect at the next checkpoint, which is about a second while
        frames are being written and up to twenty while the model or the
        voice is being called. A job that has already finished cannot be
        cancelled and says so.
        """
        return call("POST", f"/jobs/{_job_id(job_id)}/cancel")

    @server.tool()
    def retry_video(job_id: str) -> dict:
        """Put a job that failed back in the queue.

        Only a failed job can be retried, and retrying does not cost
        another allowance or another credit: it re-runs work that was
        already counted, and a failed render has produced nothing to
        charge for.
        """
        return call("POST", f"/jobs/{_job_id(job_id)}/retry")

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="videosynx-mcp",
        description="Speak MCP over stdio, so an assistant can drive videosynx.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report whether the API is reachable and who it thinks you are, "
             "then exit. This is the thing to run when a client shows no "
             "tools and it is not obvious why.",
    )
    args = parser.parse_args(argv)

    if args.check:
        return _check()

    # stdout is the protocol. Anything printed there that is not MCP is a
    # parse error at the client, so this server must never print.
    build().run("stdio")
    return 0


def _check() -> int:
    print(f"api: {client.base_url()}")
    headers = client.credentials()
    if "Authorization" in headers:
        print("auth: from VIDEOSYNX_TOKEN")
    elif headers:
        print("auth: from VIDEOSYNX_DEV_USER (development only)")
    else:
        print("auth: none set -- set VIDEOSYNX_TOKEN to a Supabase access token")

    try:
        account = client.call("GET", "/me/subscription")
    except client.ApiError as exc:
        print(f"result: {exc}")
        return 1

    print(f"result: signed in, tier {account.get('tier')}, "
          f"{account.get('credits')} credits, "
          f"{account.get('minutes_of_video')} minutes rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
