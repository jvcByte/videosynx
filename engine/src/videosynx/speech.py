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

import subprocess
from collections.abc import Callable
from pathlib import Path

from .providers import Speaker
from .narration import trim_silence
from .prompts import sentences
from .timeline import SR

# Mono 48 kHz in, mono mp3 out. The rate is the timeline's own, so a clip made
# here and a clip in a video are the same audio rather than two things that
# nearly match.
BITRATE = "96k"


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
    parts = sentences(text)
    if not parts:
        raise ValueError("nothing to say")

    chunks: list[bytes] = []
    for index, sentence in enumerate(parts):
        if should_stop is not None:
            should_stop()
        if on_progress is not None:
            on_progress(index / len(parts))
        chunks.append(trim_silence(speaker.say(sentence)).tobytes())
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
