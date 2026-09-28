"""Rendering: turn a FramePlan into an MP4."""

from __future__ import annotations

from pathlib import Path

from ..timeline import FramePlan
from .editor import Editor
from .sink import SinkError, VideoSink
from .style import DEFAULT_STYLE, STYLES, Style, style_for

# Pinned explicitly rather than resolved through fontconfig: if the face is
# missing, Pillow silently falls back to a proportional font and the whole
# monospace layout collapses. Better to fail loudly.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf",
    "/home/jvcbyte/.local/share/fonts/JetBrainsMono/JetBrainsMonoNerdFont-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)

__all__ = ["Editor", "VideoSink", "SinkError", "render_plan", "find_font",
           "Style", "STYLES", "DEFAULT_STYLE", "style_for"]


def find_font() -> str:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    raise SinkError(
        "No monospace font found. Install one, or add its path to FONT_CANDIDATES. "
        f"Tried: {', '.join(FONT_CANDIDATES)}"
    )


def render_plan(
    plan: FramePlan,
    path: Path | str,
    *,
    width: int = 1920,
    height: int = 1080,
    font_path: str | None = None,
    filename: str = "snippet.py",
    style: str | Style | None = None,
    watermark: str | None = None,
) -> Path:
    """Render *plan* to an MP4 at *path*.

    Writes exactly one buffer per frame in the plan; the assertion at the end
    is what guarantees the video can never silently disagree with the audio.

    *style* names how concept slides are drawn (see :mod:`.style`). It accepts
    a name or a resolved Style, so a caller that already resolved one is not
    made to look it up twice.
    """
    path = Path(path)
    font_path = font_path or find_font()
    resolved = style if isinstance(style, Style) else style_for(style)
    audio = plan.build_audio()

    with VideoSink(path, width, height, audio=audio) as sink:
        editor = Editor(width, height, font_path, filename, style=resolved,
                        watermark=watermark)
        buf: bytes | None = None
        last_state = None
        for span in plan.spans:
            if span.state != last_state:
                editor.apply(span.state)
                buf = editor.tobytes()
                last_state = span.state
            for _ in range(span.n_frames):
                sink.write(buf)

    if sink.frames_written != plan.total_frames:
        raise SinkError(
            f"wrote {sink.frames_written} frames, plan specified {plan.total_frames}"
        )
    return path
