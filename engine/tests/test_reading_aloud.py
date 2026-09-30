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
    assert seconds == pytest.approx(0.4)


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
