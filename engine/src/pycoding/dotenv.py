"""Load a .env file, so credentials do not have to be exported every run.

Deliberately not a dependency: this reads the subset of the format that
matters -- KEY=value, optional quotes, # comments -- and stops.

Existing environment variables win. A value exported in the shell is an
explicit instruction for this run; a .env file is a stored default, and a
stored default must not override what the caller just said.
"""

from __future__ import annotations

import os
from pathlib import Path

LOADED: list[str] = []


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        # Strip one layer of matching quotes, so a value containing spaces or
        # a trailing # survives.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load(path: Path | str = ".env", *, override: bool = False) -> list[str]:
    """Apply *path* to the environment. Returns the names that were set."""
    file = Path(path)
    if not file.is_file():
        return []

    applied = []
    for key, value in parse(file.read_text(encoding="utf-8")).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    if applied:
        LOADED.extend(applied)
    return applied
