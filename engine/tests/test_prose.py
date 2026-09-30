"""The local script parser: prose in, slides out, no model involved.

This is what lets local mode take pasted text, so the properties that matter
are that it finds the right section boundaries and that it adds nothing --
what the writer pasted is what gets filmed.
"""

from __future__ import annotations

import pytest

from videosynx.prose import lesson_from_prose

# The shape a real script arrives in: markers at the start of a line, no blank
# lines between scenes, and hard wrapping through the middle of sentences.
SCRIPT = """Lesson 15.1 — When to Fine-Tune
Lesson 1 — When to Fine-Tune and When Not To
Video Talking Points
Scene 1 — The Two-Week Deadline: Open inside a growing fintech startup. An
engineer is handed a pile of support messages: complaints, requests, fraud.
A manager says we need this in two weeks.
Scene 2 — Don't Build From Zero: The screen expands into a huge training
operation. Then everything freezes.
"""


def test_scenes_become_slides():
    lesson = lesson_from_prose(SCRIPT)
    assert [s.slide.heading for s in lesson.sections] == [
        "The Two-Week Deadline",
        "Don't Build From Zero",
    ]


def test_the_document_title_is_the_title_card():
    """The heading lines before the first scene name the video, not a scene."""
    assert lesson_from_prose(SCRIPT).title == "Lesson 15.1 — When to Fine-Tune"


def test_hard_wrapped_lines_are_joined_back_into_sentences():
    """A script is wrapped at eighty columns, so a sentence arrives as three
    lines. Splitting on the wrapping would give fragments, not narration."""
    lesson = lesson_from_prose(SCRIPT)
    said = lesson.sections[0].sentences
    assert said[0] == "Open inside a growing fintech startup."
    assert "complaints, requests, fraud." in " ".join(said)


def test_a_marker_with_nothing_after_it_is_not_a_scene():
    """"Lesson 1 — ..." over "Video Talking Points" is a title and a
    subheading, not something worth a slide of its own."""
    headings = [s.slide.heading for s in lesson_from_prose(SCRIPT).sections]
    assert "Video Talking Points" not in headings


def test_prose_with_no_markers_still_becomes_a_lesson():
    """Someone pasting three paragraphs and no scene numbers should still get
    a video rather than an error."""
    lesson = lesson_from_prose(
        "A queue absorbs a burst of work. A worker takes one item at a time. "
        "The web tier stays fast because it never waits."
    )
    assert lesson.sections
    assert len(lesson.sections[0].sentences) == 3


def test_a_long_scene_is_split_across_slides():
    body = " ".join(f"Sentence number {n}." for n in range(11))
    lesson = lesson_from_prose(f"Scene 1 — A Long One: {body}")
    assert len(lesson.sections) == 3          # eleven sentences, four per slide
    assert all(len(s.sentences) <= 4 for s in lesson.sections)
    # Every sentence survives, in order, across the split.
    said = [line for s in lesson.sections for line in s.sentences]
    assert said == [f"Sentence number {n}." for n in range(11)]


def test_a_scene_spanning_several_slides_says_where_you_are():
    """One scene can run for minutes. A heading that never changes across all
    of it reads as the video having stopped, so it carries its position."""
    body = " ".join(f"Sentence number {n}." for n in range(11))
    lesson = lesson_from_prose(f"Scene 1 — A Long One: {body}")
    assert [s.slide.heading for s in lesson.sections] == [
        "A Long One · 1/3", "A Long One · 2/3", "A Long One · 3/3",
    ]


def test_a_scene_that_fits_one_slide_is_not_numbered():
    """Nothing is gained by saying "1/1"."""
    lesson = lesson_from_prose(
        "Scene 1 — Short: The whole scene is one sentence, so it is one slide.")
    assert lesson.sections[0].slide.heading == "Short"


def test_the_counter_does_not_push_the_heading_over_the_cap():
    body = " ".join(f"Sentence number {n}." for n in range(6))
    lesson = lesson_from_prose(
        "Scene 1 — " + " ".join(f"word{n}" for n in range(40)) + f": {body}")
    for section in lesson.sections:
        assert len(section.slide.heading) <= 72, section.slide.heading
        assert section.slide.heading.endswith(("1/2", "2/2"))
        assert "…" in section.slide.heading, "the name was not trimmed"


def test_nothing_is_invented():
    """The words on the slide are the words that were pasted.

    The panel is no longer the whole scene: the first sentence is spoken over
    the heading and reaches the screen through the caption band instead. So
    this checks the words are in the video, and that no panel line contains a
    word the writer did not write.
    """
    import re

    lesson = lesson_from_prose(SCRIPT)
    said = " ".join(s for sec in lesson.sections for s in sec.sentences)
    assert "growing fintech startup" in said
    assert "huge training" in said

    for section in lesson.sections:
        written = " ".join(section.sentences)
        for _, lines in section.slide.columns:
            for line in lines:
                for word in re.findall(r"\w+", line):
                    assert word in written, f"{word!r} is not in the script"


def test_a_heading_is_not_a_paragraph():
    """"Scene 1 — " followed by a very long title is capped, at a word edge."""
    body = "This is the body of the scene, and it says several things."
    lesson = lesson_from_prose(
        "Scene 1 — " + " ".join(f"word{n}" for n in range(40)) + f": {body}"
    )
    heading = lesson.sections[0].slide.heading
    assert len(heading) <= 72
    assert heading.endswith("…")
    # The body still arrives whole; only the heading was shortened.
    assert lesson.sections[0].sentences == (
        "This is the body of the scene, and it says several things.",
    )


def test_emphasis_markers_are_not_read_aloud():
    """A script is written with markup in it, and the markup is not words.

    Left in, `**` is spoken by the voice and printed in the caption. A real
    script -- one that opens a bold run and never closes it -- came out with a
    narration ending in a literal `"**`.
    """
    lesson = lesson_from_prose(
        "Scene 1 — The Trap: The model **memorises** the examples rather than "
        "the pattern, and it scores well until new data arrives.\n"
        'Core line: "The model did not learn the problem. It learned the '
        'examples."**\n'
        "Scene 2 — Next: Reach for `feature extraction` first when the data is "
        "thin, and only then consider changing the model.\n"
    )
    said = " ".join(s for section in lesson.sections for s in section.sentences)
    headings = " ".join(section.slide.heading for section in lesson.sections)

    assert "*" not in said and "`" not in said, said
    assert "*" not in headings and "`" not in headings, headings
    # Stripped, not dropped: the words those markers wrapped are still there.
    assert "memorises" in said
    assert "It learned the examples." in said
    assert "feature extraction first" in said


def test_empty_input_is_refused():
    for empty in ("", "   \n\n  "):
        with pytest.raises(ValueError, match="empty"):
            lesson_from_prose(empty)


def test_the_slides_carry_their_own_sentences():
    """The panel and the caption band show the same words; the panel is what
    makes the slide more than a bare heading."""
    for section in lesson_from_prose(SCRIPT).sections:
        panel = [line for _, lines in section.slide.columns for line in lines]
        # Sentence 0 is spoken over the heading, so it is the rest that has
        # somewhere to appear.
        assert panel == list(section.sentences[1:])


def test_every_piece_is_revealed_while_its_sentence_is_being_spoken():
    """A slide needs one FEWER piece than it has sentences.

    `plan_explanation` reveals `min(sentence_index, pieces)`, so the first
    sentence always plays over the heading alone and cannot light anything.
    One piece too many and the last is on screen only during the silent
    second after the voice stops -- never during narration, which is
    invisible to a test that does not check for it and obvious in a video.
    """
    for section in lesson_from_prose(SCRIPT).sections:
        pieces = section.slide.pieces
        assert pieces <= len(section.sentences) - 1, (
            f"{section.slide.heading!r}: {pieces} pieces for "
            f"{len(section.sentences)} sentences"
        )


# ------------------------------------------------------- markers on one line

# How the scripts actually arrive: one continuous line, every marker inline,
# and the writer referring back to scenes already gone. Every part of this
# shape defeated the parser at once.
ONE_LINE = (
    "Asynchronous Ingestion Video Talking Points "
    "Scene 1 — The Market Stall: On the left: one attendant takes orders. "
    "On the right: three attendants share the work. "
    "Scene 2 — The Core Truth: Nothing is happening while the stall waits. "
    "The queue is what lets the two sides run at different speeds. "
    "Scene 3 — The Summary: Do not wait. Hand the work off and keep moving. "
    "Remember the stall from Scene 1 and the queue in Scene 3."
)


def test_scenes_are_found_when_the_script_is_one_line():
    """The anchored marker search finds nothing at all here: the whole script
    becomes one blob chopped every four sentences, under headings that are
    truncated sentences, with "Scene 2 — The Core Truth:" read aloud."""
    lesson = lesson_from_prose(ONE_LINE)
    headings = [s.slide.heading for s in lesson.sections]
    assert headings == ["The Market Stall", "The Core Truth", "The Summary"]


def test_the_title_is_the_text_before_the_first_scene():
    lesson = lesson_from_prose(ONE_LINE)
    assert lesson.title == "Asynchronous Ingestion Video Talking Points"


def test_a_marker_is_never_read_aloud():
    """The marker's own words are not part of what the writer is saying."""
    lesson = lesson_from_prose(ONE_LINE)
    said = " ".join(s for sec in lesson.sections for s in sec.sentences)
    # The marker itself -- "Scene 2 — The Core Truth:" -- is what gets read
    # out when the split does not happen.
    assert "Scene 1 —" not in said
    assert "Scene 2 —" not in said
    assert "Scene 3 —" not in said
    # But a sentence that merely mentions a scene is left alone.
    assert "the stall from Scene 1" in said


def test_a_scene_number_that_goes_backwards_is_not_a_scene():
    """Looking back is not starting a new one.

    Lesson 15.1 -- the script that works today -- says "Bring back the
    engineer from Scene 1." long after Scene 15. Counting only ascending
    numbers is what tells the two apart; without it that sentence is cut out
    mid-clause and becomes a scene of its own.
    """
    lesson = lesson_from_prose(ONE_LINE)
    said = " ".join(s for sec in lesson.sections for s in sec.sentences)
    assert "and the queue in Scene 3." in said, "the back-reference was cut"
    assert len(lesson.sections) == 3, "a back-reference became a scene"


def test_a_back_reference_carrying_a_colon_is_still_not_a_scene():
    """The separator alone does not decide it -- "as in Scene 1: the same
    rule" carries the same colon a real marker does. What settles it is that
    real scene numbers only ever go up."""
    text = ("Scene 1 — One: The first thing happens here and it matters. "
            "Scene 2 — Two: The second thing happens, and as in Scene 1: "
            "the very same rule applies all over again. "
            "Scene 3 — Three: The third thing closes it out for good.")
    lesson = lesson_from_prose(text)
    assert [s.slide.heading for s in lesson.sections] == ["One", "Two", "Three"]
    said = " ".join(s for sec in lesson.sections for s in sec.sentences)
    assert "as in Scene 1: the very same rule applies" in said, (
        "the sentence was cut at the back-reference"
    )


# ----------------------------------------------------------------- with code

CODE_SCRIPT = """Scene 1 — The Problem: A web tier that waits on slow work falls over.
The queue is what absorbs the burst.

Scene 2 — The Fix: Hand the work off and return immediately.
```python
import asyncio

async def ingest(doc):
    await queue.put(doc)
    return "accepted"
```
The caller is free the moment the work is queued.

Scene 3 — The Wrap: Nothing waits, so nothing falls over."""


def test_a_fenced_block_becomes_a_code_section():
    """"Both" has to mean something in local mode, and the writer putting a
    block in the script is them saying which half the scene belongs to."""
    from videosynx.lesson import Section

    lesson = lesson_from_prose(CODE_SCRIPT)
    kinds = [type(s).__name__ for s in lesson.sections]
    assert kinds == ["ExplainSection", "Section", "ExplainSection"]

    code = lesson.sections[1]
    assert isinstance(code, Section)
    assert code.code.startswith("import asyncio")
    assert "await queue.put(doc)" in code.code
    # The scene's own words narrate it, and the fence markers are not spoken.
    said = " ".join(s for _, s in code.narration)
    assert "Hand the work off and return immediately." in said
    assert "```" not in said and "python" not in said.split()


def test_code_narration_is_not_anchored_to_the_first_character():
    """Nobody said which line each sentence is about, and guessing would put
    every sentence over the first keystroke."""
    from videosynx.lesson import Section

    code = lesson_from_prose(CODE_SCRIPT).sections[1]
    assert isinstance(code, Section)
    assert all(marker == "" for marker, _ in code.narration)


def test_a_script_with_no_code_is_all_slides():
    """Neither script submitted so far has a fence in it, so this is the
    path they take -- and it must not invent a code section."""
    from videosynx.lesson import Section

    lesson = lesson_from_prose(ONE_LINE)
    assert not any(isinstance(s, Section) for s in lesson.sections)


def test_a_left_and_right_comparison_becomes_two_panels():
    """The writer wrote a comparison; two panels is what a comparison is."""
    scene = lesson_from_prose(ONE_LINE).sections[0]
    titles = [title for title, _ in scene.slide.columns]
    assert titles == ["On the left", "On the right"]
    assert scene.slide.columns[0][1] == ("One attendant takes orders.",)
    assert scene.slide.columns[1][1] == ("Three attendants share the work.",)
