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
from pathlib import Path

from .providers import Speaker
from .prompts import sentences
from .script import speak_plain
from .timeline import SR

# Mono 48 kHz in, mono mp3 out. The rate is the timeline's own, so a clip made
# here and a clip in a video are the same audio rather than two things that
# nearly match.
BITRATE = "96k"


def synthesise(text: str, speaker: Speaker, out: Path) -> float:
    """Write *text* to *out* as speech, and return its length in seconds.

    Sentence at a time, through the same `speak_plain` the narration uses --
    which is not only for consistency. Kokoro's quality falls off on long
    input, and a paragraph read as one block is audibly worse than the same
    paragraph read as sentences.
    """
    phrases = speak_plain(speaker, text)
    if not phrases:
        raise ValueError("nothing to say")

    pcm = b"".join(p.samples.tobytes() for p in phrases)
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
