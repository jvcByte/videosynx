"""The runnable check for isolation.

Each test asserts one guarantee the pipeline relies on before it will execute
LLM-generated code. If any of these regress, the worker must refuse to start.
"""

from __future__ import annotations

import os

import pytest

from pycoding import sandbox
from pycoding.sandbox import SandboxUnavailable, assert_sandbox_available, run_untrusted


@pytest.fixture
def workdir(tmp_path):
    return tmp_path / "job"


def run(code, workdir, **kwargs):
    kwargs.setdefault("timeout_s", 20)
    return run_untrusted(code, workdir=workdir, **kwargs)


# ------------------------------------------------------------- prerequisites

def test_isolation_holds_on_this_host():
    """The gate the worker calls at boot. Must raise, not warn, when it fails."""
    assert_sandbox_available()


def test_missing_bwrap_refuses_to_run(monkeypatch, workdir):
    """No bubblewrap must mean no execution — never an unsandboxed fallback."""
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    with pytest.raises(SandboxUnavailable):
        run("print('should never run')", workdir)
    with pytest.raises(SandboxUnavailable):
        assert_sandbox_available()


# ---------------------------------------------------------------- guarantees

def test_network_is_unreachable(workdir):
    result = run(
        "import socket\n"
        "socket.create_connection(('1.1.1.1', 80), timeout=5)\n",
        workdir,
    )
    assert not result.ok
    assert "Network is unreachable" in result.stderr or "OSError" in result.stderr


def test_secrets_are_unreadable(workdir):
    result = run(
        "for p in ('/etc/shadow', '/root/.bashrc'):\n"
        "    open(p).read()\n",
        workdir,
    )
    assert not result.ok
    assert "PermissionError" in result.stderr or "FileNotFoundError" in result.stderr


def test_environment_is_cleared(monkeypatch, workdir):
    """API keys must not be reachable from inside, even with full filesystem access."""
    monkeypatch.setenv("GEMINI_API_KEY", "sentinel-value")
    result = run(
        "import os, sys\n"
        "leaked = [k for k in os.environ if 'KEY' in k or 'TOKEN' in k]\n"
        "print('LEAKED:' + ','.join(leaked))\n"
        "sys.exit(1 if leaked else 0)\n",
        workdir,
    )
    assert result.ok, result.stderr
    assert "LEAKED:" in result.stdout and result.stdout.strip().endswith("LEAKED:")


def test_home_directory_is_masked(workdir):
    result = run(
        "import os\n"
        "print('HOME=' + os.environ['HOME'])\n"
        "print(sorted(os.listdir(os.environ['HOME'])))\n",
        workdir,
    )
    assert result.ok, result.stderr
    assert "HOME=/tmp" in result.stdout


# ------------------------------------------------------------------- limits

def test_infinite_loop_is_killed_by_the_wall_clock(workdir):
    result = run("while True:\n    pass\n", workdir, timeout_s=5)
    assert result.timed_out
    assert result.duration_s < 20, "kill took far longer than the timeout"


def test_runaway_allocation_fails_without_killing_the_worker(workdir):
    """The job dies; the process running it must not."""
    result = run(
        "bytearray(4 * 1024 * 1024 * 1024)\n",
        workdir,
        memory_mb=512,
    )
    assert not result.ok
    assert "MemoryError" in result.stderr


def test_output_flood_is_capped(workdir):
    """A snippet printing unboundedly must not be able to exhaust worker memory."""
    result = run(
        "for _ in range(20000):\n"
        "    print('x' * 1000)\n",
        workdir,
        timeout_s=60,
    )
    assert result.truncated, "cap was not reported"
    assert len(result.stdout) <= sandbox.MAX_OUTPUT_BYTES + 4096


def test_writes_outside_the_scratch_dir_fail(workdir):
    result = run(
        "open('/work/snippet.py', 'w').write('tampered')\n",
        workdir,
    )
    assert not result.ok
    assert "Read-only file system" in result.stderr or "PermissionError" in result.stderr


# ------------------------------------------------------------------ success

def test_normal_snippet_runs_and_returns_output(workdir):
    result = run(
        "evens = [x for x in range(10) if x % 2 == 0]\n"
        "print(evens)\n",
        workdir,
    )
    assert result.ok, result.stderr
    assert result.stdout.strip() == "[0, 2, 4, 6, 8]"


def test_error_output_is_captured_separately(workdir):
    result = run("print('before')\nraise ValueError('boom')\n", workdir)
    assert not result.ok
    assert "before" in result.stdout
    assert "ValueError: boom" in result.stderr
    assert "Traceback" in result.stderr


# --------------------------------------------------------------- transcript

def test_transcript_reads_like_a_terminal(workdir):
    from pycoding.execute import run_snippet

    execution = run_snippet("print('hello')\nraise ValueError('boom')\n", workdir=workdir)
    assert execution.lines[0] == "$ python snippet.py"
    assert "hello" in execution.lines
    assert any("ValueError: boom" in line for line in execution.lines)
    assert not execution.ok


def test_transcript_truncates_long_output_to_the_tail():
    from pycoding.execute import MAX_LINES, transcript
    from pycoding.sandbox import ExecResult

    result = ExecResult(
        stdout="\n".join(f"line {i}" for i in range(60)),
        stderr="", exit_code=0, timed_out=False, truncated=False, duration_s=0.1,
    )
    lines = transcript(result)
    assert len(lines) == MAX_LINES
    assert lines[0] == "$ python snippet.py"
    assert "lines omitted" in lines[1]
    assert lines[-1] == "line 59", "the tail must survive truncation"
