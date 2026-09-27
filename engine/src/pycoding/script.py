"""Turn a topic into a render plan.

This is the whole pipeline above the renderer: ask for code, run it, ask for
narration against each snippet, speak it, and lay the result out on the frame
grid. It talks to the outside world only through ``Writer`` and ``Speaker``,
so the entire thing runs offline against stand-ins.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from .execute import run_snippet
from .narration import trim_silence
from .prompts import (
    code_prompt,
    intro_prompt,
    narration_prompt,
    parse_anchored,
    parse_code,
    sentences,
)
from .providers import Speaker, Writer
from .sandbox import DEFAULT_TIMEOUT_S, assert_sandbox_available
from .timeline import FramePlan, Intro, Phrase, Snippet, build_plan

_console = Console()


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


@dataclass
class Tutorial:
    plan: FramePlan
    snippets: list[Snippet]
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
            "snippets": [
                {
                    "code": snippet.code,
                    "output": list(snippet.output),
                    "narration": [
                        {
                            "text": phrase.text,
                            "anchor": phrase.anchor,
                            "anchored": phrase.anchor is not None,
                            "frames": phrase.n_frames,
                        }
                        for phrase in snippet.phrases
                    ],
                }
                for snippet in self.snippets
            ],
            "captions": [
                {"text": c.text, "start_frame": c.start_frame, "end_frame": c.end_frame}
                for c in self.plan.captions
            ],
        }
        path = Path(path)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path


def build_tutorial(
    topic: str,
    *,
    writer: Writer,
    speaker: Speaker,
    context: str = "",
    execute: bool = True,
    intro: bool = True,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    workdir: Path | None = None,
) -> Tutorial:
    """Generate a complete render plan for *topic*.

    The work directory is temporary and always cleaned up: the old pipeline
    left every job's intermediate files behind forever.
    """
    if not topic.strip():
        raise ValueError("topic must not be empty")

    _console.log(f"[blue]Writing code for:[/blue] {topic}")
    raw_code = writer.write(code_prompt(topic, context))
    snippets_code = parse_code(raw_code)
    _console.log(f"[green]{len(snippets_code)} snippets[/green]")

    if execute:
        # Refuse before generating anything that will be run.
        assert_sandbox_available()

    built: list[Snippet] = []
    with tempfile.TemporaryDirectory(prefix="pycoding_") as td:
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

    card = None
    if intro:
        _console.log("[blue]Writing the opening[/blue]")
        card = Intro(topic, speak_plain(speaker, writer.write(intro_prompt(topic))))

    plan = build_plan(built, intro=card)
    _console.log(
        f"[green]Plan:[/green] {plan.total_frames} frames, {plan.duration_s:.1f}s, "
        f"{len(plan.captions)} captions"
    )
    return Tutorial(plan=plan, snippets=built, topic=topic)
