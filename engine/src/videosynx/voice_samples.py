"""One short clip per voice, so a voice can be heard before it is chosen.

Picking a narrator from a list of fifty-four identifiers is picking blind:
`af_heart` and `am_michael` say nothing about what either one sounds like.
The settings page offers a preview for each, and the preview is a *file*
rather than a synthesis, for a reason that is a measurement -- a six-second
clip takes eight to ten seconds to make, and the render queue would be asked
for one every time somebody clicked through the list. Fifty-four of them,
made once, are four megabytes of static audio and play instantly.

Written by::

    python -m videosynx.voice_samples web/public/voices

and checked by `engine/tests/test_reading_aloud.py`, which fails if a voice
in the catalogue has no sample or a sample has no voice -- so the two cannot
drift apart quietly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from .providers import DEFAULT_PACE, KokoroSpeaker, speed_for
from .speech import synthesise

# What every voice says. Short, because it is a sample rather than a
# demonstration: enough for the timbre and the accent to arrive, and about
# six seconds, which is what makes fifty-four of them affordable.
SAMPLE_TEXT = (
    "A list comprehension builds one list from another. "
    "It reads like the thing you are describing."
)


def write_samples(
    directory: Path,
    *,
    pace: str | None = None,
    voices: tuple[str, ...] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[Path]:
    """Render a sample for each voice into *directory*, and return the paths.

    The voices come from the model rather than from a list kept beside it, so
    one added to a future release of the voice pack gets a sample the next
    time this runs -- and the test that compares the directory against the
    catalogue says so in the meantime.

    One pace for all of them, the default one. The samples are committed
    rather than made on demand, so they cannot follow a reader's own setting;
    a preview is answering "what does this voice sound like", which a fifteen
    percent difference in tempo does not change.

    The model is opened once and then asked to speak as somebody else each
    time. Opening it costs five seconds cold, and there are fifty-four voices.
    """
    speaker = KokoroSpeaker(speed=speed_for(pace))
    wanted = tuple(voices) if voices is not None else speaker.voices
    if not wanted:
        raise RuntimeError(
            "the voice model did not list any voices, so there is nothing to "
            "render -- check that kokoro-v1.0.onnx and voices-v1.0.bin are "
            "where the engine expects them"
        )

    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, voice in enumerate(wanted):
        if on_progress is not None:
            on_progress(index, len(wanted), voice)
        out = directory / f"{voice}.mp3"
        synthesise(SAMPLE_TEXT, speaker.with_voice(voice), out)
        written.append(out)
    if on_progress is not None:
        on_progress(len(wanted), len(wanted), "")
    return written


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: python -m videosynx.voice_samples <directory>", file=sys.stderr)
        return 2

    directory = Path(args[0])

    def report(done: int, total: int, voice: str) -> None:
        print(f"\r{done}/{total} {voice:<14}", end="" if done < total else "\n")

    written = write_samples(directory, on_progress=report)
    print(f"{len(written)} samples at {DEFAULT_PACE} pace in {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
