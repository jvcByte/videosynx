"""Offline driver for the render core.

Phase 1 deliberately has no network, no Gemini, no ElevenLabs and no sandbox:
snippets are hardcoded and narration comes from espeak-ng (falling back to a
sine tone). That keeps the riskiest component iterable without touching the
20/day Gemini free quota or spending ElevenLabs credits.

Run: python -m pycoding.cli --demo
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from .execute import run_snippet
from .narration import decode_pcm
from .render import render_plan
from .sandbox import assert_sandbox_available
from .srt import write_srt
from .timeline import SR, Snippet, build_plan

SPEECH_WPM = 150  # espeak-ng pace; roughly a calm tutorial delivery


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
            "A list comprehension builds a new list in a single expression. "
            "Here we start with the numbers zero through fourteen, then keep only "
            "the evens, and separately the multiples of three.",
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
            "We define a function that counts each name by its first letter. "
            "Inside, we loop over the rows, normalise each key to upper case, and "
            "tally it in a dictionary. Finally we sort by count, most common first.",
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
            "We sum the list by accumulating into a running total. Now watch what "
            "happens when we divide by zero: Python raises a ZeroDivisionError and "
            "prints the traceback in red.",
        ),
    ]
    if not execute:
        return [
            Snippet(code=code, output=output, narration=speak(text), narration_text=text)
            for code, output, text in specs
        ]

    # Refuse to run anything generated unless isolation actually holds.
    assert_sandbox_available()
    snippets = []
    with tempfile.TemporaryDirectory(prefix="pycoding_demo_") as td:
        for i, (code, _, text) in enumerate(specs):
            execution = run_snippet(code, workdir=Path(td) / f"snippet_{i}")
            snippets.append(
                Snippet(
                    code=code,
                    output=execution.lines,
                    narration=speak(text),
                    narration_text=text,
                )
            )
    return snippets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pycoding render core (offline demo).")
    parser.add_argument("--demo", action="store_true", help="render the built-in demo")
    parser.add_argument("--out", type=Path, default=Path("demo.mp4"))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--narration-type", choices=["after", "parallel"], default="after")
    parser.add_argument("--no-captions", action="store_true", help="render without captions")
    parser.add_argument("--execute", action="store_true",
                        help="actually run each snippet in the sandbox and show its real output")
    args = parser.parse_args(argv)

    if not args.demo:
        parser.error("nothing to do — pass --demo")

    snippets = _demo_snippets(execute=args.execute)
    if args.no_captions:
        snippets = [
            Snippet(s.code, s.output, s.narration, "") for s in snippets
        ]

    plan = build_plan(snippets, narration_type=args.narration_type)
    print(f"plan: {len(plan.spans)} spans, {plan.total_frames} frames, "
          f"{plan.duration_s:.2f}s, {len(plan.narrations)} narrations, "
          f"{len(plan.captions)} captions")

    path = render_plan(plan, args.out, width=args.width, height=args.height)
    print(f"wrote {path}")

    srt_path = write_srt(plan.captions, Path(args.out).with_suffix(".srt"))
    if srt_path:
        print(f"wrote {srt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
