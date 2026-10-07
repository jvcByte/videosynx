"""The pipeline above the renderer: prompts, parsing, and build_tutorial.

Everything here runs against stand-ins -- a scripted writer and a tone speaker
-- so the whole path is exercised with no key, no quota and no spend. That is
the point of putting the two services behind protocols.
"""

from __future__ import annotations

import io
import json
import os
import urllib.error

import numpy as np
import pytest

from videosynx.prompts import (
    MAX_SLIDES,
    code_prompt,
    concept_prompt,
    intro_prompt,
    narration_prompt,
    parse_anchored,
    parse_code,
    parse_slides,
    sentences,
)
from videosynx.providers import ProviderError, ScriptedWriter
from videosynx.render import render_plan
from videosynx.script import SLIDE_PLAN, build_tutorial, resolve_marker, speak_anchored
from videosynx.timeline import SR, Explanation, Phrase, Snippet

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
    assert "real system" in prompt
    assert "production" in prompt
    # The same system throughout, not a different toy per snippet.
    assert "same across every block" in prompt


def test_the_code_prompt_puts_the_topic_before_the_scenario():
    """The failure this was written for: a video on Python data types whose
    code was a webhook dispatcher.

    "Ground everything in one concrete scenario" was read as licence to pick
    any production system, and the model picked one from the prompt's own
    examples. The scenario then replaced the subject -- four of the six
    sections were about webhooks and said nothing about data types.
    """
    prompt = flat(code_prompt("Python data types"))
    assert "The topic is the subject" in prompt
    assert "because the topic is" in prompt and "what turns on it" in prompt
    # And the example that caused it is gone: naming domains in the prompt is
    # what the model reached for instead of choosing one to fit the topic.
    assert "a webhook dispatcher, a billing pipeline" not in prompt
    # A line that is not there because of the topic is out of scope.
    assert "not there because of the topic" in prompt


def test_the_code_prompt_asks_for_nothing_unexplained():
    prompt = flat(code_prompt("Python data types"))
    assert "Introduce nothing the narration will not explain" in prompt
    assert "saying what it is before it is used" in prompt


def test_the_code_prompt_bans_placeholder_names():
    prompt = code_prompt("x")
    assert "foo" in prompt and "bar" in prompt  # named as things not to use
    assert "Never" in prompt


def test_the_intro_prompt_puts_the_subject_before_the_scenario():
    """Asked for Python basics, the video opened on "In an online store
    inventory system..." -- the scenario, which the viewer had not asked for
    and could not place, before the thing they did ask for had been named
    once. The subject goes first."""
    prompt = flat(intro_prompt("Decorators"))
    assert "first sentence says what the topic is" in prompt
    assert "after the subject, never instead of it" in prompt


def test_the_intro_prompt_still_asks_where_it_applies():
    """Real-world grounding is the point of the product; this is only about
    the order it arrives in."""
    prompt = flat(intro_prompt("Decorators"))
    assert "where it applies" in prompt


def flat(text: str) -> str:
    """Collapse wrapping, so a phrase can be checked across a line break."""
    return " ".join(text.split())


def test_the_narration_prompt_asks_what_it_is_for():
    """Knowing what the code does is not the same as knowing why it exists."""
    prompt = flat(narration_prompt(CODE))
    assert "scenario's terms" in prompt
    assert "what would go wrong without it" in prompt
    assert "not only what it does" in prompt


def test_the_narration_prompt_bans_saying_the_word_underscore():
    """The rule that produced "http underscore status underscore code".

    It asked for variables to be "named the way a person would say them,
    spelling out underscores", which the model read literally -- and the
    narration spelled every name out character by character as a result.
    """
    prompt = flat(narration_prompt(CODE))
    assert "Never say the word \"underscore\"" in prompt
    assert "max_retry_attempts" in prompt  # given as the example to say aloud


def test_the_narration_prompt_defines_a_word_the_first_time_it_appears():
    """A viewer who has to guess what a word means has stopped following."""
    prompt = flat(narration_prompt(CODE))
    assert "never assume one" in prompt
    assert "say what it is in that same sentence" in prompt


def test_the_concept_prompt_asks_for_the_actual_words():
    """The slides for the data-types video said "raw data has no meaning on
    its own" and never named one type the viewer would take away."""
    prompt = flat(concept_prompt("Python data types", "", "one to three"))
    assert "Name the actual things" in prompt
    assert "`dict`" in prompt
    assert "never use a word before it is defined" in prompt


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
    from videosynx.timeline import build_plan, Snippet

    phrases = (
        Phrase("One.", ToneSpeaker().say("One."), anchor=0),
        Phrase("Two.", ToneSpeaker().say("Two."), anchor=None),
        Phrase("Three.", ToneSpeaker().say("Three."), anchor=len(CODE)),
    )
    plan = build_plan([Snippet(code=CODE, phrases=phrases)])
    assert plan.total_frames > 0
    # The middle sentence is interpolated, so it sits between its neighbours.
    from videosynx.timeline import resolve_anchors

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


CLOSING = ("That is the running total: a loop, and one variable carried through it. "
           "You can now add up anything that arrives one item at a time.")


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
        responses.append(CLOSING)
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

    # The opening is gone; the tutorial is not. The closing card is its own
    # call and still lands, so what must be absent is a card at the *start*.
    assert tutorial.plan.spans[0].state.title is None
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
    from videosynx.providers import GeminiWriter

    with pytest.raises(ProviderError):
        GeminiWriter("")


def test_elevenlabs_speaker_requires_keys():
    from videosynx.providers import ElevenLabsSpeaker

    with pytest.raises(ProviderError):
        ElevenLabsSpeaker("", "voice")
    with pytest.raises(ProviderError):
        ElevenLabsSpeaker("key", "")


def test_a_spent_allowance_is_reported_as_one_fact():
    """Five copies of a 429 body made a job's error message unreadable.

    The message is stored on the job and shown on the page, so it has to be
    something a person can act on.
    """
    from videosynx.providers import ProviderError, summarise

    spent = [(model, ProviderError("daily quota exhausted", reason="quota"))
             for model in ("a", "b", "c", "d", "e")]
    message, reason = summarise(spent)

    assert reason == "quota"
    assert "5 models" in message
    assert "resets daily" in message
    assert len(message) < 140, "still too long to read on a page"


def test_a_single_failure_keeps_the_part_you_can_act_on():
    """The common case now: one key, one provider, one model. A free tier
    that renamed a model answers 404, and the name it was asked for is the
    only thing that makes that fixable."""
    from videosynx.providers import ProviderError, summarise

    message, reason = summarise([
        ("openrouter/old-model-name", ProviderError("HTTP 404: model not found")),
    ])
    assert reason == "error"
    assert "404" in message and "old-model-name" not in message
    assert "every model" not in message, "there is only one"

    message, reason = summarise([
        ("groq", ProviderError("daily quota exhausted", reason="quota")),
    ])
    assert reason == "quota"
    assert "resets daily" in message


def test_a_mixed_failure_keeps_the_detail():
    """With no single cause, the specific failures are the useful part."""
    from videosynx.providers import ProviderError, summarise

    message, reason = summarise([
        ("a", ProviderError("quota", reason="quota")),
        ("b", ProviderError("503 unavailable", reason="busy")),
    ])
    assert reason == "error"
    assert "a:" in message and "b:" in message


def test_a_provider_error_carries_its_reason():
    from videosynx.providers import ProviderError

    assert ProviderError("x").reason == "error"
    assert ProviderError("x", reason="quota").reason == "quota"


def test_scripted_writer_replays_and_records():
    writer = ScriptedWriter(["a", "b"])
    assert writer.write("first") == "a"
    assert writer.write("second") == "b"
    # Past the end it keeps returning the last response rather than stopping.
    assert writer.write("third") == "b"
    assert writer.prompts == ["first", "second", "third"]


# ------------------------------------------------- other free model tiers
#
# A spent free allowance is the ordinary way this runs, so the fallback is the
# path that gets used rather than the path that might. None of it touches the
# network here: the HTTP call is passed in.

KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "HF_TOKEN", "HUGGINGFACE_API_KEY",
        "OPENROUTER_API_KEY")


@pytest.fixture
def no_keys(monkeypatch):
    """Whatever the shell that ran pytest happens to have exported."""
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)


def _reply(text: str):
    """A stand-in for the network, shaped like a chat completion."""
    def post(url, body, headers, timeout):
        return {"choices": [{"message": {"content": text}}]}
    return post


def test_a_per_minute_limit_is_told_apart_from_a_spent_quota():
    """Both are 429, and treating a per-day quota as busy is what makes a
    chain sleep through its whole retry budget for nothing."""
    from videosynx.providers import classify_http

    assert classify_http(429, "rate limit exceeded, try again in 2s").reason == "busy"
    assert classify_http(429, "quota exceeded for today").reason == "quota"
    assert classify_http(429, "daily quota reached").reason == "quota"
    assert classify_http(503, "upstream is overloaded").reason == "busy"
    assert classify_http(400, "bad request").reason == "error"
    # OpenRouter says 402 and "insufficient credits" rather than 429.
    assert classify_http(402, "insufficient credits").reason == "quota"

    # A 5xx is read as a 5xx however its body is worded. Reading one as a
    # spent allowance is the difference between trying another provider now
    # and telling someone to come back tomorrow.
    assert classify_http(500, "quota service unavailable").reason == "busy"


def test_an_openai_compatible_writer_reads_a_reply():
    from videosynx.providers import OpenAICompatWriter

    writer = OpenAICompatWriter("k", "m", base_url="https://example.test/v1",
                                opener=_reply("  hello  "))
    assert writer.write("prompt") == "hello"


def test_every_request_carries_a_user_agent():
    """Cloudflare answers a request without one with a 403 and no explanation.

    urllib's default is "Python-urllib/3.x", which is what it got. Groq replies
    with the bare string "error code: 1010" and Hugging Face with an HTML page,
    so it reads as a bad key or a retired model name -- anything but the header
    that is actually missing. Everything here is behind the same edge.
    """
    from videosynx.providers import _post_json

    # Asserted at the one place a request is actually made, so a caller that
    # builds its own headers still gets one. _post_json is what the writers
    # call unless a test has stood in for the network, and a test standing in
    # for the network is not what gets blocked.
    import urllib.request
    captured = {}

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake_urlopen(request, timeout=None):
        captured.update(request.headers)
        return FakeResponse()

    real = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        _post_json("https://example.test/v1", b"{}", {"Content-Type": "application/json"}, 5)
    finally:
        urllib.request.urlopen = real
    assert captured.get("User-agent") or captured.get("User-Agent"), captured


def test_a_writer_falls_through_its_own_models_before_giving_up():
    from videosynx.providers import OpenAICompatWriter

    tried = []

    def post(url, body, headers, timeout):
        tried.append(json.loads(body)["model"])
        if len(tried) == 1:
            raise urllib.error.HTTPError(url, 429, "quota", {}, io.BytesIO(
                b'{"error": {"message": "daily quota exhausted"}}'))
        return {"choices": [{"message": {"content": "second model answered"}}]}

    writer = OpenAICompatWriter("k", "one", base_url="https://example.test/v1",
                                fallbacks=("two",), opener=post)
    assert writer.write("prompt") == "second model answered"
    assert tried == ["one", "two"]


def test_a_writer_with_no_key_says_so_rather_than_failing_later():
    from videosynx.providers import OpenAICompatWriter

    with pytest.raises(ProviderError, match="API key"):
        OpenAICompatWriter("", "m", base_url="https://example.test/v1")


def test_the_chain_moves_on_to_the_next_provider():
    """The behaviour the whole thing exists for: Gemini is spent, Groq answers."""
    from videosynx.providers import ChainWriter

    class Spent:
        def write(self, prompt):
            raise ProviderError("daily quota exhausted", reason="quota")

    chain = ChainWriter([("gemini", Spent()), ("groq", ScriptedWriter(["written"]))])
    assert chain.write("prompt") == "written"
    assert chain.names == ["gemini", "groq"]


def test_the_chain_reports_once_every_provider_is_spent():
    from videosynx.providers import ChainWriter

    class Spent:
        def write(self, prompt):
            raise ProviderError("daily quota exhausted", reason="quota")

    chain = ChainWriter([("gemini", Spent()), ("groq", Spent())])
    with pytest.raises(ProviderError) as caught:
        chain.write("prompt")
    assert caught.value.reason == "quota"
    assert "Gemini" not in str(caught.value), "reasons, not one provider's name"


def test_a_chain_of_nothing_is_refused():
    from videosynx.providers import ChainWriter

    with pytest.raises(ProviderError):
        ChainWriter([])


def test_only_the_providers_with_keys_are_offered(no_keys, monkeypatch):
    """A provider with no key is not offered and then failed into -- it is
    not offered, because a missing key is a configuration fact known here
    and a failure is something a job has to survive."""
    from videosynx.providers import default_writer

    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    assert default_writer().names == ["groq", "openrouter"]


def test_a_lone_provider_needs_no_chain_around_it(no_keys, monkeypatch):
    """One provider is not a fallback chain, and wrapping it in one would
    only put a layer between two things that say the same message."""
    from videosynx.providers import OpenAICompatWriter, default_writer

    monkeypatch.setenv("GROQ_API_KEY", "g")
    writer = default_writer()
    assert isinstance(writer, OpenAICompatWriter)
    assert writer.name == "groq"


def test_the_order_is_gemini_first(no_keys, monkeypatch):
    from videosynx.providers import ChainWriter, default_writer

    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    assert default_writer().names == ["gemini", "deepseek", "groq", "openrouter"]


def test_deepseek_is_reachable_on_its_own(no_keys, monkeypatch):
    """It is the only backend here that is paid rather than a free tier, and
    it speaks the same API as the free ones -- which is why it is a line in
    the chain rather than a fourth writer class."""
    from videosynx.providers import OpenAICompatWriter, default_writer

    monkeypatch.setenv("DEEPSEEK_API_KEY", "d")
    writer = default_writer()
    assert isinstance(writer, OpenAICompatWriter)
    assert writer.name == "deepseek"
    assert writer._url == "https://api.deepseek.com/v1/chat/completions"


def test_hugging_face_answers_to_either_name(no_keys, monkeypatch):
    from videosynx.providers import default_writer

    for name in ("HF_TOKEN", "HUGGINGFACE_API_KEY"):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
        monkeypatch.setenv(name, "h")
        assert default_writer().name == "huggingface"


def test_a_model_list_can_be_corrected_without_a_code_change(no_keys, monkeypatch):
    """A free tier renames its models often, and a stale name should be a
    environment variable away from fixed rather than a release."""
    from videosynx.providers import OpenAICompatWriter, default_writer

    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GROQ_MODELS", " new-one , second-one ,  ")
    writer = default_writer()
    assert isinstance(writer, OpenAICompatWriter)
    assert writer._models == ["new-one", "second-one"]

    # A value that names no models at all is a typo, not an instruction to
    # offer none -- which would fail on an empty list rather than use one.
    monkeypatch.setenv("GROQ_MODELS", " , ")
    from videosynx.providers import GROQ_MODELS
    assert default_writer()._models == list(GROQ_MODELS)


def test_no_keys_at_all_names_the_ones_that_would_work(no_keys):
    from videosynx.providers import default_writer

    with pytest.raises(ProviderError) as caught:
        default_writer()
    message = str(caught.value)
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "HF_TOKEN", "OPENROUTER_API_KEY"):
        assert name in message, f"{name} missing from the message a person reads"


# ------------------------------------------------------------------- dotenv

def test_env_file_is_parsed(tmp_path, monkeypatch):
    from videosynx.dotenv import load, parse

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
    from videosynx.dotenv import load

    assert load(tmp_path / "nope.env") == []


def test_the_env_file_is_found_from_a_subdirectory(tmp_path, monkeypatch):
    """A worker started from `web/` after `npm run dev` had no key at all.

    Nothing failed at startup -- the key was simply absent -- so it surfaced
    minutes later as a render dying with "no model key is configured" while
    the key sat in a file one directory up.
    """
    from videosynx.dotenv import find

    (tmp_path / ".git").mkdir()          # the repository root
    (tmp_path / ".env").write_text("FOUND=1\n")
    nested = tmp_path / "web" / "app"
    nested.mkdir(parents=True)

    assert find(nested) == tmp_path / ".env"
    assert find(tmp_path) == tmp_path / ".env"


def test_the_search_stops_at_the_repository_root(tmp_path):
    """A .env above the checkout is somebody else's and is not ours to read."""
    from videosynx.dotenv import find

    (tmp_path / ".env").write_text("OUTSIDE=1\n")
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()

    assert find(repo / "src") is None


def test_no_env_file_anywhere_is_not_an_error(tmp_path):
    from videosynx.dotenv import find

    (tmp_path / ".git").mkdir()
    assert find(tmp_path) is None


def test_the_video_ends_on_a_card():
    """A tutorial used to stop the moment the last line was read out.

    Not an ending -- where the file happened to run out. The closing card is
    the answer to "what did I just watch", and it is the last thing heard.
    """
    tutorial = build_tutorial("Running totals", writer=_scripted(),
                              speaker=ToneSpeaker(), execute=False)

    last = tutorial.plan.spans[-1]
    assert last.state.title == "Running totals"
    assert not last.state.code and last.state.slide is None

    # And the words are the ones written for the closing, not the opening.
    # Captions, not narrations: a Narration is audio and a frame offset, and
    # the captions are the only place the sentences survive as text.
    end = tutorial.plan.captions[-6:]
    spoken = " ".join(c.text for c in end).lower()
    assert "running total" in spoken, spoken


def test_the_closing_is_summarised_from_the_slides():
    """It summarises this video, not the topic in general."""
    writer = _scripted()
    build_tutorial("Running totals", writer=writer, speaker=ToneSpeaker(),
                   execute=False)

    closing = next(p for p in writer.prompts if "closing narration" in p)
    assert "How a running total works" in closing or "- " in closing


def test_a_failed_closing_card_does_not_lose_the_whole_video():
    """The same bargain as the opening: framing is written last, after
    everything paid for, and the video is finished without it."""
    class OutroFails(ScriptedWriter):
        def write(self, prompt: str) -> str:
            if "closing narration" in prompt:
                raise ProviderError("every model failed -- daily quota exhausted")
            return super().write(prompt)

    writer = OutroFails([CONCEPTS, f"```python\n{CODE}```\n", NARRATION, "Opening words."])
    tutorial = build_tutorial("Running totals", writer=writer,
                              speaker=ToneSpeaker(), execute=False)

    # One card, and it is the opening: the closing never made it. Counted as
    # runs, because a card is several spans -- one per caption and one of
    # held silence at the end.
    spans = tutorial.plan.spans
    runs = sum(
        1 for i, span in enumerate(spans)
        if span.state.title and (i == 0 or not spans[i - 1].state.title)
    )
    assert runs == 1, [s.state.title for s in spans]
    assert spans[0].state.title is not None
    assert any(span.state.code for span in tutorial.plan.spans)
    assert tutorial.plan.captions


def test_the_env_is_found_when_the_working_directory_is_nowhere_near_it(
        tmp_path, monkeypatch):
    """The working directory can be somewhere the project has never been.

    A shell whose directory is deleted falls back to `$HOME` -- which is what
    a terminal sitting in this repository did when the repository was
    renamed -- and a service started with no working directory does the same.
    The API came up that way, found no credentials, answered 503 to every
    request, and said nothing at startup about why.
    """
    from videosynx import dotenv

    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    (repo / ".env").write_text("FOUND=1\n")
    # Where the engine would be if it were this checkout's.
    module = repo / "engine" / "src" / "videosynx" / "dotenv.py"
    module.parent.mkdir(parents=True)
    module.write_text("")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(dotenv, "__file__", str(module))

    assert dotenv.find() == repo / ".env"

    # An explicit start is still obeyed rather than answered from the source:
    # the caller has said where to look.
    assert dotenv.find(elsewhere) is None


def test_a_checkout_with_no_env_beside_it_falls_back_to_nothing(
        tmp_path, monkeypatch):
    """Nothing above the working directory, and no .env in the source either."""
    from videosynx import dotenv

    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    module = repo / "engine" / "src" / "videosynx" / "dotenv.py"
    module.parent.mkdir(parents=True)
    module.write_text("")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(dotenv, "__file__", str(module))

    assert dotenv.find() is None
