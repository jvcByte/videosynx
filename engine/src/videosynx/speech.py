"""Text in, speech out.

The narration half of the pipeline, without the video around it. The engine
has synthesised speech since the first tutorial -- `Speaker.say` is what every
line of narration goes through -- so this is the same machinery with the
frames, the sandbox and the encoder taken away, and the audio written to a
file instead of piped into a video.

Synthesis runs at roughly 0.8x realtime on this machine, which is the number
that shapes everything around it: five minutes of speech is four minutes of
CPU, so a caller queues this rather than waiting on a request.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .providers import Speaker
import numpy as np

from .narration import trim_silence
from .prompts import sentences
from .timeline import SR

# Mono 48 kHz in, mono mp3 out. The rate is the timeline's own, so a clip made
# here and a clip in a video are the same audio rather than two things that
# nearly match.
BITRATE = "96k"

# How long to hold after a sentence, and after a paragraph.
#
# Kokoro does not pause at a full stop: measured at about 60 ms of trailing
# silence, which is nothing. A reader does, and a listener uses it -- without
# a gap the sentences run together and the whole clip reads as one long
# breath. The video path never had this problem because its phrases are laid
# out on a timeline with room between them; concatenating PCM directly is
# what exposed it.
SENTENCE_PAUSE_S = 0.45
PARAGRAPH_PAUSE_S = 0.9


def _segments(text: str) -> list[tuple[str, float]]:
    """Sentences, each with the silence that follows it.

    Paragraphs are honoured as well as full stops: `sentences` splits on
    whitespace after a full stop, so a blank line is consumed as one and a
    pasted article loses the shape it was written in. A paragraph is a longer
    pause than a sentence, and both are a pause.
    """
    out: list[tuple[str, float]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        said = sentences(block)
        for index, sentence in enumerate(said):
            last_of_paragraph = index == len(said) - 1
            out.append((sentence, PARAGRAPH_PAUSE_S if last_of_paragraph
                        else SENTENCE_PAUSE_S))
    # Nothing is read after the last one, so nothing is held after it either.
    if out:
        out[-1] = (out[-1][0], 0.0)
    return out


def synthesise(
    text: str,
    speaker: Speaker,
    out: Path,
    *,
    should_stop: Callable[[], None] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> float:
    """Write *text* to *out* as speech, and return its length in seconds.

    Sentence at a time, the same way the narration does it -- which is not
    only for consistency. Kokoro's quality falls off on long input, and a
    paragraph read as one block is audibly worse than the same paragraph read
    as sentences.

    Sentence at a time for the other two reasons as well: it is the only
    place either of these can go. Synthesis is roughly half realtime, so a
    long clip is minutes of CPU in one call, and a stop that is only noticed
    when the call returns is not a stop. Progress has the same problem from
    the other side -- one call means the job sits at its opening percentage
    for the whole of it, which on a four-minute clip is a progress bar that
    has stopped.
    """
    parts = _segments(text)
    if not parts:
        raise ValueError("nothing to say")

    chunks: list[bytes] = []
    for index, (sentence, pause) in enumerate(parts):
        if should_stop is not None:
            should_stop()
        if on_progress is not None:
            on_progress(index / len(parts))
        chunks.append(trim_silence(speaker.say(sentence)).tobytes())
        if pause:
            chunks.append(np.zeros(int(pause * SR), dtype=np.int16).tobytes())
    if on_progress is not None:
        on_progress(1.0)

    pcm = b"".join(chunks)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
            "-c:a", "libmp3lame", "-b:a", BITRATE,
            "-y", str(out),
        ],
        input=pcm,
        check=True,
        timeout=600,
    )
    # From the PCM rather than from the file: it is the same number, and it
    # does not need ffprobe to agree with the sample count we just wrote.
    return len(pcm) / 2 / SR


def label(text: str, limit: int = 120) -> str:
    """What to call a clip in a list, from the text itself.

    The first line that says anything, which is what `job_label` on the API
    does for a pasted script. A caller should not have to name a thing twice.

    Its first *sentence*, though, because prose pasted as one line is one line
    -- and a row labelled with the opening hundred and twenty characters of a
    paragraph is a row nobody can tell apart from the next one.
    """
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            first = sentences(cleaned)[0] if sentences(cleaned) else cleaned
            return first[:limit]
    return "untitled"
