"""The timeline: a *plan*, not a recording.

Everything downstream derives from integer frame counts, so audio and video
cannot disagree. There is no wall clock anywhere in this module -- execution
happens before the plan is built, and nothing here depends on how long it took.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import numpy as np

# --- The frame grid -------------------------------------------------------
# 48000 / 30 == 1600 exactly, so every frame maps to a whole number of audio
# samples and the sync path stays integer-only. Changing FPS without keeping
# SR % FPS == 0 would silently reintroduce rounding.
FPS = 30
SR = 48000
SPF = SR // FPS  # samples per frame -- 1600, exact

# --- Timing (all in integer frames) --------------------------------------
FRAMES_PER_CHAR = 3  # 100 ms/char — a deliberate, readable typing cadence
OUT_LINE_FRAMES = 4  # 133 ms per output line revealed
POST_PAD_FRAMES = 30  # 1 s of breathing room after each snippet
MIN_HOLD_FRAMES = 1  # a span is never zero-length


@dataclass(frozen=True)
class VisualState:
    """What the editor looks like at one moment.

    Immutable so the renderer can cheaply compare states to find what changed.
    """

    code: str
    output: tuple[str, ...] = ()


@dataclass(frozen=True)
class Span:
    """A visual state held for an integer number of frames."""

    state: VisualState
    n_frames: int


@dataclass(frozen=True)
class Narration:
    """Narration audio placed at an exact frame offset."""

    samples: np.ndarray
    start_frame: int


@dataclass(frozen=True)
class Snippet:
    """One unit of the tutorial: code, its captured output, its narration."""

    code: str
    output: tuple[str, ...]
    narration: np.ndarray


@dataclass(frozen=True)
class FramePlan:
    spans: tuple[Span, ...]
    narrations: tuple[Narration, ...]
    total_frames: int

    @property
    def duration_s(self) -> float:
        return self.total_frames / FPS

    def build_audio(self) -> np.ndarray:
        """Render the full narration track as int16 PCM.

        Length is exactly ``total_frames * SPF`` by construction, so the audio
        and video streams are the same length and neither needs truncating.
        """
        track = np.zeros(self.total_frames * SPF, dtype=np.int32)
        for n in self.narrations:
            off = n.start_frame * SPF
            if off >= len(track):
                continue
            chunk = n.samples[: len(track) - off]
            track[off : off + len(chunk)] += chunk
        # Clip rather than wrap — overlapping narration must not alias into noise.
        return np.clip(track, -32768, 32767).astype(np.int16)


def plan_snippet(
    snippet: Snippet, narration_type: str = "after"
) -> tuple[list[Span], Narration | None, int]:
    """Build the spans for one snippet.

    Returns (spans, narration, n_frames). ``cursor`` is the frame index of the
    span list's first frame, used to place the narration.
    """
    if narration_type not in ("after", "parallel"):
        raise ValueError(f"narration_type must be 'after' or 'parallel', got {narration_type!r}")

    spans: list[Span] = []

    # Typing: one span per character, plus the initial empty state.
    for i in range(len(snippet.code) + 1):
        spans.append(Span(VisualState(snippet.code[:i]), FRAMES_PER_CHAR))

    # Output reveal: one span per captured line.
    for j in range(1, len(snippet.output) + 1):
        spans.append(
            Span(VisualState(snippet.code, snippet.output[:j]), OUT_LINE_FRAMES)
        )

    body_frames = sum(s.n_frames for s in spans)
    narr_frames = ceil(len(snippet.narration) / SPF)  # the only rounding, and it rounds up

    if narration_type == "parallel":
        # Narration starts as typing begins; the segment runs until both finish.
        hold = max(MIN_HOLD_FRAMES, narr_frames + POST_PAD_FRAMES - body_frames)
        start_frame = 0
    else:
        # Narration starts once typing and output are done.
        hold = max(MIN_HOLD_FRAMES, narr_frames + POST_PAD_FRAMES)
        start_frame = body_frames

    # Hold the final visual state while the narration plays out.
    spans.append(Span(spans[-1].state, hold))

    narration = (
        Narration(snippet.narration, start_frame) if len(snippet.narration) else None
    )
    return spans, narration, body_frames + hold


def build_plan(snippets: list[Snippet], narration_type: str = "after") -> FramePlan:
    """Build the complete frame plan for a tutorial.

    Each snippet starts from a blank editor, matching the engine's existing
    behaviour of writing a fresh file per snippet.
    """
    if not snippets:
        raise ValueError("Cannot build a plan with no snippets.")

    all_spans: list[Span] = []
    narrations: list[Narration] = []
    cursor = 0

    for snippet in snippets:
        spans, narration, n_frames = plan_snippet(snippet, narration_type)
        if narration is not None:
            narrations.append(
                Narration(narration.samples, narration.start_frame + cursor)
            )
        all_spans.extend(spans)
        cursor += n_frames

    return FramePlan(tuple(all_spans), tuple(narrations), cursor)
