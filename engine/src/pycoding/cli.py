"""Offline driver for the render core.

Phase 1 deliberately has no network, no Gemini, no ElevenLabs and no sandbox:
snippets are hardcoded and narration comes from espeak-ng (falling back to a
sine tone). That keeps the riskiest component iterable without touching the
20/day Gemini free quota or spending ElevenLabs credits.

Run: python -m pycoding.cli --demo
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from .execute import run_snippet
from .narration import decode_pcm, trim_silence
from .render import render_plan
from .sandbox import assert_sandbox_available
from .srt import write_srt
from .timeline import SR, Intro, Phrase, Snippet, build_plan

SPEECH_WPM = 165  # espeak-ng pace; roughly a calm tutorial delivery

TOPIC = "Python List Comprehensions"

# A viewer arriving cold has no idea what they are looking at. The opening
# card says what the video covers before any code starts moving.
INTRO_TEXT = (
    "In this video we'll look at how Python builds new lists out of existing "
    "ones. We'll start with a simple comprehension, then write a function that "
    "counts names by their first letter. Finally we'll run some code that "
    "fails on purpose."
)


def split_sentences(text: str) -> list[str]:
    """Break narration into sentences so each can be synthesised separately."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def narrate(text: str) -> tuple[Phrase, ...]:
    """Synthesise narration one sentence at a time.

    Sentence-at-a-time is what makes caption timing exact: each caption spans
    the frames of the audio it was actually spoken over. It also keeps prosody
    intact, because a sentence is a natural unit of speech -- unlike the
    caption-sized fragments, which would each get their own intonation contour.
    """
    return tuple(Phrase(s, trim_silence(speak(s))) for s in split_sentences(text))


def narrate_against(code: str, script: list[tuple[str, str]]) -> tuple[Phrase, ...]:
    """Synthesise narration, each sentence anchored to the code it describes.

    *script* pairs a marker -- a fragment of the code -- with what to say while
    that fragment is being typed. Anchoring by substring rather than by a
    hardcoded offset means the script survives edits to the code.
    """
    phrases = []
    for marker, sentence in script:
        anchor = code.index(marker) if marker else 0
        phrases.append(Phrase(sentence, trim_silence(speak(sentence)), anchor))
    return tuple(phrases)


def _tone(seconds: float) -> np.ndarray:
    """Fallback stand-in: a sine tone, encoded to MP3 and decoded back."""
    with tempfile.TemporaryDirectory() as td:
        mp3 = Path(td) / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency=320:duration={seconds}",
             "-ar", str(SR), "-ac", "1", "-b:a", "96k", str(mp3)],
            check=True,
        )
        return decode_pcm(mp3)


def speak(text: str) -> np.ndarray:
    """Narration for *text*, via espeak-ng when available.

    The voice is robotic on purpose: it exists to make pacing and caption sync
    audible before ElevenLabs is wired up, not to be the final product.
    """
    engine = shutil.which("espeak-ng") or shutil.which("espeak")
    if engine is None:
        return _tone(max(1.2, len(text) * 0.055))
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "line.wav"
        subprocess.run(
            [engine, "-v", "en-us", "-s", str(SPEECH_WPM), "-w", str(wav), text],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return decode_pcm(wav)


def _demo_snippets(execute: bool = False) -> list[Snippet]:
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
            Snippet(code=code, output=output, phrases=narrate_against(code, script))
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
                        phrases=narrate_against(code, script))
            )
    return snippets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pycoding render core (offline demo).")
    parser.add_argument("--demo", action="store_true", help="render the built-in demo")
    parser.add_argument("--out", type=Path, default=Path("demo.mp4"))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--narration-type", choices=["after", "parallel"], default="after")
    parser.add_argument("--pace", choices=["narration", "fixed"], default="narration",
                        help="'narration' times the typing to the voice; 'fixed' types "
                             "at a constant rate regardless of narration length")
    parser.add_argument("--no-captions", action="store_true", help="render without captions")
    parser.add_argument("--no-intro", action="store_true", help="skip the opening title card")
    parser.add_argument("--execute", action="store_true",
                        help="actually run each snippet in the sandbox and show its real output")
    args = parser.parse_args(argv)

    if not args.demo:
        parser.error("nothing to do — pass --demo")

    snippets = _demo_snippets(execute=args.execute)
    if args.no_captions:
        snippets = [
            Snippet(s.code, s.output, tuple(Phrase("", p.samples) for p in s.phrases))
            for s in snippets
        ]

    intro = None if args.no_intro else Intro(TOPIC, narrate(INTRO_TEXT))

    plan = build_plan(snippets, narration_type=args.narration_type, pace=args.pace,
                      intro=intro)
    print(f"plan: {len(plan.spans)} spans, {plan.total_frames} frames, "
          f"{plan.duration_s:.2f}s, {len(plan.narrations)} narrations, "
          f"{len(plan.captions)} captions")
    if intro is not None:
        card_frames = sum(s.n_frames for s in plan.spans if s.state.title is not None)
        print(f"      title card: {card_frames / 30:.1f}s")

    path = render_plan(plan, args.out, width=args.width, height=args.height)
    print(f"wrote {path}")

    srt_path = write_srt(plan.captions, Path(args.out).with_suffix(".srt"))
    if srt_path:
        print(f"wrote {srt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
