"""Command line entry point.

Two modes:

- ``--demo`` renders a hardcoded tutorial with synthetic speech. No network,
  no API keys, no quota, no spend -- this is what the render core is developed
  and judged against.
- ``--topic`` runs the real pipeline: Gemini writes the code and the
  narration, the code is executed in the sandbox, ElevenLabs speaks it. It
  needs API keys and spends real quota.

Both end at the same place: a FramePlan handed to the renderer.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from rich.console import Console

from .execute import run_snippet
from .lesson import build_lesson, load_lesson
from .narration import trim_silence
from .prompts import sentences
from .providers import (
    DEFAULT_KOKORO_VOICE,
    DEFAULT_MODEL,
    ESpeakSpeaker,
    ElevenLabsSpeaker,
    GeminiWriter,
    KokoroSpeaker,
    ProviderError,
    Speaker,
    ToneSpeaker,
)
from .render import render_plan
from .sandbox import assert_sandbox_available
from .script import MODES, build_tutorial
from .srt import write_srt
from .timeline import FPS, Intro, Phrase, Snippet, Span, build_plan, without_captions

_console = Console()

TOPIC = "Python List Comprehensions"

# A viewer arriving cold has no idea what they are looking at. The opening
# card says what the video covers before any code starts moving.
INTRO_TEXT = (
    "In this video we'll look at how Python builds new lists out of existing "
    "ones. We'll start with a simple comprehension, then write a function that "
    "counts names by their first letter. Finally we'll run some code that "
    "fails on purpose."
)


def narrate(speaker: Speaker, text: str) -> tuple[Phrase, ...]:
    """Synthesise narration one sentence at a time.

    Sentence-at-a-time is what makes caption timing exact: each caption spans
    the frames of the audio it was actually spoken over. It also keeps prosody
    intact, because a sentence is a natural unit of speech -- unlike the
    caption-sized fragments, which would each get their own intonation contour.
    """
    return tuple(Phrase(s, trim_silence(speaker.say(s))) for s in sentences(text))


def narrate_against(speaker: Speaker, code: str,
                    script: list[tuple[str, str]]) -> tuple[Phrase, ...]:
    """Synthesise narration, each sentence anchored to the code it describes.

    *script* pairs a marker -- a fragment of the code -- with what to say while
    that fragment is being typed. Anchoring by substring rather than by a
    hardcoded offset means the script survives edits to the code.
    """
    phrases = []
    for marker, sentence in script:
        anchor = code.index(marker) if marker else 0
        phrases.append(Phrase(sentence, trim_silence(speaker.say(sentence)), anchor))
    return tuple(phrases)


def _demo_snippets(speaker: Speaker, execute: bool = False) -> list[Snippet]:
    specs = [
        (
            (
                "numbers = range(15)\n"
                "\n"
                "# Keep only elements where the condition is True\n"
                "evens = [x for x in numbers if x % 2 == 0]\n"
                "by_three = [x for x in numbers if x % 3 == 0]\n"
                "\n"
                'print("Evens:             ", evens)\n'
                'print("Divisible by three:", by_three)\n'
            ),
            (
                "$ python snippet.py",
                "Evens:              [0, 2, 4, 6, 8, 10, 12, 14]",
                "Divisible by three: [0, 3, 6, 9, 12]",
            ),
            [
                ("numbers = range",
                 "First we make a range holding the numbers zero through fourteen."),
                ("evens = [",
                 "This comprehension keeps only the elements where the remainder "
                 "after dividing by two is zero, giving us the even numbers."),
                ("by_three = [",
                 "The second one does the same with three, so we get the multiples "
                 "of three."),
                ('print("Evens',
                 "Finally we print both lists side by side so you can compare them."),
            ],
        ),
        (
            # 22 lines — long enough to exercise scrolling past the visible rows.
            (
                "def summarise(rows):\n"
                '    """Count rows by first letter, most common first."""\n'
                "    counts = {}\n"
                "    for row in rows:\n"
                "        key = row[0].upper()\n"
                "        if key in counts:\n"
                "            counts[key] += 1\n"
                "        else:\n"
                "            counts[key] = 1\n"
                "\n"
                "    ordered = sorted(counts.items(), key=lambda kv: -kv[1])\n"
                "    return ordered\n"
                "\n"
                "\n"
                "names = [\n"
                '    "ada", "alan", "grace", "alice",\n'
                '    "barbara", "bob", "carol", "claude",\n'
                '    "dijkstra", "donald",\n'
                "]\n"
                "\n"
                "for letter, count in summarise(names):\n"
                '    print(f"{letter}: {count}")\n'
            ),
            ("$ python snippet.py", "A: 3", "B: 2", "C: 2", "D: 2", "G: 1"),
            [
                ("def summarise",
                 "Here we define a function called summarise that counts a list of "
                 "names by their first letter."),
                ("counts = {}",
                 "Inside, we begin with an empty dictionary."),
                ("for row in rows",
                 "Then we loop over every row and take its first character."),
                ("key = row[0].upper()",
                 "We convert that character to upper case, so the grouping does not "
                 "depend on how a name was capitalised."),
                ("if key in counts",
                 "If the letter is already in the dictionary we increment its count, "
                 "otherwise we create a new entry."),
                ("ordered = sorted",
                 "Once the loop finishes, we sort the items by count with the most "
                 "common first, and return them."),
                ("names = [",
                 "Further down, we build the list of names to summarise."),
                ('"barbara"',
                 "Any names will do; we just need enough of them for the counts "
                 "to be interesting."),
                ("for letter, count",
                 "Finally we call the function and print each letter alongside its "
                 "count."),
            ],
        ),
        (
            (
                "values = [4, 8, 15, 16, 23, 42]\n"
                "total = 0\n"
                "\n"
                "for v in values:\n"
                "    total += v\n"
                "\n"
                'print("Sum:", total)\n'
                'print("Average:", total / 0)\n'
            ),
            (
                "$ python snippet.py",
                "Sum: 108",
                "Traceback (most recent call last):",
                '  File "/work/snippet.py", line 8, in <module>',
                '    print("Average:", total / 0)',
                "ZeroDivisionError: division by zero",
            ),
            [
                ("values = [",
                 "We start with a list of values, and a running total set to zero."),
                ("for v in values",
                 "Then we loop over every value and add it into the total."),
                ('print("Sum:',
                 "That gives us one hundred and eight."),
                ('print("Average',
                 "Now watch what happens when we divide by zero."),
            ],
        ),
    ]
    if not execute:
        return [
            Snippet(code=code, output=output,
                    phrases=narrate_against(speaker, code, script))
            for code, output, script in specs
        ]

    # Refuse to run anything generated unless isolation actually holds.
    assert_sandbox_available()
    snippets = []
    with tempfile.TemporaryDirectory(prefix="pycoding_demo_") as td:
        for i, (code, _, script) in enumerate(specs):
            execution = run_snippet(code, workdir=Path(td) / f"snippet_{i}")
            snippets.append(
                Snippet(code=code, output=execution.lines,
                        phrases=narrate_against(speaker, code, script))
            )
    return snippets


def _offline_speaker() -> Speaker:
    """Speech for development: espeak-ng, or a tone if it is not installed."""
    espeak = ESpeakSpeaker()
    if espeak.available:
        return espeak
    _console.log(
        "[yellow]espeak-ng not found, falling back to a tone. "
        "Install it with: sudo apt install espeak-ng[/yellow]"
    )
    return ToneSpeaker()


def _make_speaker(choice: str, args) -> Speaker:
    """Build one speaker, or raise. Never quietly downgrades."""
    if choice == "elevenlabs":
        if not (args.elevenlabs_api_key and args.elevenlabs_voice_id):
            raise ProviderError(
                "ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID not set"
            )
        return ElevenLabsSpeaker(args.elevenlabs_api_key, args.elevenlabs_voice_id)
    if choice == "kokoro":
        return KokoroSpeaker(voice=args.voice)
    return _offline_speaker()


def choose_speaker(args, *, allow_paid: bool = True) -> Speaker:
    """Pick the voice, for every mode.

    An explicit --tts is honoured or fails loudly; nothing is substituted
    behind the user's back. `auto` prefers Kokoro, which is free, local and
    cannot run out of credit partway through a video the way a paid API can.
    Pass --tts elevenlabs when the best voice is wanted and the allowance is
    known to cover it.

    `allow_paid=False` is for --demo, whose whole promise is that it spends
    nothing, so it must never reach for ElevenLabs implicitly.
    """
    if args.no_tts:
        _console.log("[yellow]--no-tts: offline speech[/yellow]")
        return _offline_speaker()

    if args.tts != "auto":
        return _make_speaker(args.tts, args)

    chain = ["kokoro"] + (["elevenlabs"] if allow_paid else []) + ["espeak"]
    for choice in chain:
        try:
            speaker = _make_speaker(choice, args)
            _console.log(f"[blue]voice: {choice}[/blue]")
            return speaker
        except ProviderError as exc:
            _console.log(f"[yellow]{choice} unavailable: {exc}[/yellow]")

    _console.log("[yellow]no local voice available; using a tone[/yellow]")
    return ToneSpeaker()


def main(argv: list[str] | None = None) -> int:
    # Credentials usually live in a .env; loading it means one command
    # instead of an export line before every run.
    from .dotenv import load

    if load():
        _console.log("[blue]loaded .env[/blue]")

    parser = argparse.ArgumentParser(
        description="Generate narrated coding tutorial videos."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true",
                      help="render the built-in offline demo (no keys, no spend)")
    mode.add_argument("--topic", type=str, help="topic to generate a tutorial about")
    mode.add_argument("--lesson", type=Path,
                      help="render a hand-written lesson JSON instead of generating one")

    parser.add_argument("--out", type=Path, default=Path("tutorial.mp4"))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--narration-type", choices=["after", "parallel"], default="after")
    parser.add_argument("--pace", choices=["narration", "fixed"], default="narration",
                        help="'narration' times the typing to the voice; 'fixed' types "
                             "at a constant rate regardless of narration length")
    parser.add_argument("--no-captions", action="store_true", help="render without captions")
    parser.add_argument("--no-intro", action="store_true", help="skip the opening title card")
    parser.add_argument("--mode", choices=list(MODES), default="both",
                        help="'both' explains the idea then writes code; 'coding' "
                             "is code alone; 'illustration' is drawn explanation "
                             "alone, with no code on screen")
    parser.add_argument("--execute", action="store_true",
                        help="run each snippet in the sandbox and show its real output")
    parser.add_argument(
        "--repl", action=argparse.BooleanOptionalAction, default=True,
        help="echo bare expression results the way a REPL would, so a teaching "
             "line like `letters[0]` shows its value (default: on)",
    )
    parser.add_argument("--context", type=str, default="",
                        help="extra context for code generation (files, constraints)")
    parser.add_argument("--no-tts", action="store_true",
                        help="use offline speech even when ElevenLabs keys are present")
    parser.add_argument("--tts", choices=["auto", "elevenlabs", "kokoro", "espeak"],
                        default="auto",
                        help="voice engine. 'auto' prefers Kokoro, which is local "
                             "and cannot run out of credit mid-video, then "
                             "ElevenLabs, then espeak")
    parser.add_argument("--voice", type=str, default=DEFAULT_KOKORO_VOICE,
                        help=f"Kokoro voice (default: {DEFAULT_KOKORO_VOICE})")
    parser.add_argument("--save-script", type=Path,
                        help="write the generated code and narration to this JSON file")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help=f"Gemini model (default: {DEFAULT_MODEL}). Quota is "
                             "per-model on the free tier, so a different model is "
                             "often the way past a daily limit")
    parser.add_argument("--google-api-key", type=str,
                        default=os.environ.get("GEMINI_API_KEY", ""))
    parser.add_argument("--elevenlabs-api-key", type=str,
                        default=os.environ.get("ELEVENLABS_API_KEY", ""))
    parser.add_argument("--elevenlabs-voice-id", type=str,
                        default=os.environ.get("ELEVENLABS_VOICE_ID", ""))
    args = parser.parse_args(argv)

    if args.demo:
        speaker = choose_speaker(args, allow_paid=False)
        snippets = _demo_snippets(speaker, execute=args.execute)
        intro = None if args.no_intro else Intro(TOPIC, narrate(speaker, INTRO_TEXT))
        plan = build_plan(snippets, narration_type=args.narration_type,
                          pace=args.pace, intro=intro)
    elif args.lesson:
        tutorial = build_lesson(
            load_lesson(args.lesson),
            speaker=choose_speaker(args),
            execute=args.execute,
            repl=args.repl,
        )
        plan = tutorial.plan
        if args.save_script:
            _console.log(f"[green]wrote {tutorial.export(args.save_script)}[/green]")
    else:
        if not args.google_api_key:
            raise SystemExit(
                "--google-api-key or GEMINI_API_KEY is required, "
                "or pass --demo for the offline one"
            )
        tutorial = build_tutorial(
            args.topic,
            writer=GeminiWriter(args.google_api_key, model=args.model),
            speaker=choose_speaker(args),
            context=args.context,
            execute=args.execute,
            intro=not args.no_intro,
            mode=args.mode,
        )
        plan = tutorial.plan
        if args.save_script:
            _console.log(f"[green]wrote {tutorial.export(args.save_script)}[/green]")

    if args.no_captions:
        plan = without_captions(plan)

    _console.log(
        f"plan: {len(plan.spans)} spans, {plan.total_frames} frames, "
        f"{plan.duration_s:.2f}s, {len(plan.captions)} captions"
    )
    card_frames = sum(s.n_frames for s in plan.spans if s.state.title is not None)
    if card_frames:
        _console.log(f"      title card: {card_frames / FPS:.1f}s")

    path = render_plan(plan, args.out, width=args.width, height=args.height)
    _console.log(f"[green]wrote {path}[/green]")

    srt_path = write_srt(plan.captions, Path(args.out).with_suffix(".srt"))
    if srt_path:
        _console.log(f"[green]wrote {srt_path}[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
