"""The pipeline above the renderer: prompts, parsing, and build_tutorial.

Everything here runs against stand-ins -- a scripted writer and a tone speaker
-- so the whole path is exercised with no key, no quota and no spend. That is
the point of putting the two services behind protocols.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from pycoding.prompts import (
    MAX_SLIDES,
    code_prompt,
    intro_prompt,
    narration_prompt,
    parse_anchored,
    parse_code,
    parse_slides,
    sentences,
)
from pycoding.providers import ProviderError, ScriptedWriter
from pycoding.render import render_plan
from pycoding.script import SLIDE_PLAN, build_tutorial, resolve_marker, speak_anchored
from pycoding.timeline import SR, Explanation, Phrase, Snippet

CODE = (
    "total = 0\n"
    "for n in range(5):\n"
    "    total += n\n"
    "print(total)\n"
)

NARRATION = """@@ total = 0 @@
We start by setting a running total to zero.
@@ for n in range(5): @@
Then we walk over the numbers zero to four.
@@ total += n @@
Each one is added into the total as we go.
@@ print(total) @@
Finally we print the result.
"""


class ToneSpeaker:
    """A Speaker that returns a predictable tone, and records what it was asked."""

    def __init__(self, seconds: float = 1.0):
        self.seconds = seconds
        self.calls: list[tuple[str, str, str]] = []

    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        self.calls.append((text, previous, following))
        n = int(SR * self.seconds)
        t = np.arange(n, dtype=np.float64)
        return (np.sin(2 * np.pi * 300 * t / SR) * 8000).astype(np.int16)


# ------------------------------------------------------------------ prompts

def test_code_prompt_carries_the_topic_and_context():
    prompt = code_prompt("List comprehensions", "the repo uses numpy")
    assert "List comprehensions" in prompt
    assert "numpy" in prompt
    # Without context the placeholder must not leak into the prompt.
    assert "None provided" not in code_prompt("x")


def test_code_prompt_forbids_installation():
    """The sandbox has no network, so installing at runtime cannot work."""
    assert "Do not install anything" in code_prompt("x")


def test_narration_prompt_includes_the_code_and_the_marker_format():
    prompt = narration_prompt(CODE)
    assert CODE in prompt
    assert "@@" in prompt
    assert "under twenty words" in prompt


def test_intro_prompt_names_the_topic():
    assert "Decorators" in intro_prompt("Decorators")


# --------------------------------------------------- real-world grounding
# Tutorials made of `foo` and `[1, 2, 3]` teach the syntax and nothing else.
# The viewer should come away knowing where the code belongs.

def test_the_code_prompt_demands_a_real_scenario():
    prompt = code_prompt("Python decorators")
    assert "concrete scenario" in prompt
    assert "production" in prompt
    # The same system throughout, not a different toy per snippet.
    assert "same scenario across every block" in prompt


def test_the_code_prompt_bans_placeholder_names():
    prompt = code_prompt("x")
    assert "foo" in prompt and "bar" in prompt  # named as things not to use
    assert "Never" in prompt


def test_the_intro_prompt_asks_where_it_applies():
    prompt = intro_prompt("Decorators")
    assert "real-world scenario" in prompt
    assert "which kind of system" in prompt


def flat(text: str) -> str:
    """Collapse wrapping, so a phrase can be checked across a line break."""
    return " ".join(text.split())


def test_the_narration_prompt_asks_what_it_is_for():
    """Knowing what the code does is not the same as knowing why it exists."""
    prompt = flat(narration_prompt(CODE))
    assert "scenario's terms" in prompt
    assert "what would go wrong without it" in prompt
    assert "not only what it does" in prompt


# ------------------------------------------------------------------ parsing

def test_parse_code_extracts_blocks():
    raw = "here:\n```python\nx = 1\n```\nand:\n```python\ny = 2\n```\n"
    assert parse_code(raw) == ["x = 1", "y = 2"]


def test_parse_code_raises_on_no_code():
    """An empty result used to produce a job with nothing in it."""
    with pytest.raises(ValueError):
        parse_code("I'm sorry, I can't help with that.")


def test_parse_anchored_pairs_markers_with_sentences():
    pairs = parse_anchored(NARRATION)
    assert [m for m, _ in pairs] == [
        "total = 0", "for n in range(5):", "total += n", "print(total)"
    ]
    assert pairs[0][1].startswith("We start by setting")


def test_parse_anchored_tolerates_multiline_sentences_and_junk():
    raw = (
        "Sure! Here is the narration:\n\n"
        "@@ x = 1 @@\n"
        "First we set x,\n"
        "and say why.\n\n"
        "@@ y = 2 @@\n"
        "Then y.\n"
        "\nSome trailing commentary.\n"
    )
    pairs = parse_anchored(raw)
    assert len(pairs) == 2
    assert pairs[0] == ("x = 1", "First we set x, and say why.")


def test_parse_anchored_returns_nothing_for_plain_prose():
    assert parse_anchored("Just a paragraph with no markers.") == []


def test_sentences_splits_prose():
    assert sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]


# ------------------------------------------------------------------ anchors

def test_resolve_marker_exact_and_fallback():
    assert resolve_marker(CODE, "total += n") == CODE.index("total += n")
    # The model may reformat whitespace; a whole-line match still lands.
    assert resolve_marker(CODE, "    total += n  \n") == CODE.index("total += n")
    assert resolve_marker(CODE, "not in this code at all") is None


def test_speak_anchored_passes_prosody_context():
    speaker = ToneSpeaker()
    pairs = parse_anchored(NARRATION)
    phrases = speak_anchored(speaker, CODE, pairs)
    assert len(phrases) == 4
    # Neighbours are handed to the synthesizer, which is what stops every
    # sentence getting its own clipped final intonation.
    assert speaker.calls[0][1] == "" and speaker.calls[0][2].startswith("Then we walk")
    assert speaker.calls[1][1].startswith("We start") and speaker.calls[1][2].startswith("Each")
    assert all(isinstance(p, Phrase) and p.anchor is not None for p in phrases)


def test_unanchored_sentences_get_spread_not_stacked():
    """A marker that does not match must not collapse the sentence to the start."""
    from pycoding.timeline import build_plan, Snippet

    phrases = (
        Phrase("One.", ToneSpeaker().say("One."), anchor=0),
        Phrase("Two.", ToneSpeaker().say("Two."), anchor=None),
        Phrase("Three.", ToneSpeaker().say("Three."), anchor=len(CODE)),
    )
    plan = build_plan([Snippet(code=CODE, phrases=phrases)])
    assert plan.total_frames > 0
    # The middle sentence is interpolated, so it sits between its neighbours.
    from pycoding.timeline import resolve_anchors

    anchors = resolve_anchors(phrases, len(CODE))
    assert anchors[0] == 0
    assert anchors[1] == len(CODE) // 2


# ------------------------------------------------------------- build_tutorial

CONCEPTS = """Here are the slides:

```json
[
  {
    "heading": "A running total",
    "columns": [
      ["Start at zero", ["nothing counted yet"]],
      ["Add as you go", ["each step updates it"]]
    ],
    "note": "A running total is a value you carry forward.",
    "narration": [
      "First, the idea of a running total.",
      "It starts at zero, with nothing counted yet.",
      "Then each step adds to it.",
      "A running total is a value you carry forward."
    ]
  }
]
```
"""


def _scripted_for(mode: str = "both", include_intro: bool = True) -> ScriptedWriter:
    """Responses in the order the given mode asks for them."""
    responses: list[str] = []
    if mode != "coding":
        responses.append(CONCEPTS)
    if mode != "illustration":
        responses.append(f"Here you go:\n```python\n{CODE}```\n")
        responses.append(NARRATION)
    if include_intro:
        responses.append("In this video we build up a running total step by step. "
                         "We start with a loop, then add each number into it.")
    return ScriptedWriter(responses)


def _scripted(include_intro: bool = True) -> ScriptedWriter:
    return _scripted_for("both", include_intro)


def test_build_tutorial_produces_a_renderable_plan():
    writer = _scripted()
    speaker = ToneSpeaker(seconds=1.0)
    tutorial = build_tutorial("Running totals", writer=writer, speaker=speaker,
                              execute=False)

    plan = tutorial.plan
    assert plan.total_frames > 0
    assert plan.captions, "no captions were produced"
    # The concept slide, then the snippet.
    assert len(tutorial.sections) == 2
    # Intro card, then the slide, then the code.
    assert plan.spans[0].state.title == "Running totals"
    assert any(s.state.slide for s in plan.spans)
    assert any(s.state.code for s in plan.spans)
    # The narration prompt was built from the generated code.
    assert any(CODE in prompt for prompt in writer.prompts)


# ------------------------------------------------------------- concept phase
# Without a concept phase the video opens on a production example and never
# says what the topic means: a string is parsed before anyone has said what a
# string is.

def test_concept_slides_come_before_the_code():
    tutorial = build_tutorial("Running totals", writer=_scripted(),
                              speaker=ToneSpeaker(), execute=False)
    kinds = [type(s).__name__ for s in tutorial.sections]
    assert kinds == ["Explanation", "Snippet"]

    # And on screen, not just in the section list.
    first_slide = next(i for i, s in enumerate(tutorial.plan.spans)
                       if s.state.slide is not None)
    first_code = next(i for i, s in enumerate(tutorial.plan.spans) if s.state.code)
    assert first_slide < first_code


def test_the_concept_slide_carries_its_columns_and_note():
    tutorial = build_tutorial("Running totals", writer=_scripted(),
                              speaker=ToneSpeaker(), execute=False)
    slide = tutorial.sections[0].slide
    assert slide.heading == "A running total"
    assert [title for title, _ in slide.columns] == ["Start at zero", "Add as you go"]
    assert slide.note == "A running total is a value you carry forward."


def test_the_slide_reveals_one_column_per_sentence():
    tutorial = build_tutorial("Running totals", writer=_scripted(),
                              speaker=ToneSpeaker(), execute=False)
    seen: list[int] = []
    for span in tutorial.plan.spans:
        if span.state.slide is not None:
            if not seen or seen[-1] != span.state.slide.revealed:
                seen.append(span.state.slide.revealed)
    # Heading alone, then one column per sentence, then the note.
    assert seen == [0, 1, 2]


# ------------------------------------------------------------------- modes
# A viewer who wants a coding walkthrough should not sit through slides, and
# one who wants the idea drawn out should not be shown code at all.

def build(mode: str, **kwargs):
    return build_tutorial("Running totals", writer=_scripted_for(mode),
                          speaker=ToneSpeaker(), execute=False, mode=mode, **kwargs)


def test_mode_coding_has_code_and_no_slides():
    tutorial = build("coding")
    assert tutorial.sections and all(isinstance(s, Snippet) for s in tutorial.sections)
    assert not any(span.state.slide for span in tutorial.plan.spans)
    assert any(span.state.code for span in tutorial.plan.spans)


def test_mode_illustration_has_slides_and_no_code():
    tutorial = build("illustration")
    assert all(isinstance(s, Explanation) for s in tutorial.sections)
    assert any(span.state.slide for span in tutorial.plan.spans)
    assert not any(span.state.code for span in tutorial.plan.spans)


def test_mode_both_produces_both_and_is_the_default():
    explicit, default = build("both"), build_tutorial(
        "Running totals", writer=_scripted(), speaker=ToneSpeaker(), execute=False)
    for tutorial in (explicit, default):
        kinds = {type(s).__name__ for s in tutorial.sections}
        assert kinds == {"Explanation", "Snippet"}


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="mode must be one of"):
        build("podcast")


def test_illustration_mode_asks_for_more_slides_than_both():
    """With no code to carry the runtime, the slides have to."""
    assert SLIDE_PLAN["illustration"][1] > SLIDE_PLAN["both"][1]


def test_max_snippets_caps_the_code():
    """The tier declares this cap, so something has to enforce it."""
    many = "here:\n" + "".join(
        f"```python\nprint({i})\n```\n" for i in range(5)
    )
    writer = ScriptedWriter([CONCEPTS, many] + [NARRATION] * 5 +
                            ["A short intro."])
    tutorial = build_tutorial("Running totals", writer=writer,
                              speaker=ToneSpeaker(), execute=False, max_snippets=2)
    assert len([s for s in tutorial.sections if isinstance(s, Snippet)]) == 2


def test_no_cap_keeps_every_snippet():
    many = "here:\n" + "".join(
        f"```python\nprint({i})\n```\n" for i in range(5)
    )
    writer = ScriptedWriter([CONCEPTS, many] + [NARRATION] * 5 +
                            ["A short intro."])
    tutorial = build_tutorial("Running totals", writer=writer,
                              speaker=ToneSpeaker(), execute=False)
    assert len([s for s in tutorial.sections if isinstance(s, Snippet)]) == 5


def test_a_video_still_renders_when_the_concepts_cannot_be_parsed():
    """Degrade to opening on the code, rather than failing the job."""
    writer = ScriptedWriter([
        "I would rather describe it in prose than give you JSON.",
        f"```python\n{CODE}```\n",
        NARRATION,
        "A short intro.",
    ])
    tutorial = build_tutorial("Running totals", writer=writer,
                              speaker=ToneSpeaker(), execute=False)
    assert all(isinstance(s, Snippet) for s in tutorial.sections)
    assert tutorial.plan.captions


def test_a_failed_title_card_does_not_lose_the_whole_video():
    """The card is written last, once everything else has been generated.

    When the model ran out of daily quota on that final call, it took a
    finished tutorial -- concepts, code and every line of narration, all
    already paid for -- down with it.
    """
    class IntroFails(ScriptedWriter):
        def write(self, prompt: str) -> str:
            if "opening narration" in prompt:
                raise ProviderError("every model failed -- daily quota exhausted")
            return super().write(prompt)

    writer = IntroFails([CONCEPTS, f"```python\n{CODE}```\n", NARRATION])
    tutorial = build_tutorial("Running totals", writer=writer,
                              speaker=ToneSpeaker(), execute=False)

    assert all(span.state.title is None for span in tutorial.plan.spans)
    assert tutorial.plan.captions
    assert any(span.state.slide for span in tutorial.plan.spans)
    assert any(span.state.code for span in tutorial.plan.spans)


def test_parse_slides_reads_fenced_json_through_prose():
    slides = parse_slides(CONCEPTS)
    assert len(slides) == 1
    assert slides[0]["heading"] == "A running total"
    assert slides[0]["columns"][0] == ("Start at zero", ("nothing counted yet",))
    assert len(slides[0]["narration"]) == 4


def test_parse_slides_rejects_what_it_cannot_use():
    assert parse_slides("no json here at all") == []
    assert parse_slides("[not valid json") == []
    assert parse_slides('{"heading": "not a list"}') == []
    # A slide with no columns can never reveal; one with no narration is silent.
    assert parse_slides('[{"heading": "H", "narration": ["s"]}]') == []
    assert parse_slides('[{"heading": "H", "columns": [["a", ["b"]]]}]') == []


def test_parse_slides_caps_a_runaway_response():
    many = json.dumps([
        {"heading": f"H{i}", "columns": [["a", ["b"]]], "narration": ["s"]}
        for i in range(20)
    ])
    assert len(parse_slides(many)) == MAX_SLIDES


def test_build_tutorial_survives_a_model_that_ignores_the_format():
    """Prose instead of markers must degrade, not crash."""
    writer = ScriptedWriter([
        "Some prose instead of the JSON I asked for.",
        f"```python\n{CODE}```\n",
        "The total starts at zero. Then we add each number in turn.",
        "A short intro.",
    ])
    tutorial = build_tutorial("Running totals", writer=writer,
                              speaker=ToneSpeaker(seconds=1.0), execute=False)
    assert tutorial.plan.captions, "unanchored narration produced no captions"


def test_build_tutorial_rejects_an_empty_topic():
    with pytest.raises(ValueError):
        build_tutorial("   ", writer=_scripted(), speaker=ToneSpeaker(), execute=False)


def test_build_tutorial_can_skip_the_intro():
    tutorial = build_tutorial("Running totals", writer=_scripted(), speaker=ToneSpeaker(),
                              execute=False, intro=False)
    assert all(s.state.title is None for s in tutorial.plan.spans)


def test_build_tutorial_renders(tmp_path):
    tutorial = build_tutorial("Running totals", writer=_scripted(), speaker=ToneSpeaker(),
                              execute=False)
    path = tmp_path / "out.mp4"
    render_plan(tutorial.plan, path, width=960, height=540)
    assert path.exists() and path.stat().st_size > 0


# ------------------------------------------------------------------ providers

def test_gemini_writer_requires_a_key():
    from pycoding.providers import GeminiWriter

    with pytest.raises(ProviderError):
        GeminiWriter("")


def test_elevenlabs_speaker_requires_keys():
    from pycoding.providers import ElevenLabsSpeaker

    with pytest.raises(ProviderError):
        ElevenLabsSpeaker("", "voice")
    with pytest.raises(ProviderError):
        ElevenLabsSpeaker("key", "")


def test_a_spent_allowance_is_reported_as_one_fact():
    """Five copies of a 429 body made a job's error message unreadable.

    The message is stored on the job and shown on the page, so it has to be
    something a person can act on.
    """
    from pycoding.providers import ProviderError, summarise

    spent = [(model, ProviderError("daily quota exhausted", reason="quota"))
             for model in ("a", "b", "c", "d", "e")]
    message, reason = summarise(spent)

    assert reason == "quota"
    assert "5 models" in message
    assert "resets daily" in message
    assert len(message) < 140, "still too long to read on a page"


def test_a_mixed_failure_keeps_the_detail():
    """With no single cause, the specific failures are the useful part."""
    from pycoding.providers import ProviderError, summarise

    message, reason = summarise([
        ("a", ProviderError("quota", reason="quota")),
        ("b", ProviderError("503 unavailable", reason="busy")),
    ])
    assert reason == "error"
    assert "a:" in message and "b:" in message


def test_a_provider_error_carries_its_reason():
    from pycoding.providers import ProviderError

    assert ProviderError("x").reason == "error"
    assert ProviderError("x", reason="quota").reason == "quota"


def test_scripted_writer_replays_and_records():
    writer = ScriptedWriter(["a", "b"])
    assert writer.write("first") == "a"
    assert writer.write("second") == "b"
    # Past the end it keeps returning the last response rather than stopping.
    assert writer.write("third") == "b"
    assert writer.prompts == ["first", "second", "third"]


# ------------------------------------------------------------------- dotenv

def test_env_file_is_parsed(tmp_path, monkeypatch):
    from pycoding.dotenv import load, parse

    assert parse('A=1\n# comment\n\nB = "two words" \n') == {
        "A": "1", "B": "two words"
    }
    # A shell export is an instruction for this run; a stored default must not
    # override it.
    monkeypatch.setenv("A", "from-shell")
    path = tmp_path / ".env"
    path.write_text("A=from-file\nB=from-file\n")
    assert load(path) == ["B"]
    assert os.environ["A"] == "from-shell"
    assert os.environ["B"] == "from-file"


def test_a_missing_env_file_is_not_an_error(tmp_path):
    from pycoding.dotenv import load

    assert load(tmp_path / "nope.env") == []
