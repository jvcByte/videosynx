"""The runnable check for the render core.

If test_narration_lands_on_its_planned_frame passes, sync is exact by
construction and every later stage inherits it. If it fails, nothing else
in the render path is trustworthy yet.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from pycoding.render import render_plan
from pycoding.timeline import (
    FPS,
    SPF,
    SR,
    Intro,
    Phrase,
    Snippet,
    build_plan,
    split_evenly,
    weighted_split,
)

W, H = 960, 540
# AAC encoding adds encoder priming, which shifts the decoded onset by a
# handful of samples. 128 samples is 2.7 ms -- far below the ~40 ms at which
# A/V desync becomes perceptible, and far above the priming we are tolerating.
AAC_TOLERANCE = 128


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.int16)


def phrase(text: str, seconds: float) -> Phrase:
    return Phrase(text, silence(seconds))


def _tone(seconds: float, freq: int = 440) -> np.ndarray:
    """A sine tone, MP3-encoded then decoded -- the same path real narration takes."""
    with tempfile.TemporaryDirectory() as td:
        mp3 = Path(td) / "t.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:duration={seconds}",
             "-ar", str(SR), "-ac", "1", str(mp3)],
            check=True,
        )
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(mp3), "-f", "s16le",
             "-ar", str(SR), "-ac", "1", "-"],
            capture_output=True, check=True,
        ).stdout
    return np.frombuffer(raw, dtype=np.int16).copy()


def _decode_video_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le",
         "-ar", str(SR), "-ac", "1", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16)


def _count_video_frames(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(out)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> tuple[object, Path]:
    """A small two-snippet tutorial, rendered once and shared by the checks."""
    plan = build_plan(
        [
            Snippet(
                code="evens = [x for x in range(15) if x % 2 == 0]\nprint(evens)\n",
                output=("$ python snippet.py", "[0, 2, 4, 6, 8, 10, 12, 14]"),
                phrases=(Phrase("Here we keep the even numbers.", _tone(1.0)),),
            ),
            Snippet(
                code="total = sum(range(10))\nprint(total)\n",
                output=("$ python snippet.py", "45"),
                phrases=(Phrase("Now we add them all up.", _tone(1.5, freq=330)),),
            ),
        ]
    )
    path = tmp_path_factory.mktemp("render") / "out.mp4"
    render_plan(plan, path, width=W, height=H)
    return plan, path


# ------------------------------------------------------------------ timing

def test_video_frame_count_is_exact(rendered):
    """N buffers in must equal N frames out — this is the whole point of the pipe."""
    plan, path = rendered
    assert _count_video_frames(path) == plan.total_frames


def test_video_duration_is_exact(rendered):
    plan, path = rendered
    assert abs(_duration(path) - plan.total_frames / FPS) < 1e-3


def test_audio_length_matches_video(rendered):
    """Both streams derive from the same integer, so neither needs truncating."""
    plan, path = rendered
    decoded = _decode_video_audio(path)
    assert abs(len(decoded) - plan.total_frames * SPF) <= 2048


def test_equal_lengths_produce_no_trailing_silence(rendered):
    """If the PCM ran short, -shortest would clip video — assert it did not."""
    plan, path = rendered
    decoded = _decode_video_audio(path)
    assert len(decoded) >= plan.total_frames * SPF


def test_identical_inputs_render_identical_timing():
    """Nothing in the pipeline may consult a clock."""
    def make():
        return [Snippet(code="x = 1\nprint(x)\n", output=("1",),
                        phrases=(phrase("We set x to one.", 0.5),))]

    a, b = build_plan(make()), build_plan(make())
    assert a.total_frames == b.total_frames
    assert [s.n_frames for s in a.spans] == [s.n_frames for s in b.spans]
    assert [n.start_frame for n in a.narrations] == [n.start_frame for n in b.narrations]
    assert a.captions == b.captions


@pytest.mark.parametrize("index", [0, 1])
def test_narration_lands_on_its_planned_frame(tmp_path, index):
    """THE keystone check.

    Narration is placed at an integer frame in the plan; decoding the rendered
    file must find the tone starting at exactly that frame's first sample.
    """
    plan = build_plan(
        [
            Snippet(code="a" * 40, phrases=(Phrase("first", _tone(1.0, freq=440)),)),
            Snippet(code="b" * 25, phrases=(Phrase("second", _tone(0.8, freq=660)),)),
        ]
    )
    path = tmp_path / "sync.mp4"
    render_plan(plan, path, width=W, height=H)

    expected = plan.narrations[index].start_frame * SPF
    decoded = _decode_video_audio(path)

    # Start the search after any earlier narration has finished, otherwise we
    # would just re-find narration 0's onset every time.
    search_from = 0
    for earlier in plan.narrations[:index]:
        end = earlier.start_frame * SPF + len(earlier.samples)
        search_from = max(search_from, end + AAC_TOLERANCE)

    onset = np.flatnonzero(np.abs(decoded[search_from:]) > 100)
    assert len(onset), f"no audio found after sample {search_from}"
    actual = int(onset[0]) + search_from
    assert abs(actual - expected) <= AAC_TOLERANCE, (
        f"narration {index} starts at sample {actual}, plan says {expected}"
    )


# ------------------------------------------------------------- typing pace

def test_typing_has_a_human_rhythm():
    """Line ends and punctuation must cost more frames than ordinary letters."""
    from pycoding.timeline import typing_weights

    code = "ab\ncd, e"
    # state i is the code typed so far, so weights[i] belongs to code[i-1]
    weights = typing_weights(code)
    assert weights[0] < weights[1], "no pause before the first keystroke"
    assert weights[code.index("\n") + 1] > weights[1], "a newline must cost more than a letter"
    assert weights[code.index(",") + 1] > weights[1], "a comma must cost more than a letter"
    assert weights[code.index(" ") + 1] < weights[1], "a space must cost less than a letter"


def test_typing_is_not_a_uniform_cadence():
    """The reported problem: code appearing at a constant 20 chars/sec."""
    code = "a = 1\nb = 2\nc = 3\n"
    plan = build_plan([Snippet(code=code, phrases=(phrase("Counting.", 3.0),))])
    counts = [span.n_frames for span in plan.spans[: len(code) + 1]]
    assert len(set(counts)) > 1, "typing is still a flat cadence"
    # Every character is visible for at least a frame.
    assert sum(counts) >= len(code) + 1


def test_typing_never_outruns_the_readable_floor():
    """However short the narration, typing must not become a blur.

    The reported problem was 28.6 chars/sec. The floor is ~13.6.
    """
    from pycoding.timeline import MIN_FRAMES_PER_WEIGHT, typing_weights

    code = "x = 1\n" * 40
    plan = build_plan([Snippet(code=code, phrases=(phrase("Short.", 2.0),))])
    typing = sum(s.n_frames for s in plan.spans[: len(code) + 1])

    floor = sum(typing_weights(code)) * MIN_FRAMES_PER_WEIGHT
    assert typing >= floor - len(code), (
        f"typing took {typing} frames, floor is {floor:.0f}"
    )
    chars_per_sec = len(code) / (typing / FPS)
    assert chars_per_sec <= FPS / MIN_FRAMES_PER_WEIGHT + 0.5, (
        f"{chars_per_sec:.1f} chars/sec is too fast to read"
    )


def test_typing_stretches_to_cover_a_long_narration():
    """Typing slows to meet the voice rather than leaving it talking over nothing."""
    code = "x = 1\n" * 40
    narration = 60.0  # far longer than the natural typing time
    plan = build_plan([Snippet(code=code, phrases=(phrase("Long.", narration),))])
    typing = sum(s.n_frames for s in plan.spans[: len(code) + 1])
    assert typing >= narration * FPS - 2
    assert plan.narrations[0].start_frame == 0


def test_voice_starts_with_the_first_keystroke():
    """The originally reported bug: the voice waited for typing to finish."""
    plan = build_plan([Snippet(code="ab", phrases=(phrase("Hello there.", 1.0),))])
    assert plan.narrations[0].start_frame == 0


def test_split_evenly_preserves_the_total():
    for total, parts in [(153, 40), (10, 10), (7, 30), (0, 5), (500, 1)]:
        counts = split_evenly(total, parts)
        assert len(counts) == parts
        assert sum(counts) == total
        assert max(counts) - min(counts) <= 1


def test_weighted_split_preserves_the_total():
    for total, weights in [(153, [1.0, 2.0, 0.5]), (0, [1.0]), (7, [0.5] * 5)]:
        counts = weighted_split(total, weights)
        assert sum(counts) == total
        assert len(counts) == len(weights)
    # A heavier weight must never receive fewer frames than a lighter one.
    counts = weighted_split(100, [1.0, 10.0])
    assert counts[1] > counts[0]


# --------------------------------------------------------------- captions

NARRATION = (
    "A list comprehension builds a new list in a single expression. "
    "Here we start with the numbers zero through fourteen. "
    "Then we keep only the even ones."
)


def _captioned_snippet(with_text: bool = True, per_sentence: float = 2.0) -> Snippet:
    """Three phrases of fixed audio length, so captions can be checked exactly.

    The audio is identical whether or not the phrases carry text, so the two
    plans differ only in their captions.
    """
    sentences = [s.strip() for s in NARRATION.split(".") if s.strip()]
    return Snippet(
        code="evens = [x for x in range(15) if x % 2 == 0]\nprint(evens)\n",
        output=("$ python snippet.py", "[0, 2, 4, 6, 8, 10, 12, 14]"),
        phrases=tuple(
            Phrase(s + "." if with_text else "", silence(per_sentence))
            for s in sentences
        ),
    )


def test_captions_are_grouped_per_phrase():
    """The reported bug: captions drifted because they were proportioned
    across the whole narration rather than timed per spoken sentence.

    Consecutive captions from the same sentence are adjacent; a gap between
    them can only be the pause between two sentences.
    """
    snippet = _captioned_snippet()
    plan = build_plan([snippet])

    groups: list[list] = []
    for caption in plan.captions:
        if groups and groups[-1][-1].end_frame == caption.start_frame:
            groups[-1].append(caption)
        else:
            groups.append([caption])

    assert len(groups) == len(snippet.phrases), (
        f"{len(groups)} caption groups for {len(snippet.phrases)} sentences — "
        "a caption is straddling a sentence boundary"
    )
    for group, spoken in zip(groups, snippet.phrases):
        assert " ".join(c.text for c in group).split() == spoken.text.split()


def test_captions_never_overlap_or_run_backwards():
    plan = build_plan([_captioned_snippet()])
    for earlier, later in zip(plan.captions, plan.captions[1:]):
        assert earlier.end_frame <= later.start_frame
    assert plan.captions[0].start_frame >= plan.narrations[0].start_frame


def test_captions_do_not_change_the_timeline():
    """Attaching caption text must not alter a single frame of the plan."""
    plain = build_plan([_captioned_snippet(with_text=False)])
    with_caps = build_plan([_captioned_snippet()])
    assert plain.captions == ()
    assert with_caps.total_frames == plain.total_frames
    assert [n.start_frame for n in with_caps.narrations] == [
        n.start_frame for n in plain.narrations
    ]

    # Captions split spans, so the lists differ — but every original frame
    # boundary must survive, which is what keeps audio and video aligned.
    def boundaries(plan):
        edges, pos = set(), 0
        for span in plan.spans:
            edges.add(pos)
            pos += span.n_frames
        edges.add(pos)
        return edges

    assert boundaries(plain) <= boundaries(with_caps)


def test_caption_text_is_fully_preserved():
    plan = build_plan([_captioned_snippet()])
    assert " ".join(c.text for c in plan.captions).split() == NARRATION.split()


def test_chunking_respects_limits():
    from pycoding.timeline import CAPTION_MAX_CHARS, CAPTION_MAX_WORDS, chunk_caption_text

    chunks = chunk_caption_text(NARRATION)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= CAPTION_MAX_CHARS or len(chunk.split()) == 1
        assert len(chunk.split()) <= CAPTION_MAX_WORDS
    assert chunk_caption_text("") == []


def test_no_phrases_means_no_captions():
    plan = build_plan([Snippet(code="x = 1\n", output=("1",))])
    assert plan.captions == ()
    assert all(span.state.caption is None for span in plan.spans)


def test_caption_reaches_the_visual_states():
    """Captions must actually make it onto the frames, not just the plan."""
    plan = build_plan([_captioned_snippet()])
    shown = {span.state.caption for span in plan.spans if span.state.caption}
    assert shown == {c.text for c in plan.captions}


# ------------------------------------------------------------------- srt

def test_srt_timestamps_and_layout():
    from pycoding.srt import timestamp, to_srt

    assert timestamp(0) == "00:00:00,000"
    assert timestamp(30) == "00:00:01,000"  # 30 frames at 30fps
    assert timestamp(90_000) == "00:50:00,000"

    plan = build_plan([_captioned_snippet()])
    blocks = to_srt(plan.captions).strip().split("\n\n")
    assert len(blocks) == len(plan.captions)
    first = blocks[0].split("\n")
    assert first[0] == "1"
    assert " --> " in first[1]
    assert first[2] == plan.captions[0].text


# ------------------------------------------------------------------ intro

def _intro() -> Intro:
    return Intro("Python List Comprehensions",
                 (phrase("In this video we build lists.", 2.0),
                  phrase("Then we count some names.", 2.0)))


def test_intro_opens_with_a_title_card():
    """A viewer arriving cold should be told what the video is before any code."""
    plan = build_plan([_captioned_snippet()], intro=_intro())
    titled = [s for s in plan.spans if s.state.title is not None]
    assert titled, "no title card in the plan"
    assert all(s.state.title == "Python List Comprehensions" for s in titled)
    # It comes first, and nothing after it is still showing a card.
    assert plan.spans[0].state.title is not None
    titles = [s.state.title is not None for s in plan.spans]
    assert titles == sorted(titles, reverse=True), "title card is not contiguous at the start"


def test_intro_duration_follows_its_narration():
    from pycoding.timeline import INTRO_TAIL_FRAMES

    intro = _intro()
    plan = build_plan([_captioned_snippet()], intro=intro)
    card_frames = sum(s.n_frames for s in plan.spans if s.state.title is not None)
    spoken = sum(p.n_frames for p in intro.phrases)
    assert card_frames == spoken + INTRO_TAIL_FRAMES


def test_intro_captions_are_shown_on_the_card():
    """The intro is narrated, so it must be captioned like everything else."""
    plan = build_plan([_captioned_snippet()], intro=_intro())
    card_frames = sum(s.n_frames for s in plan.spans if s.state.title is not None)

    on_card = {
        s.state.caption for s in plan.spans
        if s.state.title is not None and s.state.caption
    }
    intro_captions = {c.text for c in plan.captions if c.start_frame < card_frames}
    assert on_card == intro_captions
    assert on_card, "the intro was narrated with no captions on screen"


def test_intro_shifts_the_snippets_without_changing_them():
    """Snippets must be identical, just moved later."""
    plain = build_plan([_captioned_snippet()])
    with_intro = build_plan([_captioned_snippet()], intro=_intro())
    card_frames = sum(s.n_frames for s in with_intro.spans if s.state.title is not None)
    assert with_intro.total_frames == plain.total_frames + card_frames
    assert with_intro.narrations[1].start_frame - with_intro.narrations[0].start_frame >= 0
    # The snippet's own narration starts exactly where it did, plus the card.
    assert with_intro.narrations[-1].start_frame == plain.narrations[-1].start_frame + card_frames


def test_no_intro_means_no_title_card():
    plan = build_plan([_captioned_snippet()])
    assert all(s.state.title is None for s in plan.spans)


def test_intro_without_narration_is_ignored():
    plan = build_plan([_captioned_snippet()], intro=Intro("Empty", ()))
    assert all(s.state.title is None for s in plan.spans)


def test_title_card_renders(tmp_path):
    """Smoke test: the card path must actually produce frames."""
    plan = build_plan(
        [Snippet(code="x = 1\n", output=("1",), phrases=(phrase("We set x.", 1.0),))],
        intro=_intro(),
    )
    path = tmp_path / "card.mp4"
    render_plan(plan, path, width=W, height=H)
    assert _count_video_frames(path) == plan.total_frames


# ------------------------------------------------------------------- misc

def test_visible_lines_tracks_typing():
    """Partial reveals must slice the pre-tokenized spans, not re-lex."""
    from pycoding.render.theme import highlight, visible_lines

    code = 'x = "hello"\ny = 2\n'
    spans = highlight(code)
    assert visible_lines(spans, code, 0) == [[]]
    assert visible_lines(spans, code, 1)[0][0][0] == "x"
    assert sum(len(t) for t, _ in visible_lines(spans, code, 5)[0]) == 5
    assert len(visible_lines(spans, code, 14)) == 2


def test_highlight_preserves_trailing_blank_lines():
    """Pygments normalises trailing newlines; the render must not lose lines."""
    from pycoding.render.theme import highlight, visible_lines

    for code in ["x", "a\n", "a\n\n", "a\nb\n\n", "a = 1\nb = 2\n\n\nc = 3\n", "\n\n"]:
        spans = highlight(code)
        assert len(spans) == code.count("\n") + 1, f"{code!r} -> {len(spans)} lines"
        for n in range(len(code) + 1):
            visible = visible_lines(spans, code, n)
            assert len(visible) == code[:n].count("\n") + 1, f"{code!r} at {n}"
            drawn = sum(len(t) for line in visible for t, _ in line)
            assert drawn == len(code[:n]) - code[:n].count("\n")


def test_build_plan_rejects_empty():
    with pytest.raises(ValueError):
        build_plan([])


def test_plan_rejects_bad_options():
    snippet = Snippet(code="x", phrases=(phrase("hi", 1.0),))
    with pytest.raises(ValueError):
        build_plan([snippet], narration_type="sometimes")
    with pytest.raises(ValueError):
        build_plan([snippet], pace="warp")
