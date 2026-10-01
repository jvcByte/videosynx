#!/usr/bin/env python3
"""Drive a queued job to succeeded without rendering.

A real render takes minutes, which is far too slow for a UI test. This
stands in for the worker's last step -- upload the artifact, mark the row --
so the browser test can exercise submit, poll and playback against the real
API, real Postgres and real storage. Only the part that takes minutes is
skipped.

An artifact can be seeded as audio rather than video, because a clip is
stored under its own suffix -- the third argument, ".mp4" unless given.

Usage: seed.py <dsn> <artifacts-root> <job_id> [suffix]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from videosynx_worker.storage import LocalStorage, video_key

# A few bytes is enough: the browser only has to fetch it and attach it to a
# <video>. Playability is checked elsewhere, against a real render.
FAKE_MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 120


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        print(__doc__, file=sys.stderr)
        return 2
    dsn, root, job_id = argv[1], argv[2], argv[3]
    suffix = argv[4] if len(argv) == 5 else ".mp4"

    store = LocalStorage(Path(root))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("select tier from jobs where id = %s", (job_id,)).fetchone()
        if row is None:
            print(f"no such job: {job_id}", file=sys.stderr)
            return 1
        tier = row["tier"]

        source = Path(root) / f"{job_id}.incoming{suffix}"
        source.write_bytes(FAKE_MP4)
        key = video_key(tier, job_id, suffix)
        store.put(key, source)

        # The plan the worker writes beside the video, in the same shape
        # `Tutorial.export` produces. Written here so the inspector has
        # something real to render rather than only its "no plan" fallback.
        plan = {
            "topic": "seeded",
            "total_frames": 600,
            "duration_s": 20.0,
            "sections": [
                {
                    "kind": "explain",
                    "heading": "A seeded slide",
                    "columns": [["Left", ["one", "two"]], ["Right", ["three"]]],
                    "diagram": None,
                    "note": "the takeaway",
                    "narration": [{"text": "One sentence.", "frames": 90},
                                  {"text": "Another sentence.", "frames": 120}],
                },
                {
                    "kind": "code",
                    "code": "print('seeded')\n",
                    "output": ["seeded"],
                    "narration": [{"text": "It prints.", "anchor": 0,
                                   "anchored": True, "frames": 60}],
                },
            ],
        }
        plan_key = video_key(tier, job_id, ".script.json")
        plan_source = Path(root) / f"{job_id}.plan.json"
        plan_source.write_text(json.dumps(plan))

        conn.execute(
            """
            update jobs
               set status = 'succeeded', artifact_key = %s, duration_s = 42.0,
                   finished_at = now(), progress = 1
             where id = %s
            """,
            (key, job_id),
        )
        # After the row, so a failure here does not leave a job that claims a
        # plan it does not have.
        store.put(plan_key, plan_source)
        plan_source.unlink(missing_ok=True)
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
