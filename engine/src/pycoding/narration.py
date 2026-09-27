"""Decoding narration audio to exact PCM.

The timeline needs an exact sample count, so the MP3 is decoded rather than
trusting header metadata: ``MP3.info.length`` is derived from the frame count
and can disagree with the true decoded length, which would put the audio and
video a few milliseconds apart at every snippet boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .timeline import SR


def decode_pcm(path: Path | str) -> np.ndarray:
    """Decode an audio file to mono int16 PCM at ``SR``."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-f", "s16le", "-ar", str(SR), "-ac", "1", "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg could not decode {path}: {result.stderr.decode('utf-8', 'replace')[-500:]}"
        )
    # .copy() because frombuffer returns a read-only view, and callers need to
    # apply fades and mixing in place.
    return np.frombuffer(result.stdout, dtype=np.int16).copy()


def trim_silence(samples: np.ndarray, threshold: int = 120,
                 keep_s: float = 0.03) -> np.ndarray:
    """Strip leading and trailing silence from a synthesised phrase.

    TTS engines pad their output. Left in, that padding becomes dead air the
    caption is still sitting on, so captions linger after the sentence ends --
    measured at ~330 ms per sentence before this was added.
    """
    if len(samples) == 0:
        return samples
    audible = np.abs(samples) > threshold
    if not audible.any():
        return samples[:0]
    first = int(np.argmax(audible))
    last = len(audible) - int(np.argmax(audible[::-1]))
    margin = int(keep_s * SR)
    return samples[max(0, first - margin) : min(len(samples), last + margin)]
