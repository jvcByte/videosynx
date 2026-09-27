"""The timeline: a *plan*, not a recording.

Everything downstream derives from integer frame counts, so audio and video
cannot disagree. There is no wall clock anywhere in this module -- execution
happens before the plan is built, and nothing here depends on how long it took.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    caption: str | None = None


@dataclass(frozen=True)
class Caption:
    """A chunk of narration shown on screen, in absolute frame numbers."""

    text: str
    start_frame: int
    end_frame: int


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
    narration_text: str = ""


@dataclass(frozen=True)
class FramePlan:
    spans: tuple[Span, ...]
    narrations: tuple[Narration, ...]
    total_frames: int
    captions: tuple[Caption, ...] = ()

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


# Caption chunking: wide enough to take in at a glance, narrow enough to fit
# two lines on screen at 1080p.
CAPTION_MAX_CHARS = 42
CAPTION_MAX_WORDS = 8


def chunk_caption_text(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        # Close the current chunk *before* it would overflow, so a chunk only
        # exceeds the limit when a single word is longer than the whole limit.
        if current and (
            len(" ".join([*current, word])) > CAPTION_MAX_CHARS
            or len(current) >= CAPTION_MAX_WORDS
        ):
            chunks.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        chunks.append(" ".join(current))
    return chunks


def time_captions(text: str, start_frame: int, end_frame: int) -> list[Caption]:
    """Spread a narration's words across its exact frame span.

    Timing is proportional to character count -- an approximation of speaking
    rate, not forced alignment. ElevenLabs can return character-level
    timestamps, which would make this exact; until that is wired up this is at
    least deterministic and every caption lands inside a span of known length.
    """
    chunks = chunk_caption_text(text)
    span = end_frame - start_frame
    if not chunks or span <= 0:
        return []
    if span < len(chunks):
        # Too short to give each chunk its own frames without flicker.
        return [Caption(" ".join(chunks), start_frame, end_frame)]

    total = sum(len(c) for c in chunks)
    bounds, acc = [], 0
    for chunk in chunks[:-1]:
        acc += len(chunk)
        bounds.append(start_frame + round(span * acc / total))
    bounds.append(end_frame)

    captions: list[Caption] = []
    previous = start_frame
    for chunk, stop in zip(chunks, bounds):
        stop = min(max(stop, previous + 1), end_frame)
        captions.append(Caption(chunk, previous, stop))
        previous = stop
    return [c for c in captions if c.end_frame > c.start_frame]


def with_captions(spans: list[Span], captions: list[Caption]) -> list[Span]:
    """Split spans at caption boundaries and stamp each piece with its caption.

    Both lists are already in frame order, so this is a single forward pass
    rather than a nested scan.
    """
    if not captions:
        return list(spans)

    ranges, pos = [], 0
    for span in spans:
        ranges.append((pos, pos + span.n_frames, span.state))
        pos += span.n_frames
    if not ranges:
        return []

    points: set[int] = {0, pos}
    for start, end, _ in ranges:
        points.add(start)
        points.add(end)
    for caption in captions:
        points.add(caption.start_frame)
        points.add(caption.end_frame)
    ordered = sorted(p for p in points if 0 <= p <= pos)

    out: list[Span] = []
    span_i = caption_i = 0
    for start, end in zip(ordered, ordered[1:]):
        if end <= start:
            continue
        while span_i + 1 < len(ranges) and ranges[span_i][1] <= start:
            span_i += 1
        while caption_i < len(captions) and captions[caption_i].end_frame <= start:
            caption_i += 1
        state = ranges[span_i][2]
        caption = None
        if caption_i < len(captions) and captions[caption_i].start_frame <= start:
            caption = captions[caption_i].text
        out.append(Span(replace(state, caption=caption), end - start))
    return out


def build_plan(snippets: list[Snippet], narration_type: str = "after") -> FramePlan:
    """Build the complete frame plan for a tutorial.

    Each snippet starts from a blank editor, matching the engine's existing
    behaviour of writing a fresh file per snippet.
    """
    if not snippets:
        raise ValueError("Cannot build a plan with no snippets.")

    all_spans: list[Span] = []
    narrations: list[Narration] = []
    captions: list[Caption] = []
    cursor = 0

    for snippet in snippets:
        spans, narration, n_frames = plan_snippet(snippet, narration_type)
        if narration is not None:
            narrations.append(
                Narration(narration.samples, narration.start_frame + cursor)
            )
            if snippet.narration_text:
                start = cursor + narration.start_frame
                end = min(start + ceil(len(narration.samples) / SPF), cursor + n_frames)
                captions.extend(time_captions(snippet.narration_text, start, end))
        all_spans.extend(spans)
        cursor += n_frames

    return FramePlan(
        tuple(with_captions(all_spans, captions)),
        tuple(narrations),
        cursor,
        tuple(captions),
    )
