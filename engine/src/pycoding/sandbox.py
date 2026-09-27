"""Running untrusted code.

The pipeline executes Python written by an LLM, from a topic supplied by a
user. That is a prompt-injection path from an untrusted user to code execution
on the host, and on a shared worker it also sits next to the API keys.

So generated code runs inside bubblewrap with no network, no readable home
directory, no inherited environment, and bounded CPU/memory/output. One
function, one call site -- when this needs to become a container, nothing
outside this module changes.
"""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_TIMEOUT_S = 30
DEFAULT_CPU_S = 15
DEFAULT_MEMORY_MB = 2048
READ_CHUNK = 65536

# Bound read-only so the child can import the stdlib and site-packages but
# cannot write to them.
RO_BIND_ROOTS = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/alternatives")


class SandboxUnavailable(RuntimeError):
    """Raised when isolation cannot be established. Never fall back."""


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    truncated: bool
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def _venv_root() -> Path | None:
    """Return the virtualenv root if we are running inside one."""
    candidate = Path(sys.executable).parent.parent
    return candidate if (candidate / "pyvenv.cfg").exists() else None


def _interpreter() -> tuple[str, list[str]]:
    """Where the child's Python lives, and any mount needed to expose it.

    A virtualenv is mounted at /venv rather than at its real path, because the
    real path is usually under /home -- which is masked by a tmpfs, and bwrap
    applies mounts in order, so binding first and masking after hides it.
    """
    venv = _venv_root()
    if venv is not None:
        return "/venv/bin/python", ["--ro-bind", str(venv), "/venv"]
    return sys.executable, []


def _bwrap_command(code_path: Path, scratch: Path | None,
                   runner: Path | None = None) -> list[str]:
    argv = ["bwrap"]
    for root in RO_BIND_ROOTS:
        if Path(root).exists():
            argv += ["--ro-bind", root, root]

    # Masked before anything is bound inside them.
    argv += ["--tmpfs", "/tmp", "--tmpfs", "/home", "--tmpfs", "/root"]

    python, binds = _interpreter()
    argv += binds
    argv += ["--ro-bind", str(code_path), "/work/snippet.py"]
    if runner is not None:
        # Bound as a plain file: the package itself lives under /home, which is
        # masked, so the runner has to be self-contained.
        argv += ["--ro-bind", str(runner), "/work/_runner.py"]
    if scratch is not None:
        argv += ["--bind", str(scratch), "/work/scratch"]
    argv += ["--chdir", "/work"]
    argv += ["--proc", "/proc", "--dev", "/dev"]
    argv += ["--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    argv += [
        # --clearenv is what keeps API keys out of the child: it starts with a
        # four-variable environment and nothing else.
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/tmp",
        # Deletes the entire matplotlib-window problem: nothing tries to open a
        # display, so there is no window to hunt down and close.
        "--setenv", "MPLBACKEND", "Agg",
        "--setenv", "PYTHONHASHSEED", "0",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
    ]
    if runner is not None:
        argv += [python, "/work/_runner.py", "/work/snippet.py"]
    else:
        argv += [python, "/work/snippet.py"]
    return argv


def _limits(cpu_s: int, memory_mb: int):
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        # Generous address space: numpy reserves large virtual regions and a
        # tight RLIMIT_AS makes `import numpy` fail outright.
        memory = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        # Deliberately no RLIMIT_NPROC: it is per-UID, so one job hitting the
        # cap makes fork() fail in every other job running as the same user.
        # Process containment comes from --unshare-pid plus the wall timeout.

    return apply


def _drain(stream, cap: int, sink: list[bytes], state: dict) -> None:
    """Read a pipe, keeping at most *cap* bytes but always draining it."""
    total = 0
    try:
        while True:
            chunk = stream.read(READ_CHUNK)
            if not chunk:
                break
            if total < cap:
                sink.append(chunk[: cap - total])
            total += len(chunk)
    except Exception:
        pass
    finally:
        state["bytes"] = total
        try:
            stream.close()
        except Exception:
            pass


def run_untrusted(
    code: str,
    *,
    workdir: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    cpu_s: int = DEFAULT_CPU_S,
    memory_mb: int = DEFAULT_MEMORY_MB,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    filename: str = "snippet.py",
    runner: Path | None = None,
) -> ExecResult:
    """Run *code* in an isolated sandbox and capture its output.

    Raises:
        SandboxUnavailable: if bubblewrap is missing, or if the code path is
            unsafe to run unsandboxed. Never falls back to running it raw.
    """
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise SandboxUnavailable(
            "bubblewrap (bwrap) not found. Refusing to run generated code "
            "unsandboxed. Install with: sudo apt install bubblewrap"
        )

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    code_path = workdir / filename
    code_path.write_text(code, encoding="utf-8")
    scratch = workdir / "scratch"
    scratch.mkdir(exist_ok=True)

    argv = _bwrap_command(code_path, scratch, runner)

    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_limits(cpu_s, memory_mb),
        start_new_session=True,
        env={},  # belt and braces alongside --clearenv
    )

    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    out_state: dict = {}
    err_state: dict = {}
    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, max_output_bytes, out_chunks, out_state)),
        threading.Thread(target=_drain, args=(proc.stderr, max_output_bytes, err_chunks, err_state)),
    ]
    for thread in readers:
        thread.daemon = True
        thread.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Kill the whole process group: subprocess's timeout only signals the
        # direct child, so a snippet that forked would otherwise survive.
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()

    for thread in readers:
        thread.join(timeout=2)

    truncated = out_state.get("bytes", 0) > max_output_bytes or (
        err_state.get("bytes", 0) > max_output_bytes
    )
    return ExecResult(
        stdout=b"".join(out_chunks).decode("utf-8", "replace"),
        stderr=b"".join(err_chunks).decode("utf-8", "replace"),
        exit_code=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
        truncated=truncated,
        duration_s=time.monotonic() - started,
    )


_PROBE = r"""
import os, socket, sys
failures = []

if "PYCODING_SENTINEL" in os.environ:
    failures.append("environment was not cleared")

try:
    socket.create_connection(("1.1.1.1", 80), timeout=5).close()
    failures.append("network is reachable")
except Exception:
    pass

# A masked directory is an empty tmpfs, so listing it succeeding is correct
# and means nothing. What must fail is reading an actual secret file.
for path in ("/etc/shadow", "/root/.bashrc", "/root/.ssh/id_rsa",
             os.path.expanduser("~/.ssh/id_rsa")):
    try:
        open(path, "rb").read(16)
        failures.append("readable: " + path)
    except Exception:
        pass

if failures:
    print("; ".join(failures), file=sys.stderr)
    sys.exit(1)
"""


def assert_sandbox_available(timeout_s: int = 30) -> None:
    """Verify isolation actually holds on this host, or refuse to start.

    Isolation depends on kernel, host and container runtime. ``unshare -n``
    fails outright on some hosts even though bubblewrap works, so this is
    checked rather than assumed. Raises SandboxUnavailable on any failure --
    there is deliberately no unsandboxed fallback.
    """
    if shutil.which("bwrap") is None:
        raise SandboxUnavailable(
            "bubblewrap (bwrap) not found. Install with: sudo apt install bubblewrap"
        )

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        environ_before = os.environ.get("PYCODING_SENTINEL")
        os.environ["PYCODING_SENTINEL"] = "leaked"
        try:
            result = run_untrusted(_PROBE, workdir=workdir, timeout_s=timeout_s)
        finally:
            if environ_before is None:
                os.environ.pop("PYCODING_SENTINEL", None)
            else:
                os.environ["PYCODING_SENTINEL"] = environ_before

    if result.timed_out:
        raise SandboxUnavailable("sandbox probe timed out")
    if result.exit_code != 0:
        raise SandboxUnavailable(
            "sandbox isolation failed: " + (result.stderr.strip() or "unknown reason")
        )
