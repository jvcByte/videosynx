"""Render a lesson that was written by hand, rather than generated.

The ``--topic`` path asks a model to write the code and the narration. This
path is for content that already exists: the author supplies the snippets and
says what to narrate over each part of them. Nothing is paraphrased -- what is
written is what gets filmed.

The lesson is a small JSON file::

    {
      "title": "Common Sequence Operations",
      "intro": "Spoken over the title card.",
      "sections": [
        {
          "name": "Length",
          "code": "len([\\"a\\", \\"b\\"])\\n",
          "narration": [["len([", "len returns how many items a sequence has."]]
        }
      ],
      "outro": "Spoken over the final frame."
    }

Each narration entry pairs a fragment of that section's code with the sentence
to say while the fragment is on screen, exactly as the generated path does.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from rich.console import Console

from .execute import run_snippet
from .providers import Speaker
from .sandbox import DEFAULT_TIMEOUT_S, assert_sandbox_available
from .script import Tutorial, speak_anchored, speak_plain
from .timeline import Explanation, Intro, Phrase, Slide, Snippet, build_plan

_console = Console()


@dataclass(frozen=True)
class Section:
    """A coded section: narration sentences anchored to fragments of the code."""

    name: str
    code: str
    narration: list[tuple[str, str]]


@dataclass(frozen=True)
class Lesson:
    title: str
    sections: list[Section | ExplainSection]
    intro: str = ""
    outro: str = ""


@dataclass(frozen=True)
class ExplainSection:
    """A concept section: a slide, narrated, with no code on screen yet."""

    name: str
    slide: Slide
    sentences: tuple[str, ...]


def _load_explain(raw: dict, name: str) -> ExplainSection:
    spec = raw["explain"]
    columns = tuple(
        (str(title), tuple(str(line) for line in lines))
        for title, lines in spec.get("columns", [])
    )
    slide = Slide(
        heading=str(spec.get("heading", name)),
        columns=columns,
        sequence=tuple(str(v) for v in spec.get("sequence", [])),
        note=str(spec.get("note", "")),
    )
    sentences = tuple(str(s) for s in raw.get("narration", []) if str(s).strip())
    if not sentences:
        raise ValueError(f"section {name!r}: an explanation needs narration")
    return ExplainSection(name=name, slide=slide, sentences=sentences)


def load_lesson(path: Path | str) -> Lesson:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    sections: list[Section | ExplainSection] = []
    for i, raw in enumerate(data.get("sections", []), start=1):
        name = raw.get("name", f"Section {i}")
        if "explain" in raw:
            sections.append(_load_explain(raw, name))
            continue

        code = raw["code"]
        narration = [(str(marker), str(sentence)) for marker, sentence in raw["narration"]]
        for marker, _ in narration:
            if marker and marker not in code:
                # Anchors are how a sentence is lined up with the screen; a
                # marker that is not in the code means it will drift.
                raise ValueError(
                    f"section {i} ({name}): narration marker "
                    f"{marker!r} does not appear in the code"
                )
        sections.append(Section(name=name, code=code, narration=narration))

    if not sections:
        raise ValueError(f"{path}: no sections")
    return Lesson(
        title=data["title"],
        sections=sections,
        intro=data.get("intro", ""),
        outro=data.get("outro", ""),
    )


def build_lesson(
    lesson: Lesson,
    *,
    speaker: Speaker,
    execute: bool = True,
    repl: bool = True,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Tutorial:
    """Turn a written lesson into a render plan."""
    if execute:
        # Refuse before running anything that did not exist a moment ago.
        assert_sandbox_available()

    snippets: list[Snippet | Explanation] = []
    with tempfile.TemporaryDirectory(prefix="pycoding_lesson_") as td:
        for i, section in enumerate(lesson.sections):
            if isinstance(section, ExplainSection):
                _console.log(f"[blue]Explaining[/blue] {section.name}")
                snippets.append(
                    Explanation(
                        slide=section.slide,
                        phrases=speak_plain(speaker, " ".join(section.sentences)),
                    )
                )
                continue

            output: tuple[str, ...] = ()
            if execute:
                _console.log(f"[blue]Running[/blue] {section.name}")
                execution = run_snippet(
                    section.code,
                    workdir=Path(td) / f"section_{i}",
                    timeout_s=timeout_s,
                    repl=repl,
                )
                output = execution.lines
            snippets.append(
                Snippet(
                    code=section.code,
                    output=output,
                    phrases=speak_anchored(speaker, section.code, section.narration),
                )
            )

    if lesson.outro and snippets:
        # The closing remark plays over the final held frame, so it anchors to
        # the end of the last snippet.
        last = snippets[-1]
        closing = tuple(
            Phrase(p.text, p.samples, len(last.code))
            for p in speak_plain(speaker, lesson.outro)
        )
        snippets[-1] = replace(last, phrases=last.phrases + closing)

    card = Intro(lesson.title, speak_plain(speaker, lesson.intro)) if lesson.intro else None
    plan = build_plan(snippets, intro=card)
    _console.log(
        f"[green]Plan:[/green] {plan.total_frames} frames, {plan.duration_s:.1f}s, "
        f"{len(plan.captions)} captions"
    )
    return Tutorial(plan=plan, sections=snippets, topic=lesson.title)
