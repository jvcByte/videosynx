"""Raw RGB frames piped straight into ffmpeg.

N buffers written == N frames emitted. No temp files, no image codec, no
variable-frame-rate guessing -- which is what makes the video side of the
timeline as exact as the audio side.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..timeline import FPS, SR


class SinkError(RuntimeError):
    pass


class VideoSink:
    """Write fixed-size RGB frames to ffmpeg over a pipe, optionally with PCM audio.

    Audio and video are the same length by construction: the caller writes
    exactly ``total_frames`` buffers and passes exactly ``total_frames * SPF``
    samples, both derived from the same integer. ``-shortest`` is deliberately
    not used -- it would hide a mismatch instead of letting it surface.
    """

    def __init__(self, path: Path | str, width: int, height: int,
                 audio: np.ndarray | None = None, fps: int = FPS,
                 crf: int = 20, preset: str = "veryfast"):
        if not shutil.which("ffmpeg"):
            raise SinkError("ffmpeg not found on PATH")
        self.path = Path(path)
        self.width, self.height = width, height
        self.frame_bytes = width * height * 3
        self.frames_written = 0
        self._pcm_file: Path | None = None

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        ]
        if audio is not None:
            fd, pcm_path = tempfile.mkstemp(suffix=".s16le", prefix="pycoding_")
            os.close(fd)
            self._pcm_file = Path(pcm_path)
            audio.astype(np.int16).tofile(self._pcm_file)
            cmd += ["-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", str(self._pcm_file)]
        cmd += [
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p",
        ]
        if audio is not None:
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += ["-movflags", "+faststart", str(self.path)]

        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def write(self, buf: bytes) -> None:
        if len(buf) != self.frame_bytes:
            raise SinkError(
                f"frame is {len(buf)} bytes, expected {self.frame_bytes} "
                f"for {self.width}x{self.height}"
            )
        try:
            self._proc.stdin.write(buf)
        except BrokenPipeError as e:
            raise SinkError(f"ffmpeg died: {self._stderr_text()}") from e
        self.frames_written += 1

    def _stderr_text(self) -> str:
        if self._proc.stderr is None:
            return ""
        try:
            return self._proc.stderr.read().decode("utf-8", "replace")[-2000:]
        except Exception:
            return ""

    def close(self) -> None:
        """Flush, wait for ffmpeg, and verify it succeeded."""
        if self._proc.stdin and not self._proc.stdin.closed:
            self._proc.stdin.close()
        code = self._proc.wait()
        if self._pcm_file is not None:
            self._pcm_file.unlink(missing_ok=True)
            self._pcm_file = None
        if code != 0:
            raise SinkError(f"ffmpeg exited {code}: {self._stderr_text()}")

    def __enter__(self) -> "VideoSink":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self._proc.kill()
            if self._pcm_file is not None:
                self._pcm_file.unlink(missing_ok=True)
            return
        self.close()
