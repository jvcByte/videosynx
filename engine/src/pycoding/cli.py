"""Offline driver for the render core.

Phase 1 deliberately has no network, no Gemini, no ElevenLabs and no sandbox:
snippets are hardcoded and narration is a decoded sine tone. That keeps the
riskiest component iterable without touching the 20/day Gemini free quota.

Run: python -m pycoding.cli --demo
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from .narration import decode_pcm
from .render import render_plan
from .timeline import SR, Snippet, build_plan


def _tone(seconds: float) -> np.ndarray:
    """A stand-in narration track: a sine tone encoded to MP3 and decoded back.

    Deliberately round-trips through the encoder rather than returning raw
    samples, so the demo exercises the same exact-sample-count path that real
    ElevenLabs MP3s will take.
    """
    with tempfile.TemporaryDirectory() as td:
        mp3 = Path(td) / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency=320:duration={seconds}",
             "-ar", str(SR), "-ac", "1", "-b:a", "96k", str(mp3)],
            check=True,
        )
        pcm = decode_pcm(mp3)
    # Fade the edges so the tone does not click against silence.
    n = min(len(pcm), int(0.05 * SR))
    if n:
        ramp = np.linspace(0, 1, n)
        pcm[:n] = (pcm[:n] * ramp).astype(np.int16)
        pcm[-n:] = (pcm[-n:] * ramp[::-1]).astype(np.int16)
    return pcm


def _demo_snippets() -> list[Snippet]:
    return [
        Snippet(
            code=(
                "numbers = range(15)\n"
                "\n"
                "# Keep only elements where the condition is True\n"
                "evens = [x for x in numbers if x % 2 == 0]\n"
                "by_three = [x for x in numbers if x % 3 == 0]\n"
                "\n"
                'print("Evens:             ", evens)\n'
                'print("Divisible by three:", by_three)\n'
            ),
            output=(
                "$ python snippet.py",
                "Evens:              [0, 2, 4, 6, 8, 10, 12, 14]",
                "Divisible by three: [0, 3, 6, 9, 12]",
            ),
            narration=_tone(2.2),
        ),
        Snippet(
            # 22 lines — long enough to exercise scrolling past the visible rows.
            code=(
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
            output=(
                "$ python snippet.py",
                "A: 3",
                "B: 2",
                "C: 2",
                "D: 2",
                "G: 1",
            ),
            narration=_tone(4.0),
        ),
        Snippet(
            code=(
                "values = [4, 8, 15, 16, 23, 42]\n"
                "total = 0\n"
                "\n"
                "for v in values:\n"
                "    total += v\n"
                "\n"
                'print("Sum:", total)\n'
                'print("Average:", total / 0)\n'
            ),
            output=(
                "$ python snippet.py",
                "Sum: 108",
                "Traceback (most recent call last):",
                '  File "/work/snippet.py", line 8, in <module>',
                '    print("Average:", total / 0)',
                "ZeroDivisionError: division by zero",
            ),
            narration=_tone(3.0),
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pycoding render core (offline demo).")
    parser.add_argument("--demo", action="store_true", help="render the built-in demo")
    parser.add_argument("--out", type=Path, default=Path("demo.mp4"))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--narration-type", choices=["after", "parallel"], default="after")
    args = parser.parse_args(argv)

    if not args.demo:
        parser.error("nothing to do — pass --demo")

    plan = build_plan(_demo_snippets(), narration_type=args.narration_type)
    print(f"plan: {len(plan.spans)} spans, {plan.total_frames} frames, "
          f"{plan.duration_s:.2f}s, {len(plan.narrations)} narrations")

    path = render_plan(plan, args.out, width=args.width, height=args.height)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
