"""Turn a topic into a render plan.

This is the whole pipeline above the renderer: ask for code, run it, ask for
narration against each snippet, speak it, and lay the result out on the frame
grid. It talks to the outside world only through ``Writer`` and ``Speaker``,
so the entire thing runs offline against stand-ins.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.console import Console

from .execute import run_snippet
from .narration import trim_silence
from .prompts import (
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
from .providers import ProviderError, Speaker, Writer
from .sandbox import DEFAULT_TIMEOUT_S, assert_sandbox_available
from .timeline import Explanation, FramePlan, Intro, Phrase, Slide, Snippet, build_plan

_console = Console()

# Which halves of a tutorial to produce.
MODES = ("coding", "illustration", "both")

# What to ask the model for, and how much of it to accept: (wording, cap).
# An illustration-only video has no code, so its slides have to carry the whole
# runtime rather than merely introduce it.
SLIDE_PLAN = {
    "both": ("one to three", MAX_SLIDES),
    "illustration": ("four to six", 6),
}


def resolve_marker(code: str, marker: str) -> int | None:
    """Where *marker* sits in *code*, or None if it cannot be found.

    The model is asked to copy a fragment exactly, but whitespace and
    reformatting drift. Falling back to the fragment's own lines recovers most
    of those; returning None lets the caller interpolate instead of silently
    anchoring everything to character zero.
    """
    marker = marker.strip()
    if marker and marker in code:
        return code.index(marker)
    for line in marker.splitlines():
        line = line.strip()
        if line and line in code:
            return code.index(line)
    return None


def speak_anchored(
    speaker: Speaker, code: str, pairs: list[tuple[str, str]]
) -> tuple[Phrase, ...]:
    """Synthesise each sentence, keeping its neighbour for prosody context."""
    phrases = []
    for i, (marker, sentence) in enumerate(pairs):
        previous = pairs[i - 1][1] if i else ""
        following = pairs[i + 1][1] if i + 1 < len(pairs) else ""
        audio = trim_silence(speaker.say(sentence, previous=previous, following=following))
        phrases.append(Phrase(sentence, audio, resolve_marker(code, marker)))
    return tuple(phrases)


def speak_plain(speaker: Speaker, text: str) -> tuple[Phrase, ...]:
    """Synthesise unanchored prose, one sentence at a time."""
    return tuple(
        Phrase(s, trim_silence(speaker.say(s))) for s in sentences(text)
    )


def concept_slides(
    writer: Writer, speaker: Speaker, topic: str, context: str = "",
    *, slide_range: str = "one to three", limit: int = MAX_SLIDES,
) -> list[Explanation]:
    """The slides that explain the idea before the first line of code.

    Without these the video opens on a production example and never says what
    the topic means -- the viewer is shown a string being parsed before anyone
    has told them what a string is.
    """
    _console.log("[blue]Writing the concept slides[/blue]")
    slides: list[Explanation] = []
    raw_slides = writer.write(concept_prompt(topic, context, slide_range))
    for raw in parse_slides(raw_slides, limit):
        phrases = tuple(
            phrase
            for sentence in raw["narration"]  # one entry per spoken sentence
            for phrase in speak_plain(speaker, sentence)
        )
        if not phrases:
            continue
        slides.append(
            Explanation(
                slide=Slide(heading=raw["heading"], columns=raw["columns"],
                            diagram=raw.get("diagram"), note=raw["note"]),
                phrases=phrases,
            )
        )
    if slides:
        _console.log(f"[green]{len(slides)} concept slides[/green]")
    else:
        _console.log("[yellow]No concept slides; the video opens on the code[/yellow]")
    return slides


@dataclass
class Tutorial:
    plan: FramePlan
    sections: list[Snippet | Explanation]
    topic: str

    def export(self, path: Path) -> Path:
        """Write the generated script to JSON.

        Without this the generated code exists only as pixels in the video,
        which makes a bad run impossible to diagnose without paying for
        another one.
        """
        data = {
            "topic": self.topic,
            "total_frames": self.plan.total_frames,
            "duration_s": self.plan.duration_s,
            "sections": [
                {
                    "kind": "explain",
                    "heading": section.slide.heading,
                    # The whole slide, so a bad one can be inspected and
                    # hand-edited into a lesson rather than guessed at.
                    "columns": [[title, list(lines)]
                                for title, lines in section.slide.columns],
                    "diagram": (asdict(section.slide.diagram)
                                if section.slide.diagram else None),
                    "note": section.slide.note,
                    "narration": [
                        {"text": p.text, "frames": p.n_frames}
                        for p in section.phrases
                    ],
                }
                if isinstance(section, Explanation)
                else {
                    "kind": "code",
                    "code": section.code,
                    "output": list(section.output),
                    "narration": [
                        {
                            "text": phrase.text,
                            "anchor": phrase.anchor,
                            "anchored": phrase.anchor is not None,
                            "frames": phrase.n_frames,
                        }
                        for phrase in section.phrases
                    ],
                }
                for section in self.sections
            ],
            "captions": [
                {"text": c.text, "start_frame": c.start_frame, "end_frame": c.end_frame}
                for c in self.plan.captions
            ],
        }
        path = Path(path)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path


def code_sections(
    writer: Writer,
    speaker: Speaker,
    topic: str,
    context: str = "",
    *,
    execute: bool = True,
    max_snippets: int | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    workdir: Path | None = None,
) -> list[Snippet]:
    """Write, run and narrate the coded half of a tutorial."""
    _console.log(f"[blue]Writing code for:[/blue] {topic}")
    snippets_code = parse_code(writer.write(code_prompt(topic, context)))
    if max_snippets is not None and len(snippets_code) > max_snippets:
        # The tier caps how much renders. Say so, rather than quietly
        # dropping the tail and leaving a video that stops mid-thought.
        _console.log(f"[yellow]keeping {max_snippets} of {len(snippets_code)} "
                     f"snippets (tier limit)[/yellow]")
        snippets_code = snippets_code[:max_snippets]
    _console.log(f"[green]{len(snippets_code)} snippets[/green]")

    if execute:
        # Refuse before generating anything that will be run.
        assert_sandbox_available()

    built: list[Snippet] = []
    with tempfile.TemporaryDirectory(prefix="videosynx_") as td:
        root = Path(workdir) if workdir else Path(td)
        for i, code in enumerate(snippets_code):
            output: tuple[str, ...] = ()
            if execute:
                _console.log(f"[blue]Running snippet {i + 1}[/blue]")
                execution = run_snippet(
                    code, workdir=root / f"snippet_{i}", timeout_s=timeout_s
                )
                output = execution.lines

            _console.log(f"[blue]Narrating snippet {i + 1}[/blue]")
            pairs = parse_anchored(writer.write(narration_prompt(code)))
            if not pairs:
                # The model ignored the format; degrade rather than fail.
                _console.log(
                    f"[yellow]Snippet {i + 1}: no anchored narration, "
                    "narration will be unanchored[/yellow]"
                )
            unanchored = sum(1 for marker, _ in pairs if resolve_marker(code, marker) is None)
            if unanchored:
                _console.log(
                    f"[yellow]Snippet {i + 1}: {unanchored} of {len(pairs)} "
                    "anchors did not match the code[/yellow]"
                )
            built.append(
                Snippet(code=code, output=output, phrases=speak_anchored(speaker, code, pairs))
            )
    return built


def build_tutorial(
    topic: str,
    *,
    writer: Writer,
    speaker: Speaker,
    context: str = "",
    execute: bool = True,
    intro: bool = True,
    mode: str = "both",
    max_snippets: int | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    workdir: Path | None = None,
) -> Tutorial:
    """Generate a complete render plan for *topic*.

    ``mode`` decides which halves are produced: ``coding`` is code alone,
    ``illustration`` is slides alone, ``both`` is one after the other, concepts
    first. An illustration-only video has no code to run, so it needs no
    sandbox and its slides have to carry the whole runtime -- hence the larger
    slide count.

    The work directory is temporary and always cleaned up: the old pipeline
    left every job's intermediate files behind forever.
    """
    if not topic.strip():
        raise ValueError("topic must not be empty")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    concepts: list[Explanation] = []
    if mode != "coding":
        # Concepts first, then examples: the viewer needs to know what the
        # thing is before watching it being typed.
        slide_range, limit = SLIDE_PLAN[mode]
        concepts = concept_slides(writer, speaker, topic, context,
                                  slide_range=slide_range, limit=limit)

    built: list[Snippet] = []
    if mode != "illustration":
        built = code_sections(writer, speaker, topic, context, execute=execute,
                              max_snippets=max_snippets, timeout_s=timeout_s,
                              workdir=workdir)

    card = None
    if intro:
        _console.log("[blue]Writing the opening[/blue]")
        try:
            opening = writer.write(intro_prompt(topic))
        except ProviderError as exc:
            # The title card is framing, not content, and it is written last --
            # after the concept slides, the code and every line of narration
            # have already been generated and paid for. Failing the job here
            # throws all of that away, and --no-intro proves it is optional.
            _console.log(f"[yellow]No title card: {exc}[/yellow]")
        else:
            card = Intro(topic, speak_plain(speaker, opening))

    sections: list[Snippet | Explanation] = [*concepts, *built]
    plan = build_plan(sections, intro=card)
    _console.log(
        f"[green]Plan:[/green] {plan.total_frames} frames, {plan.duration_s:.1f}s, "
        f"{len(plan.captions)} captions"
    )
    return Tutorial(plan=plan, sections=sections, topic=topic)
