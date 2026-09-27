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

# --- Typing rhythm --------------------------------------------------------
# Typing is not uniform: people pause at the end of a line and after
# punctuation, and rattle through runs of spaces. Each character state gets a
# weight, and the segment's total frames are shared out in proportion.
WEIGHT_START = 0.5      # the brief empty editor before the first keystroke
WEIGHT_NORMAL = 1.0
WEIGHT_SPACE = 0.6      # runs of indentation go by quickly
WEIGHT_PUNCT = 2.0      # a beat after a comma or a colon
WEIGHT_LINE_END = 4.0   # the pause at the end of a line
FRAMES_PER_WEIGHT = 3.0       # ~100 ms per character at a normal pace
MIN_FRAMES_PER_WEIGHT = 2.2   # ~13 chars/sec: fastest that still reads as typing
MAX_FRAMES_PER_WEIGHT = 8.0   # ~4 chars/sec: slowest we will stretch to
INTRO_TAIL_FRAMES = 45        # 1.5 s on the title card after the voice stops

OUT_LINE_FRAMES = 4  # 133 ms per output line revealed
POST_PAD_FRAMES = 30  # 1 s of breathing room after each snippet
MIN_HOLD_FRAMES = 1  # a span is never zero-length
CAPTION_MAX_CHARS = 42
CAPTION_MAX_WORDS = 8
# A pause between sentences reads as thinking; a long one reads as a stall.
MAX_GAP_FRAMES = 45  # 1.5 s


@dataclass(frozen=True)
class Phrase:
    """One spoken sentence, with the audio it was synthesised into.

    Narration is synthesised a sentence at a time rather than as one block,
    because that is what makes caption timing exact: each caption spans the
    frames of the audio it was actually spoken over, instead of being
    proportioned by character count across a whole paragraph.

    ``anchor`` is the character offset in the code this sentence is *about*.
    The sentence is spoken while that part of the code is on screen. Without
    it, narration drifts out of step with the code: an opening line about
    list comprehensions in general plays over ``numbers = range(15)`` being
    typed, and the viewer has nothing to attach it to. ``None`` spreads the
    sentences evenly across the code, which is the right default when nobody
    has said what each one refers to.
    """

    text: str
    samples: np.ndarray
    anchor: int | None = None

    @property
    def n_frames(self) -> int:
        return ceil(len(self.samples) / SPF)


@dataclass(frozen=True)
class VisualState:
    """What the screen looks like at one moment.

    Immutable so the renderer can cheaply compare states to find what changed.
    When ``title`` is set the renderer shows a title card instead of the
    editor, which is how the video opens: a viewer arriving cold needs to be
    told what the video is about before any code starts moving.
    """

    code: str
    output: tuple[str, ...] = ()
    caption: str | None = None
    title: str | None = None


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
class Intro:
    """The opening title card, and what is said over it."""

    title: str
    phrases: tuple[Phrase, ...] = ()

    @property
    def subtitle(self) -> str:
        return "a pycoding tutorial"


@dataclass(frozen=True)
class Snippet:
    """One unit of the tutorial: code, its captured output, its narration."""

    code: str
    output: tuple[str, ...] = ()
    phrases: tuple[Phrase, ...] = ()

    @property
    def narration_text(self) -> str:
        return " ".join(p.text for p in self.phrases)


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


# --------------------------------------------------------------- allocation

def split_evenly(total: int, parts: int) -> list[int]:
    """Split *total* into *parts* integers differing by at most one.

    Zero-length parts are allowed and mean "several changes land in one frame",
    which is what allows typing faster than one character per frame without
    ever leaving the integer grid.
    """
    if parts <= 0:
        return []
    base, extra = divmod(total, parts)
    return [base + 1] * extra + [base] * (parts - extra)


def weighted_split(total: int, weights: list[float]) -> list[int]:
    """Share *total* out in proportion to *weights*, preserving the total exactly."""
    if not weights:
        return []
    if total <= 0:
        return [0] * len(weights)
    scale = sum(weights) or 1.0
    exact = [total * w / scale for w in weights]
    counts = [int(v) for v in exact]
    # Hand the leftovers to whichever parts were cut hardest, so the sum is exact.
    shortfall = total - sum(counts)
    for i in sorted(range(len(exact)), key=lambda i: exact[i] - counts[i], reverse=True)[:shortfall]:
        counts[i] += 1
    return counts


def typing_weights(code: str) -> list[float]:
    """Per-state typing weights — one entry per visible character state."""
    weights = [WEIGHT_START]
    for ch in code:
        if ch == "\n":
            weights.append(WEIGHT_LINE_END)
        elif ch in ",;:":
            weights.append(WEIGHT_PUNCT)
        elif ch == " ":
            weights.append(WEIGHT_SPACE)
        else:
            weights.append(WEIGHT_NORMAL)
    return weights


# ---------------------------------------------------------------- captions

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
    """Spread one sentence's words across the frames it was spoken over.

    Within a single sentence the speaking rate is even enough that character
    proportion is a good approximation. Across a whole paragraph it is not,
    which is why captions are timed per phrase rather than per narration.
    """
    chunks = chunk_caption_text(text)
    span = end_frame - start_frame
    if not chunks or span <= 0:
        return []
    if span < len(chunks):
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

    Both lists are already in frame order, so this is a single forward pass.
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


# ------------------------------------------------------------------ layout

def resolve_anchors(phrases: tuple[Phrase, ...], code_len: int) -> list[int]:
    """Character offsets for each phrase, explicitly given or evenly spread."""
    if not phrases:
        return []
    given = [p.anchor for p in phrases]
    if all(a is None for a in given):
        # Nobody said what each sentence refers to, so spread them over the code.
        step = code_len / len(phrases)
        return [min(code_len, int(round(i * step))) for i in range(len(phrases))]

    anchors = [
        min(max(a if a is not None else 0, 0), code_len) for a in given
    ]
    # An anchor must move forward, or two sentences land on the same keystroke.
    for i in range(1, len(anchors)):
        anchors[i] = max(anchors[i], anchors[i - 1])
    return anchors


def anchor_frame(anchor: int, cum_weights: list[float], typing_total: int) -> int:
    """The frame at which typing reaches *anchor*, given the cumulative weights."""
    total = cum_weights[-1] or 1.0
    return int(round(typing_total * cum_weights[min(anchor, len(cum_weights) - 1)] / total))


def typing_total_for(
    phrases: tuple[Phrase, ...], anchors: list[int], cum_weights: list[float],
    natural: float, floor: float, ceiling: float,
) -> int:
    """How long typing should take, given where the sentences are anchored.

    Sentences are *not* forced to finish before the next anchor is reached --
    doing that drags typing down to a crawl whenever a sentence is longer than
    its stretch of code (measured at 4.5 chars/sec). Instead a sentence may
    overrun, and the next one starts a beat late. The only adjustment is
    enough slack for the final sentence to fit, so the voice is not still
    talking over a finished screen.
    """
    total_weight = cum_weights[-1] or 1.0
    target = min(max(natural, floor), ceiling)

    if phrases:
        fraction = cum_weights[anchors[-1]] / total_weight
        if fraction < 1:
            # The last sentence starts when typing reaches its anchor and must
            # still have time to play: T >= T*fraction + duration.
            needed = phrases[-1].n_frames / (1 - fraction)
            target = min(max(target, needed), ceiling)

    for _ in range(3):
        starts, lengths = place_phrases(phrases, anchors, cum_weights, int(round(target)))
        last_end = max((s + l for s, l in zip(starts, lengths)), default=0)
        if last_end <= target or target >= ceiling:
            break
        target = min(last_end, ceiling)
    return int(round(target))


def place_phrases(
    phrases: tuple[Phrase, ...], anchors: list[int],
    cum_weights: list[float], typing_total: int,
) -> tuple[list[int], list[int]]:
    """Where each phrase starts, in frames.

    A phrase starts when typing reaches its anchor, or as soon as the previous
    one finishes -- whichever is later. That non-overlap rule is what makes a
    sentence that overruns push the next one rather than talk over it.
    """
    lengths = [p.n_frames for p in phrases]
    starts: list[int] = []
    previous_end = 0
    for i in range(len(phrases)):
        desired = anchor_frame(anchors[i], cum_weights, typing_total)
        start = max(desired, previous_end)
        starts.append(start)
        previous_end = start + lengths[i]
    return starts, lengths


def lay_out_phrases(
    phrases: tuple[Phrase, ...], anchors: list[int],
    cum_weights: list[float], typing_total: int,
) -> tuple[np.ndarray, list[int], list[int]]:
    """Place each phrase so it plays while the code it describes is on screen.

    Returns (audio, starts, lengths) in snippet-relative frames. Each phrase's
    audio is padded to a whole frame so the next one starts exactly where the
    captions say it does.
    """
    if not phrases:
        return np.zeros(0, dtype=np.int16), [], []

    starts, lengths = place_phrases(phrases, anchors, cum_weights, typing_total)

    parts: list[np.ndarray] = []
    cursor = 0
    for i, phrase in enumerate(phrases):
        if starts[i] > cursor:
            parts.append(np.zeros((starts[i] - cursor) * SPF, dtype=np.int16))
            cursor = starts[i]
        parts.append(phrase.samples)
        padding = lengths[i] * SPF - len(phrase.samples)
        if padding > 0:
            parts.append(np.zeros(padding, dtype=np.int16))
        cursor += lengths[i]
    return np.concatenate(parts), starts, lengths


def plan_intro(
    intro: Intro, tail_frames: int = INTRO_TAIL_FRAMES
) -> tuple[list[Span], Narration | None, list[Caption], int]:
    """Build the title card that opens the video.

    The card holds for the length of its narration plus a beat, so the first
    snippet does not start the instant the narrator stops.
    """
    if not intro.phrases:
        return [], None, [], 0

    # No code to anchor to on the card, so the sentences run back to back.
    audio, starts, lengths = lay_out_phrases(
        intro.phrases, [0] * len(intro.phrases), [0.0, 1.0], 0
    )
    captions = [
        caption
        for phrase, start, length in zip(intro.phrases, starts, lengths)
        for caption in time_captions(phrase.text, start, start + length)
    ]
    total = sum(lengths) + tail_frames

    edges = sorted({0, total} | {c.start_frame for c in captions}
                   | {c.end_frame for c in captions})
    spans: list[Span] = []
    for start, end in zip(edges, edges[1:]):
        if end <= start:
            continue
        caption = next(
            (c.text for c in captions if c.start_frame <= start < c.end_frame), None
        )
        spans.append(Span(VisualState("", caption=caption, title=intro.title), end - start))

    return spans, Narration(audio, 0), captions, total


def plan_snippet(
    snippet: Snippet, narration_type: str = "after", pace: str = "narration"
) -> tuple[list[Span], Narration | None, list[Caption], int]:
    """Build the spans, narration and captions for one snippet.

    Returns (spans, narration, captions, n_frames) with captions in
    snippet-relative frames.
    """
    if narration_type not in ("after", "parallel"):
        raise ValueError(f"narration_type must be 'after' or 'parallel', got {narration_type!r}")
    if pace not in ("narration", "fixed"):
        raise ValueError(f"pace must be 'narration' or 'fixed', got {pace!r}")

    code = snippet.code
    weights = typing_weights(code)
    total_weight = sum(weights) or 1.0
    cumulative = [0.0] * (len(weights) + 1)
    for i, weight in enumerate(weights):
        cumulative[i + 1] = cumulative[i] + weight

    out_frames = len(snippet.output) * OUT_LINE_FRAMES
    anchored = pace == "narration" and bool(snippet.phrases)
    # Without anchors the sentences just run in order, so "after" still means
    # what it always meant: narration once the typing and output are done.
    anchors = (
        resolve_anchors(snippet.phrases, len(code))
        if anchored
        else [0] * len(snippet.phrases)
    )

    if anchored:
        typing_total = typing_total_for(
            snippet.phrases, anchors, cumulative,
            natural=total_weight * FRAMES_PER_WEIGHT,
            floor=total_weight * MIN_FRAMES_PER_WEIGHT,
            ceiling=total_weight * MAX_FRAMES_PER_WEIGHT,
        )
    else:
        typing_total = int(round(total_weight * FRAMES_PER_WEIGHT))

    counts = weighted_split(typing_total, weights)
    spans: list[Span] = [
        Span(VisualState(code[:i]), n) for i, n in enumerate(counts)
    ]
    for j in range(1, len(snippet.output) + 1):
        spans.append(Span(VisualState(code, snippet.output[:j]), OUT_LINE_FRAMES))

    body_frames = sum(s.n_frames for s in spans)
    audio, starts, lengths = lay_out_phrases(
        snippet.phrases, anchors, cumulative, typing_total
    )
    spoken_end = max((s + l for s, l in zip(starts, lengths)), default=0)

    captions = [
        caption
        for phrase, start, length in zip(snippet.phrases, starts, lengths)
        for caption in time_captions(phrase.text, start, start + length)
    ]

    start_frame = 0 if (anchored or narration_type == "parallel") else body_frames
    # The segment must outlast whichever finishes later: the code, or the voice.
    segment = max(body_frames, start_frame + spoken_end)
    hold = max(MIN_HOLD_FRAMES, segment + POST_PAD_FRAMES - body_frames)

    spans.append(Span(spans[-1].state, hold))

    narration = Narration(audio, start_frame) if len(audio) else None
    return spans, narration, captions, body_frames + hold


def build_plan(
    snippets: list[Snippet],
    narration_type: str = "after",
    pace: str = "narration",
    intro: Intro | None = None,
) -> FramePlan:
    """Build the complete frame plan for a tutorial.

    Each snippet starts from a blank editor, matching the engine's existing
    behaviour of writing a fresh file per snippet.
    """
    if not snippets:
        raise ValueError("Cannot build a plan with no snippets.")

    all_spans: list[Span] = []
    narrations: list[Narration] = []
    captions: list[Caption] = []

    if intro is not None and intro.phrases:
        intro_spans, intro_narration, intro_captions, intro_frames = plan_intro(intro)
        all_spans.extend(intro_spans)
        if intro_narration is not None:
            narrations.append(intro_narration)
        captions.extend(intro_captions)

    cursor = sum(s.n_frames for s in all_spans)

    for snippet in snippets:
        spans, narration, snippet_captions, n_frames = plan_snippet(
            snippet, narration_type, pace
        )
        if narration is not None:
            narrations.append(
                Narration(narration.samples, narration.start_frame + cursor)
            )
        captions.extend(
            Caption(c.text, c.start_frame + cursor, c.end_frame + cursor)
            for c in snippet_captions
        )
        all_spans.extend(spans)
        cursor += n_frames

    return FramePlan(
        tuple(with_captions(all_spans, captions)),
        tuple(narrations),
        cursor,
        tuple(captions),
    )
