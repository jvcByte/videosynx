"""Run a snippet and turn what happened into a terminal transcript.

The transcript is what the renderer draws in the terminal panel, so it has to
read like a real terminal session rather than like captured streams.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .sandbox import DEFAULT_TIMEOUT_S, ExecResult, run_untrusted

MAX_LINES = 18  # roughly what the terminal panel can show at 1080p
COMMAND = "$ python snippet.py"


@dataclass(frozen=True)
class Execution:
    lines: tuple[str, ...]
    ok: bool


def transcript(result: ExecResult, command: str = COMMAND) -> list[str]:
    """Build the terminal transcript. Errors stay last, where they are read."""
    lines = [command]

    stdout = result.stdout.rstrip("\n")
    if stdout:
        lines.extend(stdout.split("\n"))

    stderr = result.stderr.rstrip("\n")
    if stderr:
        lines.extend(stderr.split("\n"))

    if result.timed_out:
        lines.append(f"TimeoutError: exceeded {DEFAULT_TIMEOUT_S}s wall clock")
    elif result.truncated:
        lines.append("... output truncated")

    if len(lines) > MAX_LINES:
        # The tail is what matters -- a traceback ends there.
        omitted = len(lines) - MAX_LINES
        lines = lines[:1] + [f"... {omitted} lines omitted ..."] + lines[-(MAX_LINES - 2):]
    return lines


def run_snippet(
    code: str,
    *,
    workdir: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Execution:
    """Execute *code* in the sandbox and return its terminal transcript."""
    result = run_untrusted(code, workdir=workdir, timeout_s=timeout_s)
    return Execution(lines=tuple(transcript(result)), ok=result.ok)
