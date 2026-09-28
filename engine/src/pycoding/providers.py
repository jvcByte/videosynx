"""The two outside services, behind small interfaces.

Everything the pipeline needs from the network is one call that returns text
(code and narration) and one that returns audio. Keeping them behind
protocols means the whole pipeline is exercisable offline, which is what the
tests do -- no key, no quota, no spend.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Protocol

import numpy as np
from rich.console import Console

from .timeline import SR

_console = Console()

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_VOICE_MODEL = "eleven_v3"
# Raw PCM at the timeline's own rate: no decode step, no MP3 encoder priming,
# and the sample count is exactly what came back.
VOICE_FORMAT = "pcm_48000"
# Seconds. Long enough for a sentence, short enough that a stalled connection
# fails the sentence rather than the job.
TTS_TIMEOUT_S = 60
TTS_RETRIES = 3

# 503 "high demand" and 429 "rate limited" are routine on the free tier and
# clear on their own; anything else is a real error and should not be retried
# six times with backoff before surfacing.
RETRIES = 4
BACKOFF_CAP_S = 45
TRANSIENT = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "overloaded")
# Free-tier quota is enforced per model, so a different model is usually the
# way past a limit rather than a reason to wait.
DEFAULT_FALLBACKS = (
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
)


def _error_text(exc: Exception) -> str:
    return str(getattr(exc, "body", None) or exc)


def is_transient(exc: Exception) -> bool:
    return any(marker in _error_text(exc) for marker in TRANSIENT)


def is_daily_quota(exc: Exception) -> bool:
    """A per-day quota will not clear while we wait, so retrying is pointless.

    The free tier's 20-requests-per-day limit is per *model*, so the useful
    response is to move to another model rather than to sleep.
    """
    text = _error_text(exc)
    return "PerDay" in text or "per day" in text.lower()


def retry_after(exc: Exception, attempt: int) -> float:
    """Wait the API asked for, or an exponential backoff if it did not say."""
    text = str(getattr(exc, "body", None) or exc)
    hinted = re.search(r"retry[Dd]elay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
    if hinted:
        return min(float(hinted.group(1)) + 2, 70)
    return min(2.0 ** attempt, BACKOFF_CAP_S)


def summarise(failures: list[tuple[str, ProviderError]]) -> tuple[str, str]:
    """One short line for every model failing, plus the overall reason.

    Every model failing for the same cause is *one* fact, not five, and it is
    the difference between a message someone can act on and a wall of json.
    """
    reasons = {exc.reason for _, exc in failures}
    if reasons == {"quota"}:
        return (
            f"all {len(failures)} models are out of free-tier quota; it is "
            "per model, per day, and resets daily",
            "quota",
        )
    if len(reasons) == 1:
        only = reasons.pop()
        return f"every model failed: {only}", only
    detail = "; ".join(f"{model}: {exc}" for model, exc in failures)
    return detail[:300], "error"


class ProviderError(RuntimeError):
    """A model call failed.

    ``reason`` is a short tag -- ``quota``, ``busy``, ``error`` -- so a caller
    can react without parsing prose, and so a failed job's ``error_code`` can
    say something a person can act on rather than naming an exception class.
    """

    def __init__(self, message: str, *, reason: str = "error"):
        super().__init__(message)
        self.reason = reason


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
    """Code and narration from Gemini.

    Free-tier models are unreliable in two distinct ways: per-day request
    quotas that are enforced per model, and 503s when a model is under load.
    Both are common enough that a single model is not enough, so this walks a
    fallback chain and only gives up when every model has failed.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 fallbacks: tuple[str, ...] = DEFAULT_FALLBACKS):
        if not api_key:
            raise ProviderError("a Gemini API key is required")
        import google.genai as genai

        self._client = genai.Client(api_key=api_key)
        self._models = [model, *(m for m in fallbacks if m != model)]
        self._model = model

    def write(self, prompt: str) -> str:
        failures: list[tuple[str, ProviderError]] = []
        for index, model in enumerate(self._models):
            try:
                return self._ask(model, prompt)
            except ProviderError as exc:
                failures.append((model, exc))
                if index + 1 < len(self._models):
                    _console.log(
                        f"[yellow]{model} unavailable, trying "
                        f"{self._models[index + 1]}[/yellow]"
                    )
        message, reason = summarise(failures)
        raise ProviderError(message, reason=reason)

    def _ask(self, model: str, prompt: str) -> str:
        for attempt in range(1, RETRIES + 1):
            try:
                response = self._client.models.generate_content(
                    model=model, contents=prompt
                )
                text = (response.text or "").strip()
                if not text:
                    raise ProviderError("the model returned nothing")
                return text
            except Exception as exc:  # noqa: BLE001 - re-raised below
                if is_daily_quota(exc):
                    # Sleeping will not help; let the caller try another model.
                    # The reason travels, the API's error document does not:
                    # five copies of a 429 body is what turned a job's error
                    # message into two thousand characters of unreadable json.
                    raise ProviderError("daily quota exhausted", reason="quota") from exc
                if not is_transient(exc) or attempt == RETRIES:
                    raise ProviderError(str(exc)[:200]) from exc
                wait = retry_after(exc, attempt)
                _console.log(
                    f"[yellow]{model} under load (attempt {attempt}/{RETRIES}), "
                    f"retrying in {wait:.0f}s[/yellow]"
                )
                time.sleep(wait)
        raise ProviderError(f"failed after {RETRIES} attempts")


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

        # Explicit and tight. A stalled stream otherwise hangs the whole job:
        # one run sat blocked for three hours with two seconds of CPU. The
        # SDK's own retries are disabled so this module controls the budget.
        self._client = ElevenLabs(api_key=api_key, timeout=TTS_TIMEOUT_S)
        self._voice_id = voice_id
        self._model_id = model_id
        # Not every voice model accepts neighbour text -- eleven_v3 rejects it
        # outright -- so this is discovered on first use rather than guessed.
        self._context_supported = True

    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        if self._context_supported and (previous or following):
            try:
                return self._convert(text, previous, following)
            except Exception as exc:  # noqa: BLE001 - re-raised unless it is this
                if "unsupported_model" not in _error_text(exc):
                    raise ProviderError(str(exc)) from exc
                self._context_supported = False
                _console.log(
                    f"[yellow]{self._model_id} does not accept previous/next text; "
                    "synthesising each sentence independently[/yellow]"
                )
        return self._convert(text, "", "")

    def _convert(self, text: str, previous: str, following: str) -> np.ndarray:
        from elevenlabs import VoiceSettings

        kwargs: dict = {}
        if previous:
            kwargs["previous_text"] = previous
        if following:
            kwargs["next_text"] = following

        last: Exception | None = None
        for attempt in range(1, TTS_RETRIES + 1):
            try:
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
                    request_options={
                        "timeout_in_seconds": TTS_TIMEOUT_S,
                        # This module owns the retry budget; stacked SDK retries
                        # inside our own retries is how a stall became hours.
                        "max_retries": 0,
                    },
                    **kwargs,
                )
                pcm = np.frombuffer(b"".join(chunks), dtype=np.int16)
                if len(pcm) == 0:
                    raise ProviderError("ElevenLabs returned no audio")
                return pcm.copy()
            except Exception as exc:  # noqa: BLE001 - re-raised below
                last = exc
                if "quota_exceeded" in _error_text(exc):
                    # Retrying cannot help, and a half-narrated video is worse
                    # than none: fail the job so nobody is charged for it.
                    raise ProviderError(
                        f"ElevenLabs quota exhausted: {_error_text(exc)[:300]}"
                    ) from exc
                if attempt == TTS_RETRIES:
                    break
                wait = retry_after(exc, attempt)
                _console.log(
                    f"[yellow]speech failed (attempt {attempt}/{TTS_RETRIES}), "
                    f"retrying in {wait:.0f}s[/yellow]"
                )
                time.sleep(wait)
        raise ProviderError(f"ElevenLabs failed after {TTS_RETRIES} attempts: {last}")


# ------------------------------------------------------------------ offline

KOKORO_DIR = Path.home() / ".cache" / "kokoro"
KOKORO_MODEL = KOKORO_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES = KOKORO_DIR / "voices-v1.0.bin"
DEFAULT_KOKORO_VOICE = "af_heart"


def resample_to_timeline(samples: np.ndarray, source_rate: int) -> np.ndarray:
    """Convert float audio at *source_rate* to int16 PCM at the timeline's rate.

    Kokoro emits 24 kHz and the frame grid is built on 48 kHz. Linear
    interpolation is adequate for a 2x upsample of speech, which is already
    band-limited well below either rate.
    """
    if source_rate == SR:
        return (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    count = int(round(len(samples) * SR / source_rate))
    warped = np.interp(
        np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples
    )
    return (np.clip(warped, -1.0, 1.0) * 32767).astype(np.int16)


class KokoroSpeaker:
    """Local neural speech, running on CPU.

    Apache 2.0 and no per-character cost, which matters for a service where
    narration is otherwise the dominant expense. Not ElevenLabs -- nothing
    open is -- but markedly more natural than a formant synthesiser, and for
    neutral explanatory narration that difference is small.
    """

    def __init__(self, voice: str = DEFAULT_KOKORO_VOICE, speed: float = 1.0,
                 model_path: Path | None = None, voices_path: Path | None = None):
        model = Path(model_path or KOKORO_MODEL)
        voices = Path(voices_path or KOKORO_VOICES)
        if not model.exists() or not voices.exists():
            raise ProviderError(
                f"Kokoro model files not found in {model.parent}. Download "
                "kokoro-v1.0.onnx and voices-v1.0.bin from the kokoro-onnx "
                "releases page, or pass --tts espeak."
            )
        from kokoro_onnx import Kokoro

        self._kokoro = Kokoro(str(model), str(voices))
        # An unknown voice name would otherwise fail at synthesis, which on a
        # render is minutes in and after everything else has been paid for.
        # Catch it while the model is still open, and fall back.
        get_voices = getattr(self._kokoro, "get_voices", None)
        known = set(get_voices()) if callable(get_voices) else set()
        if known and voice not in known:
            _console.log(
                f"[yellow]unknown voice {voice!r}; using {DEFAULT_KOKORO_VOICE}[/yellow]"
            )
            voice = DEFAULT_KOKORO_VOICE
        self._voice = voice
        self._speed = speed

    def say(self, text: str, *, previous: str = "", following: str = "") -> np.ndarray:
        # Neighbour text is accepted for interface parity and ignored: Kokoro
        # has no equivalent, and each sentence carries its own prosody fine.
        samples, rate = self._kokoro.create(
            text, voice=self._voice, speed=self._speed, lang="en-us"
        )
        return resample_to_timeline(samples, rate)


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
