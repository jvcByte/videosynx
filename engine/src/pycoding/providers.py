"""The two outside services, behind small interfaces.

Everything the pipeline needs from the network is one call that returns text
(code and narration) and one that returns audio. Keeping them behind
protocols means the whole pipeline is exercisable offline, which is what the
tests do -- no key, no quota, no spend.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Protocol

import numpy as np

from .timeline import SR

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_VOICE_MODEL = "eleven_v3"
# Raw PCM at the timeline's own rate: no decode step, no MP3 encoder priming,
# and the sample count is exactly what came back.
VOICE_FORMAT = "pcm_48000"

RETRIES = 4


class ProviderError(RuntimeError):
    pass


class Writer(Protocol):
    def write(self, prompt: str) -> str:
        """Return the model's response to *prompt*."""


class Speaker(Protocol):
    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        """Synthesise *text* to mono int16 PCM at ``SR``.

        ``previous`` and ``following`` are the neighbouring sentences. They are
        hints for prosody, not content -- without them, synthesising sentence
        by sentence gives every sentence its own final intonation, which is
        what makes narration sound clipped at the joins.
        """


# ------------------------------------------------------------------ Gemini

class GeminiWriter:
    """Code and narration from Gemini."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if not api_key:
            raise ProviderError("a Gemini API key is required")
        import google.genai as genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def write(self, prompt: str) -> str:
        last: Exception | None = None
        for attempt in range(1, RETRIES + 1):
            try:
                response = self._client.models.generate_content(
                    model=self._model, contents=prompt
                )
                text = (response.text or "").strip()
                if not text:
                    raise ProviderError("the model returned nothing")
                return text
            except Exception as exc:  # noqa: BLE001 - surfaced below if it persists
                last = exc
                if attempt == RETRIES:
                    break
                # Rate limits and demand spikes are normal on the free tier.
                time.sleep(min(2 ** attempt, 30))
        raise ProviderError(f"{self._model} failed after {RETRIES} attempts: {last}")


# -------------------------------------------------------------- ElevenLabs

class ElevenLabsSpeaker:
    """Narration from ElevenLabs, returned as raw PCM."""

    def __init__(self, api_key: str, voice_id: str,
                 model_id: str = DEFAULT_VOICE_MODEL):
        if not api_key:
            raise ProviderError("an ElevenLabs API key is required")
        if not voice_id:
            raise ProviderError("an ElevenLabs voice ID is required")
        from elevenlabs.client import ElevenLabs

        self._client = ElevenLabs(api_key=api_key)
        self._voice_id = voice_id
        self._model_id = model_id

    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        from elevenlabs import VoiceSettings

        chunks = self._client.text_to_speech.convert(
            voice_id=self._voice_id,
            text=text,
            model_id=self._model_id,
            output_format=VOICE_FORMAT,
            voice_settings=VoiceSettings(
                stability=0.1,
                similarity_boost=1.0,
                style=0.5,
                use_speaker_boost=True,
            ),
            previous_text=previous or None,
            next_text=following or None,
        )
        pcm = np.frombuffer(b"".join(chunks), dtype=np.int16)
        if len(pcm) == 0:
            raise ProviderError("ElevenLabs returned no audio")
        return pcm.copy()


# ------------------------------------------------------------------ offline

class ESpeakSpeaker:
    """Offline speech, for development.

    Robotic on purpose: it exists so the pipeline can be run and judged
    without spending anything, not to be shipped.
    """

    def __init__(self, wpm: int = 165):
        self.engine = shutil.which("espeak-ng") or shutil.which("espeak")
        self.wpm = wpm

    @property
    def available(self) -> bool:
        return self.engine is not None

    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        if self.engine is None:
            raise ProviderError("espeak-ng is not installed")
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "line.wav"
            subprocess.run(
                [self.engine, "-v", "en-us", "-s", str(self.wpm), "-w", str(wav), text],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(wav), "-f", "s16le",
                 "-ar", str(SR), "-ac", "1", "-"],
                capture_output=True, check=True,
            ).stdout
        return np.frombuffer(raw, dtype=np.int16).copy()


class ToneSpeaker:
    """Last-resort stand-in so the pipeline runs with nothing installed."""

    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        length = int(SR * max(1.2, len(text) * 0.055))
        t = np.arange(length, dtype=np.float64)
        return (np.sin(2 * np.pi * 320 * t / SR) * 9000).astype(np.int16)


class ScriptedWriter:
    """A Writer that replays canned responses, for tests and offline runs."""

    def __init__(self, responses: list[str]):
        if not responses:
            raise ProviderError("ScriptedWriter needs at least one response")
        self._responses = list(responses)
        self.prompts: list[str] = []

    def write(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self._responses) - 1)
        return self._responses[index]
