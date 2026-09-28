"""The local script parser: prose in, slides out, no model involved.

This is what lets local mode take pasted text, so the properties that matter
are that it finds the right section boundaries and that it adds nothing --
what the writer pasted is what gets filmed.
"""

from __future__ import annotations

import pytest

from pycoding.prose import lesson_from_prose

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


def test_nothing_is_invented():
    """The words on the slide are the words that were pasted."""
    lesson = lesson_from_prose(SCRIPT)
    shown = " ".join(line for s in lesson.sections
                     for _, lines in s.slide.columns for line in lines)
    assert "growing fintech startup" in shown
    assert "huge training" in shown


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
        assert list(section.sentences) == panel
