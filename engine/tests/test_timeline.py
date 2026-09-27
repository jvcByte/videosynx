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
from pycoding.timeline import FPS, SPF, SR, Snippet, VisualState, build_plan

W, H = 960, 540
# AAC encoding adds encoder priming, which shifts the decoded onset by a
# handful of samples. 128 samples is 2.7 ms -- far below the ~40 ms at which
# A/V desync becomes perceptible, and far above the priming we are tolerating.
AAC_TOLERANCE = 128


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
                narration=_tone(1.0),
            ),
            Snippet(
                code="total = sum(range(10))\nprint(total)\n",
                output=("$ python snippet.py", "45"),
                narration=_tone(1.5, freq=330),
            ),
        ]
    )
    path = tmp_path_factory.mktemp("render") / "out.mp4"
    render_plan(plan, path, width=W, height=H)
    return plan, path


def test_plan_arithmetic():
    """Span lengths must follow directly from the timing constants."""
    plan = build_plan(
        [Snippet(code="ab", output=("one", "two"), narration=np.zeros(SPF * 3, dtype=np.int16))]
    )
    # 3 typing spans (empty, "a", "ab") x FRAMES_PER_CHAR(3) = 9
    # 2 output spans x OUT_LINE_FRAMES(4) = 8
    # narration 3 frames + POST_PAD(30) held on the last state = 33
    assert plan.total_frames == 9 + 8 + 33
    assert plan.narrations[0].start_frame == 9 + 8


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
    # AAC frames are padded to a whole number of 1024-sample blocks.
    assert abs(len(decoded) - plan.total_frames * SPF) <= 2048


def test_equal_lengths_produce_no_trailing_silence(rendered):
    """If the PCM ran short, -shortest would clip video — assert it did not."""
    plan, path = rendered
    decoded = _decode_video_audio(path)
    assert len(decoded) >= plan.total_frames * SPF


@pytest.mark.parametrize("index", [0, 1])
def test_narration_lands_on_its_planned_frame(tmp_path, index):
    """THE keystone check.

    Narration is placed at an integer frame in the plan; decoding the rendered
    file must find the tone starting at exactly that frame's first sample.
    """
    plan = build_plan(
        [
            Snippet(code="a" * 40, output=(), narration=_tone(1.0, freq=440)),
            Snippet(code="b" * 25, output=(), narration=_tone(0.8, freq=660)),
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


def test_identical_inputs_render_identical_timing(tmp_path):
    """Nothing in the pipeline may consult a clock."""
    snippets = [Snippet(code="x = 1\nprint(x)\n", output=("1",), narration=_tone(0.5))]
    a = build_plan(snippets)
    b = build_plan(snippets)
    assert a.total_frames == b.total_frames
    assert [s.n_frames for s in a.spans] == [s.n_frames for s in b.spans]
    assert [n.start_frame for n in a.narrations] == [n.start_frame for n in b.narrations]


def test_visible_lines_tracks_typing():
    """Partial reveals must slice the pre-tokenized spans, not re-lex."""
    from pycoding.render.theme import highlight, visible_lines

    code = 'x = "hello"\ny = 2\n'
    spans = highlight(code)
    assert len(visible_lines(spans, code, 0)) == 1
    assert visible_lines(spans, code, 0) == [[]]
    assert visible_lines(spans, code, 1)[0][0][0] == "x"
    # Mid-line slice stops exactly at the character count.
    assert sum(len(t) for t, _ in visible_lines(spans, code, 5)[0]) == 5
    # Past the first newline we are on line 2.
    assert len(visible_lines(spans, code, 14)) == 2


def test_highlight_preserves_trailing_blank_lines():
    """Pygments normalises trailing newlines; the render must not lose lines.

    Regression: ``highlight('a\\nb\\n\\n')`` returned 3 lines instead of 4, so
    trailing blank lines never rendered and the cursor sat on the wrong row.
    """
    from pycoding.render.theme import highlight, visible_lines

    for code in ["x", "a\n", "a\n\n", "a\nb\n\n", "a = 1\nb = 2\n\n\nc = 3\n", "\n\n"]:
        spans = highlight(code)
        assert len(spans) == code.count("\n") + 1, f"{code!r} -> {len(spans)} lines"
        # The renderer slices by character offset, so this must hold for every
        # prefix the typing animation will ever ask for.
        for n in range(len(code) + 1):
            visible = visible_lines(spans, code, n)
            assert len(visible) == code[:n].count("\n") + 1, f"{code!r} at {n}"
            # Leaf characters are what actually get drawn; newlines are the
            # separators between rows, not glyphs.
            drawn = sum(len(t) for line in visible for t, _ in line)
            assert drawn == len(code[:n]) - code[:n].count("\n")


def test_build_plan_rejects_empty():
    with pytest.raises(ValueError):
        build_plan([])


# --------------------------------------------------------------- captions

NARRATION_TEXT = (
    "A list comprehension builds a new list in a single expression. "
    "Here we start with the numbers zero through fourteen, then keep only the evens."
)


def _captioned_snippet(text: str = NARRATION_TEXT) -> Snippet:
    return Snippet(
        code="evens = [x for x in range(15) if x % 2 == 0]\nprint(evens)\n",
        output=("$ python snippet.py", "[0, 2, 4, 6, 8, 10, 12, 14]"),
        narration=_tone(3.0),
        narration_text=text,
    )


def test_captions_span_exactly_the_narration():
    plan = build_plan([_captioned_snippet()])
    narration = plan.narrations[0]
    assert plan.captions, "no captions were generated"
    assert plan.captions[0].start_frame == narration.start_frame
    # Narration frames round up, so the last caption ends at or before that point.
    assert plan.captions[-1].end_frame <= narration.start_frame + (
        len(narration.samples) // SPF + 1
    )


def test_captions_are_contiguous_and_ordered():
    plan = build_plan([_captioned_snippet()])
    for earlier, later in zip(plan.captions, plan.captions[1:]):
        assert earlier.end_frame == later.start_frame
    for caption in plan.captions:
        assert caption.end_frame > caption.start_frame, "zero-length caption"
        assert caption.text.strip()


def test_captions_do_not_change_the_timeline():
    """Splitting spans to attach captions must not alter the total length.

    This is the invariant that keeps captions from breaking A/V sync.
    """
    plain = build_plan([_captioned_snippet("")])
    with_caps = build_plan([_captioned_snippet()])
    assert with_caps.total_frames == plain.total_frames
    assert [n.start_frame for n in with_caps.narrations] == [
        n.start_frame for n in plain.narrations
    ]
    # Every original span boundary must survive as a numeric boundary.
    boundaries_plain = set()
    pos = 0
    for span in plain.spans:
        boundaries_plain.add(pos)
        pos += span.n_frames
    boundaries_plain.add(pos)
    boundaries_caps = set()
    pos = 0
    for span in with_caps.spans:
        boundaries_caps.add(pos)
        pos += span.n_frames
    boundaries_caps.add(pos)
    assert boundaries_plain <= boundaries_caps


def test_caption_text_is_fully_preserved():
    """Every word of the narration must appear across the captions."""
    plan = build_plan([_captioned_snippet()])
    assert " ".join(c.text for c in plan.captions).split() == NARRATION_TEXT.split()


def test_chunking_respects_limits():
    from pycoding.timeline import CAPTION_MAX_CHARS, CAPTION_MAX_WORDS, chunk_caption_text

    chunks = chunk_caption_text(NARRATION_TEXT)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= CAPTION_MAX_CHARS or len(chunk.split()) == 1
        assert len(chunk.split()) <= CAPTION_MAX_WORDS
    assert chunk_caption_text("") == []


def test_no_narration_text_means_no_captions():
    plan = build_plan([_captioned_snippet("")])
    assert plan.captions == ()
    assert all(span.state.caption is None for span in plan.spans)


def test_caption_reaches_the_visual_states():
    """Captions must actually make it onto the frames, not just the plan."""
    plan = build_plan([_captioned_snippet()])
    shown = {span.state.caption for span in plan.spans if span.state.caption}
    assert shown == {c.text for c in plan.captions}


def test_srt_timestamps_and_layout():
    from pycoding.srt import timestamp, to_srt

    assert timestamp(0) == "00:00:00,000"
    assert timestamp(30) == "00:00:01,000"  # 30 frames at 30fps
    assert timestamp(90_000) == "00:50:00,000"

    plan = build_plan([_captioned_snippet()])
    body = to_srt(plan.captions)
    blocks = [b for b in body.strip().split("\n\n")]
    assert len(blocks) == len(plan.captions)
    first = blocks[0].split("\n")
    assert first[0] == "1"
    assert " --> " in first[1]
    assert first[2] == plan.captions[0].text
