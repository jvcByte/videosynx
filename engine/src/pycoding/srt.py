"""SRT subtitle export.

Caption timings already exist as exact frame numbers, so the sidecar is a
pure format conversion -- no re-derivation, and it cannot drift from the
burned-in captions.
"""

from __future__ import annotations

from pathlib import Path

from .timeline import FPS, Caption


def timestamp(frame: int, fps: int = FPS) -> str:
    total_ms = round(frame * 1000 / fps)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def to_srt(captions: tuple[Caption, ...] | list[Caption], fps: int = FPS) -> str:
    blocks = [
        f"{i}\n{timestamp(c.start_frame, fps)} --> {timestamp(c.end_frame, fps)}\n{c.text}\n"
        for i, c in enumerate(captions, start=1)
    ]
    return "\n".join(blocks)


def write_srt(captions: tuple[Caption, ...] | list[Caption], path: Path | str,
              fps: int = FPS) -> Path | None:
    """Write *captions* beside the video. Returns None when there are none."""
    if not captions:
        return None
    path = Path(path)
    path.write_text(to_srt(captions, fps), encoding="utf-8")
    return path
