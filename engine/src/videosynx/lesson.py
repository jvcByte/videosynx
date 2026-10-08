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
from .prompts import parse_json_object, script_prompt
from .providers import Speaker, Writer
from .sandbox import DEFAULT_TIMEOUT_S, assert_sandbox_available
from .script import Tutorial, speak_anchored, speak_plain
from .timeline import (
    Explanation,
    Card,
    Phrase,
    Slide,
    Snippet,
    build_plan,
    parse_diagram,
)

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
        diagram=parse_diagram(spec.get("diagram")),
        note=str(spec.get("note", "")),
    )
    sentences = tuple(str(s) for s in raw.get("narration", []) if str(s).strip())
    if not sentences:
        raise ValueError(f"section {name!r}: an explanation needs narration")
    return ExplainSection(name=name, slide=slide, sentences=sentences)


def parse_lesson(data, source: str = "lesson") -> Lesson:
    """Validate a plain dict and build a Lesson.

    Split out from :func:`load_lesson` so model-written lessons go through
    exactly the checks a hand-written file does. The generated path is the one
    more likely to be malformed, not less, and a lesson that fails here fails
    with a readable message instead of somewhere deep in the renderer.
    """
    if not isinstance(data, dict):
        raise ValueError(f"{source}: expected a JSON object, not {type(data).__name__}")

    sections: list[Section | ExplainSection] = []
    for i, raw in enumerate(data.get("sections") or [], start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"{source}: section {i} is not an object")
        name = str(raw.get("name", f"Section {i}"))
        if "explain" in raw:
            sections.append(_load_explain(raw, name))
            continue

        if "code" not in raw:
            raise ValueError(
                f"{source}: section {i} ({name}) has neither code nor explain"
            )
        code = str(raw["code"])
        narration = [(str(marker), str(sentence))
                     for marker, sentence in raw.get("narration") or []]
        for marker, _ in narration:
            if marker and marker not in code:
                # Anchors are how a sentence is lined up with the screen; a
                # marker that is not in the code means it will drift.
                raise ValueError(
                    f"{source}: section {i} ({name}): narration marker "
                    f"{marker!r} does not appear in the code"
                )
        sections.append(Section(name=name, code=code, narration=narration))

    if not sections:
        raise ValueError(f"{source}: no sections")

    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError(f"{source}: no title")
    return Lesson(
        title=title,
        sections=sections,
        intro=str(data.get("intro", "")),
        outro=str(data.get("outro", "")),
    )


def load_lesson(path: Path | str) -> Lesson:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc
    return parse_lesson(data, source=str(path))


def lesson_from_object(data, source: str = "the submitted lesson") -> Lesson | None:
    """The lesson a decoded object is, or None if it is not one.

    Returns None for a dict that is simply not a lesson -- prose that happens
    to contain braces, say. Raises ValueError for one that *is* a lesson and
    is malformed, which is a different thing and the more useful answer: the
    caller wants to know which section is broken, not that it is unrecognised.
    """
    if isinstance(data, dict) and data.get("sections"):
        return parse_lesson(data, source=source)
    return None


def lesson_from_json(text: str) -> Lesson | None:
    """The lesson a blob of text carries, or None if it carries none.

    The first thing :func:`lesson_from_text` does, on its own. Split out so
    the API can check what the worker is about to render without a writer or
    a speaker to hand, and so that both agree on what counts as lesson JSON
    -- the way to guarantee that is for there to be one answer.
    """
    return lesson_from_object(parse_json_object(text), "the submitted script")


def lesson_from_text(text: str, *, writer: Writer | None = None,
                     local: bool = False) -> Lesson:
    """Turn submitted script text into a lesson.

    Lesson JSON is rendered as written either way: a caller who has already
    done the work should not have their content rewritten underneath them.

    Prose is the interesting case. With a model available it is structured by
    the model, which reads better. Without one -- local mode, or an exhausted
    allowance -- it is parsed mechanically instead, which is what lets local
    mode take pasted text at all.
    """
    lesson = lesson_from_json(text)
    if lesson is not None:
        _console.log("[blue]Rendering the submitted lesson[/blue]")
        return lesson

    if local:
        from .prose import lesson_from_prose  # circular at module level

        _console.log("[blue]Structuring the script on this machine[/blue]")
        lesson = lesson_from_prose(text)
        _console.log(f"[green]{len(lesson.sections)} slides from the script[/green]")
        return lesson

    if writer is None:
        raise ValueError("structuring prose needs a model; use local mode instead")
    return script_to_lesson(writer, text)


def script_to_lesson(writer: Writer, text: str) -> Lesson:
    """Turn a written script into a lesson, with a model doing the structuring.

    The result goes through :func:`parse_lesson`, so a model that answers in
    prose, returns a truncated object, or emits a slide with nothing in it
    fails here rather than part-way through a render.
    """
    _console.log("[blue]Structuring the script[/blue]")
    data = parse_json_object(writer.write(script_prompt(text)))
    if data is None:
        raise ValueError("the model did not return lesson JSON for this script")
    lesson = parse_lesson(data, source="the generated lesson")
    _console.log(f"[green]{len(lesson.sections)} sections from the script[/green]")
    return lesson


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
    with tempfile.TemporaryDirectory(prefix="videosynx_lesson_") as td:
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
        # the end of the last section. A slide has no code to anchor to, and
        # does not need one: its phrases are laid out back to back regardless.
        last = snippets[-1]
        end = len(last.code) if isinstance(last, Snippet) else 0
        closing = tuple(
            Phrase(p.text, p.samples, end)
            for p in speak_plain(speaker, lesson.outro)
        )
        snippets[-1] = replace(last, phrases=last.phrases + closing)

    card = Card(lesson.title, speak_plain(speaker, lesson.intro)) if lesson.intro else None
    plan = build_plan(snippets, intro=card)
    _console.log(
        f"[green]Plan:[/green] {plan.total_frames} frames, {plan.duration_s:.1f}s, "
        f"{len(plan.captions)} captions"
    )
    return Tutorial(plan=plan, sections=snippets, topic=lesson.title)
