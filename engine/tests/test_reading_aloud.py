"""Reading text aloud.

Two things here are not about the audio. Synthesis is minutes of CPU in one
call, so a stop that is only noticed when the call returns is not a stop, and
a progress report that only arrives at the end is not progress. Both have to
come out of the sentence loop, and both are checked below with a speaker that
counts instead of speaks.
"""

from __future__ import annotations

import numpy as np
import pytest

from videosynx.speech import synthesise


class CountingSpeaker:
    """A speaker that records what it was asked to say."""

    def __init__(self, fail_on: int | None = None):
        self.said: list[str] = []
        self.fail_on = fail_on

    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        if self.fail_on is not None and len(self.said) == self.fail_on:
            raise RuntimeError("stopped")
        self.said.append(text)
        # A tenth of a second, audible rather than silent: trim_silence is
        # applied to what comes back, and a fixture made of silence is a
        # fixture that trims to nothing.
        return np.full(4800, 1000, dtype=np.int16)


TEXT = "One. Two. Three. Four."


def test_it_says_every_sentence(tmp_path):
    speaker = CountingSpeaker()
    seconds = synthesise(TEXT, speaker, tmp_path / "a.mp3")
    assert speaker.said == ["One.", "Two.", "Three.", "Four."]
    # Four tenths of speech, and a pause between each pair of them.
    assert seconds == pytest.approx(0.4 + 3 * SENTENCE_PAUSE_S)


def test_progress_moves_between_sentences(tmp_path):
    """Not once at the start and once at the end: a bar that only moves at
    the end is a bar that has stopped for the whole job."""
    seen: list[float] = []
    synthesise(TEXT, CountingSpeaker(), tmp_path / "a.mp3",
               on_progress=seen.append)
    assert seen == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_a_stop_is_noticed_before_the_whole_clip_is_read(tmp_path):
    """The caller's stop raises. It has to be asked between sentences, or a
    cancel lands only when there is nothing left to cancel."""
    speaker = CountingSpeaker()
    calls: list[int] = []

    def stop() -> None:
        calls.append(len(speaker.said))
        if len(speaker.said) >= 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        synthesise(TEXT, speaker, tmp_path / "a.mp3", should_stop=stop)

    # Asked once per sentence, and it stopped the third -- not after four.
    assert calls == [0, 1, 2]
    assert speaker.said == ["One.", "Two."]


def test_nothing_to_say_is_refused(tmp_path):
    with pytest.raises(ValueError, match="nothing to say"):
        synthesise("   \n\n", CountingSpeaker(), tmp_path / "a.mp3")


# ------------------------------------------------------------- the pauses
# Kokoro does not pause at a full stop -- measured at about 60 ms, which is
# nothing -- and concatenating its output sentence by sentence joins them
# with no gap at all. The clip then reads as one long breath, which is what a
# listener notices long before they can say why.

from videosynx.speech import (  # noqa: E402
    PARAGRAPH_PAUSE_S, SENTENCE_PAUSE_S, _segments,
)


def test_a_full_stop_is_followed_by_a_pause():
    assert _segments("One. Two.") == [("One.", SENTENCE_PAUSE_S), ("Two.", 0.0)]


def test_a_paragraph_is_a_longer_pause_than_a_sentence():
    """`sentences` splits on whitespace after a full stop, so a blank line is
    consumed as one and a pasted article loses the shape it was written in."""
    parts = _segments("All of it. And more.\n\nA new paragraph.")
    assert parts[1][1] == PARAGRAPH_PAUSE_S
    assert parts[1][1] > parts[0][1] > 0


def test_the_clip_does_not_end_on_silence():
    assert _segments("One. Two.")[-1][1] == 0.0


def test_the_pause_is_actually_in_the_audio(tmp_path):
    """The rule above is worth nothing if the gap never reaches the PCM."""
    import subprocess

    out = tmp_path / "gap.mp3"
    synthesise("One. Two.", CountingSpeaker(), out)

    # Decoded rather than inspected: what is being checked is the file a
    # listener gets, not the array on the way to it.
    detected = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(out),
         "-af", "silencedetect=noise=-40dB:d=0.2", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    ).stderr
    assert "silence_duration" in detected, detected
    longest = max(
        float(line.split("silence_duration:")[1].strip())
        for line in detected.splitlines() if "silence_duration:" in line
    )
    assert longest > 0.3, f"only {longest:.2f}s between the sentences"


# ------------------------------------------------------------- the pace
# Kokoro at its own rate reads about 165 words a minute, which is brisk for
# someone explaining something. The difference between that and a voice that
# sounds like it wants the video over with is a multiplier and some silence.

from videosynx.providers import (  # noqa: E402
    DEFAULT_PACE, KOKORO_SPEEDS, speed_for,
)


def test_the_calm_pace_is_slower_than_the_engines_own():
    assert speed_for("calm") < speed_for("normal") < speed_for("fast")
    assert speed_for("fast") == 1.0


def test_an_unknown_pace_falls_back_rather_than_failing():
    """A render should not die on a value somebody typed by hand."""
    assert speed_for(None) == KOKORO_SPEEDS[DEFAULT_PACE]
    assert speed_for("leisurely") == KOKORO_SPEEDS[DEFAULT_PACE]


def test_the_default_is_the_calm_one():
    assert DEFAULT_PACE == "calm"


def test_a_full_stop_is_held_long_enough_to_read_as_one():
    """Half a second is a comma with delusions. A reader holds nearer eight
    tenths at a full stop and the ear is what decides it, so the floor is set
    where a listener stops hearing a list and starts hearing sentences."""
    assert SENTENCE_PAUSE_S >= 0.6
    assert PARAGRAPH_PAUSE_S >= SENTENCE_PAUSE_S * 2
