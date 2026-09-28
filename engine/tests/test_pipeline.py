"""The pipeline above the renderer: prompts, parsing, and build_tutorial.

Everything here runs against stand-ins -- a scripted writer and a tone speaker
-- so the whole path is exercised with no key, no quota and no spend. That is
the point of putting the two services behind protocols.
"""

from __future__ import annotations

import numpy as np
import pytest

from pycoding.prompts import (
    code_prompt,
    intro_prompt,
    narration_prompt,
    parse_anchored,
    parse_code,
    sentences,
)
from pycoding.providers import ProviderError, ScriptedWriter
from pycoding.render import render_plan
from pycoding.script import build_tutorial, resolve_marker, speak_anchored
from pycoding.timeline import SR, Phrase

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

def _scripted(include_intro: bool = True) -> ScriptedWriter:
    responses = [
        f"Here you go:\n```python\n{CODE}```\n",
        NARRATION,
        "In this video we build up a running total step by step. "
        "We start with a loop, then add each number into it."
        if include_intro else "",
    ]
    return ScriptedWriter([r for r in responses if r])


def test_build_tutorial_produces_a_renderable_plan():
    writer = _scripted()
    speaker = ToneSpeaker(seconds=1.0)
    tutorial = build_tutorial("Running totals", writer=writer, speaker=speaker,
                              execute=False)

    plan = tutorial.plan
    assert plan.total_frames > 0
    assert plan.captions, "no captions were produced"
    assert len(tutorial.sections) == 1
    # Intro card, then the snippet.
    assert plan.spans[0].state.title == "Running totals"
    assert any(s.state.code for s in plan.spans)
    # The narration prompt was built from the generated code.
    assert CODE in writer.prompts[1]


def test_build_tutorial_survives_a_model_that_ignores_the_format():
    """Prose instead of markers must degrade, not crash."""
    writer = ScriptedWriter([
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


def test_scripted_writer_replays_and_records():
    writer = ScriptedWriter(["a", "b"])
    assert writer.write("first") == "a"
    assert writer.write("second") == "b"
    # Past the end it keeps returning the last response rather than stopping.
    assert writer.write("third") == "b"
    assert writer.prompts == ["first", "second", "third"]
