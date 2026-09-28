"""The authored-lesson path: load_lesson and build_lesson.

The ``--topic`` path is covered in test_pipeline. This is the other way in --
content that already exists and must be rendered verbatim. Nothing here is
paraphrased, so a bug in this path silently changes what the author wrote.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from pycoding.lesson import (
    build_lesson,
    lesson_from_script,
    load_lesson,
    parse_lesson,
    script_to_lesson,
)
from pycoding.prompts import sentences
from pycoding.providers import ScriptedWriter
from pycoding.timeline import SR, Explanation, Snippet

CODE = "total = 0\nfor n in range(5):\n    total += n\nprint(total)\n"


class ToneSpeaker:
    """A Speaker that returns a predictable tone."""

    def __init__(self, seconds: float = 0.5):
        self.seconds = seconds
        self.spoken: list[str] = []

    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        self.spoken.append(text)
        n = int(SR * self.seconds)
        t = np.arange(n, dtype=np.float64)
        return (np.sin(2 * np.pi * 300 * t / SR) * 8000).astype(np.int16)


def write(tmp_path, data) -> object:
    path = tmp_path / "lesson.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def coded_lesson(**overrides) -> dict:
    data = {
        "title": "Running totals",
        "intro": "A short introduction to the lesson.",
        "sections": [
            {
                "name": "Totals",
                "code": CODE,
                "narration": [["total = 0", "We start the total at zero."],
                              ["print(total)", "And print it."]],
            }
        ],
    }
    data.update(overrides)
    return data


def slide_lesson(columns, narration, **overrides) -> dict:
    data = {
        "title": "A concept",
        "sections": [
            {
                "name": "Concept",
                "explain": {"heading": "Two things", "columns": columns},
                "narration": narration,
            }
        ],
    }
    data.update(overrides)
    return data


TWO_COLUMNS = [["Left", ["one"]], ["Right", ["two"]]]


# ------------------------------------------------------------------- loading

def test_load_lesson_reads_both_section_kinds(tmp_path):
    path = write(tmp_path, {
        "title": "Mixed",
        "intro": "hello",
        "sections": [
            {"name": "Concept", "explain": {"heading": "H", "columns": TWO_COLUMNS},
             "narration": ["First.", "Second."]},
            {"name": "Code", "code": CODE, "narration": [["total = 0", "Zero."]]},
        ],
        "outro": "bye",
    })
    lesson = load_lesson(path)
    assert lesson.title == "Mixed"
    assert lesson.intro == "hello" and lesson.outro == "bye"
    assert len(lesson.sections) == 2
    assert lesson.sections[0].slide.heading == "H"
    assert lesson.sections[1].code == CODE


def test_load_lesson_rejects_a_marker_that_is_not_in_the_code(tmp_path):
    """A marker that does not match is a sentence that will drift on screen."""
    path = write(tmp_path, {
        "title": "T",
        "sections": [{"name": "s", "code": CODE,
                      "narration": [["nope = 1", "This is not in the code."]]}],
    })
    with pytest.raises(ValueError, match="does not appear in the code"):
        load_lesson(path)


def test_load_lesson_rejects_a_slide_with_no_narration(tmp_path):
    """An un-narrated slide is a silent still; there is nothing to time it to."""
    path = write(tmp_path, {
        "title": "T",
        "sections": [{"name": "s", "explain": {"heading": "H"}, "narration": ["  "]}],
    })
    with pytest.raises(ValueError, match="needs narration"):
        load_lesson(path)


def test_load_lesson_rejects_a_lesson_with_no_sections(tmp_path):
    with pytest.raises(ValueError, match="no sections"):
        load_lesson(write(tmp_path, {"title": "T", "sections": []}))


# ------------------------------------------------------------------ building

def test_build_lesson_renders_a_coded_section(tmp_path):
    lesson = load_lesson(write(tmp_path, coded_lesson()))
    tutorial = build_lesson(lesson, speaker=ToneSpeaker(), execute=False)
    assert isinstance(tutorial.sections[0], Snippet)
    assert tutorial.plan.total_frames > 0
    assert tutorial.plan.captions


def test_build_lesson_renders_a_slide(tmp_path):
    lesson = load_lesson(write(tmp_path, slide_lesson(TWO_COLUMNS, ["First.", "Second."])))
    tutorial = build_lesson(lesson, speaker=ToneSpeaker(), execute=False)
    assert isinstance(tutorial.sections[0], Explanation)
    assert any(s.state.slide is not None for s in tutorial.plan.spans)


# --------------------------------------------------------------- slide reveals
# The reveal is the whole point of a slide: the heading states the idea, then
# each sentence adds the next piece. If a sentence and its column fall out of
# step the slide is answering a question the voice has not asked yet.

def reveals(plan) -> list[int]:
    """The distinct reveals, in the order they appear on screen."""
    out: list[int] = []
    for span in plan.spans:
        slide = span.state.slide
        if slide is not None and (not out or out[-1] != slide.revealed):
            out.append(slide.revealed)
    return out


def test_each_sentence_reveals_the_next_column(tmp_path):
    columns = [["A", ["1"]], ["B", ["2"]], ["C", ["3"]]]
    lesson = load_lesson(write(tmp_path, slide_lesson(
        columns, ["Heading only.", "Now A.", "Now B.", "Now C.", "And the takeaway."])))
    plan = build_lesson(lesson, speaker=ToneSpeaker(), execute=False).plan
    # Sentence 1 lands on the heading alone, then one column per sentence.
    assert reveals(plan) == [0, 1, 2, 3]


def test_extra_sentences_hold_the_finished_slide(tmp_path):
    lesson = load_lesson(write(tmp_path, slide_lesson(
        TWO_COLUMNS, ["Heading.", "Left.", "Right.", "Still right.", "Still right."])))
    plan = build_lesson(lesson, speaker=ToneSpeaker(), execute=False).plan
    assert reveals(plan) == [0, 1, 2]
    # Everything the slide can show is on screen by the end.
    assert max(reveals(plan)) == len(TWO_COLUMNS)


def test_a_slide_reveal_never_exceeds_its_pieces(tmp_path):
    """revealed indexes the column list; overshooting would be an IndexError."""
    lesson = load_lesson(write(tmp_path, slide_lesson(TWO_COLUMNS, ["a.", "b.", "c.", "d."])))
    plan = build_lesson(lesson, speaker=ToneSpeaker(), execute=False).plan
    for span in plan.spans:
        if span.state.slide is not None:
            assert span.state.slide.revealed <= len(TWO_COLUMNS)


# --------------------------------------------------------------------- outro

def test_outro_follows_a_coded_section(tmp_path):
    lesson = load_lesson(write(tmp_path, coded_lesson(outro="That is the end.")))
    tutorial = build_lesson(lesson, speaker=ToneSpeaker(), execute=False)
    last = tutorial.sections[-1]
    assert last.phrases[-1].text == "That is the end."
    assert last.phrases[-1].anchor == len(CODE)


def test_outro_follows_a_slide(tmp_path):
    """A slide has no code, so there is no offset to anchor the outro to.

    This used to raise AttributeError: build_lesson reached for ``.code`` on
    an Explanation, which does not have one.
    """
    lesson = load_lesson(write(tmp_path, slide_lesson(
        TWO_COLUMNS, ["First.", "Second."], outro="That is the end.")))
    tutorial = build_lesson(lesson, speaker=ToneSpeaker(), execute=False)
    last = tutorial.sections[-1]
    assert isinstance(last, Explanation)
    assert last.phrases[-1].text == "That is the end."
    assert tutorial.plan.total_frames > 0


def test_the_outro_is_spoken_after_everything_else(tmp_path):
    lesson = load_lesson(write(tmp_path, slide_lesson(
        TWO_COLUMNS, ["First.", "Second."], outro="Goodbye.")))
    tutorial = build_lesson(lesson, speaker=ToneSpeaker(), execute=False)
    text = [p.text for p in tutorial.sections[-1].phrases]
    assert text == ["First.", "Second.", "Goodbye."]


def test_an_outro_without_sections_is_ignored(tmp_path):
    """Guard the ``and snippets`` short-circuit."""
    lesson = load_lesson(write(tmp_path, coded_lesson(outro="")))
    tutorial = build_lesson(lesson, speaker=ToneSpeaker(), execute=False)
    assert tutorial.plan.total_frames > 0


# --------------------------------------------------------------------- audio

def test_the_audio_track_matches_the_frame_count(tmp_path):
    lesson = load_lesson(write(tmp_path, slide_lesson(
        TWO_COLUMNS, ["First sentence.", "Second sentence."])))
    plan = build_lesson(lesson, speaker=ToneSpeaker(), execute=False).plan
    # Exactly one frame is 1600 samples, so the two streams cannot drift.
    assert len(plan.build_audio()) == plan.total_frames * SR // 30


def test_captions_tile_their_narration(tmp_path):
    """Captions are chunked *within* a sentence, so they must still line up.

    A caption onset mid-phrase is expected -- that is a long sentence being
    split. What must hold is that the captions covering one narration begin
    where its audio begins, run back to back, and end where it ends.
    """
    lesson = load_lesson(write(tmp_path, slide_lesson(
        TWO_COLUMNS, ["First sentence.", "Second sentence.", "Third."])))
    plan = build_lesson(lesson, speaker=ToneSpeaker(), execute=False).plan
    for n in plan.narrations:
        f = n.start_frame
        length = -(-len(n.samples) // (SR // 30))  # frames, rounded up
        caps = sorted((c for c in plan.captions
                       if c.start_frame >= f and c.end_frame <= f + length),
                      key=lambda c: c.start_frame)
        if not caps:
            continue
        assert caps[0].start_frame == f
        assert caps[-1].end_frame == f + length
        for a, b in zip(caps, caps[1:]):
            assert a.end_frame == b.start_frame


# ------------------------------------------------- authored-content invariants
# A narration entry holds one sentence. write it as two and speak_plain splits
# it, so it becomes two reveal steps and the columns light up a beat before the
# voice describes them. That is invisible until someone watches the video.

def test_a_two_sentence_entry_becomes_two_reveal_steps(tmp_path):
    """Pin the behaviour that makes the one-sentence rule necessary."""
    lesson = load_lesson(write(tmp_path, slide_lesson(
        TWO_COLUMNS, ["Heading only.", "One. Two.", "Last."])))
    tutorial = build_lesson(lesson, speaker=ToneSpeaker(), execute=False)
    # Two sentences in one entry, so the slide is finished a sentence early.
    assert len(tutorial.sections[0].phrases) == 4
    plan = tutorial.plan
    assert reveals(plan) == [0, 1, 2]


# ---------------------------------------------------------- scripts as input
# A script can arrive from a browser instead of a file. It travels as text,
# because the API and the worker need not share a disk.

SCRIPT_JSON = {
    "title": "Authored elsewhere",
    "sections": [
        {"name": "Concept",
         "explain": {"heading": "H", "columns": [["A", ["a"]]]},
         "narration": ["One.", "Two.", "Three."]},
    ],
}


class NeverCalled(ScriptedWriter):
    def write(self, prompt: str) -> str:
        raise AssertionError(f"the model was called for structured input: {prompt[:60]}")


def test_parse_lesson_accepts_a_plain_dict():
    assert parse_lesson(SCRIPT_JSON).title == "Authored elsewhere"


def test_parse_lesson_rejects_anything_that_is_not_an_object():
    for bad in (None, [], "a lesson, honestly", 7):
        with pytest.raises(ValueError, match="expected a JSON object"):
            parse_lesson(bad)


def test_parse_lesson_needs_a_title():
    with pytest.raises(ValueError, match="no title"):
        parse_lesson({"sections": SCRIPT_JSON["sections"]})


def test_parse_lesson_rejects_a_section_that_is_neither_code_nor_explain():
    with pytest.raises(ValueError, match="neither code nor explain"):
        parse_lesson({"title": "T", "sections": [{"name": "bare"}]})


def test_a_script_that_is_already_a_lesson_is_not_sent_to_the_model():
    """A caller who has already done the work should not have it rewritten."""
    lesson = lesson_from_script(NeverCalled(["unused"]), json.dumps(SCRIPT_JSON))
    assert lesson.title == "Authored elsewhere"
    assert len(lesson.sections) == 1


def test_a_prose_script_goes_through_the_model_once():
    writer = ScriptedWriter([json.dumps(SCRIPT_JSON)])
    lesson = lesson_from_script(writer, "Scene 1: someone explains a thing.")
    assert lesson.title == "Authored elsewhere"
    assert len(writer.prompts) == 1
    assert "someone explains a thing" in writer.prompts[0]


def test_a_model_that_answers_in_prose_is_refused_readably():
    with pytest.raises(ValueError, match="did not return lesson JSON"):
        script_to_lesson(ScriptedWriter(["I would rather not."]), "prose")


def test_a_model_that_returns_a_broken_lesson_is_refused_readably():
    """parse_lesson is what catches it, so the message names the real problem."""
    with pytest.raises(ValueError, match="no sections"):
        script_to_lesson(ScriptedWriter(['{"title": "T", "sections": []}']), "prose")


# ------------------------------------------------------- the shipped lessons

LESSONS = Path(__file__).resolve().parents[2] / "lessons"


def shipped() -> list[Path]:
    return sorted(LESSONS.glob("*.json")) if LESSONS.is_dir() else []


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.stem)
def test_shipped_lessons_load(path):
    assert load_lesson(path).sections


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.stem)
def test_shipped_lesson_narration_is_one_sentence_per_entry(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for i, raw in enumerate(data["sections"], start=1):
        for j, entry in enumerate(raw.get("narration", [])):
            assert len(sentences(str(entry))) == 1, (
                f"section {i} ({raw.get('name')}) narration[{j}] holds "
                f"{len(sentences(str(entry)))} sentences; each entry must be "
                f"one sentence or the slide reveals out of step with the voice"
            )


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.stem)
def test_shipped_lessons_have_enough_sentences_to_reveal_and_land_the_note(path):
    """The heading holds for one sentence, then one sentence per column, then
    the note. Fewer sentences than that and the note never appears at all."""
    lesson = load_lesson(path)
    for section in lesson.sections:
        if not isinstance(section, Explanation):
            continue
        pieces = len(section.slide.columns) or len(section.slide.sequence)
        if not section.slide.note:
            continue
        n = len(sentences(" ".join(section.sentences)))
        assert n >= pieces + 1, (
            f"{path.name}: {section.name!r} has {n} sentences for {pieces} "
            f"columns, so its note never reaches the screen"
        )


@pytest.mark.parametrize("path", shipped(), ids=lambda p: p.stem)
def test_shipped_lessons_build(path):
    tutorial = build_lesson(load_lesson(path), speaker=ToneSpeaker(), execute=False)
    assert tutorial.plan.total_frames > 0
    assert tutorial.plan.captions
