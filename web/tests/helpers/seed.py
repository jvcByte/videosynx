#!/usr/bin/env python3
"""Drive a queued job to succeeded without rendering.

A real render takes minutes, which is far too slow for a UI test. This
stands in for the worker's last step -- upload the artifact, mark the row --
so the browser test can exercise submit, poll and playback against the real
API, real Postgres and real storage. Only the part that takes minutes is
skipped.

Usage: seed.py <dsn> <artifacts-root> <job_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from pycoding_worker.storage import LocalStorage, video_key

# A few bytes is enough: the browser only has to fetch it and attach it to a
# <video>. Playability is checked elsewhere, against a real render.
FAKE_MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 120


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    dsn, root, job_id = argv[1], argv[2], argv[3]

    store = LocalStorage(Path(root))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("select tier from jobs where id = %s", (job_id,)).fetchone()
        if row is None:
            print(f"no such job: {job_id}", file=sys.stderr)
            return 1
        tier = row["tier"]

        source = Path(root) / f"{job_id}.incoming.mp4"
        source.write_bytes(FAKE_MP4)
        key = video_key(tier, job_id)
        store.put(key, source)

        conn.execute(
            """
            update jobs
               set status = 'succeeded', artifact_key = %s, duration_s = 42.0,
                   finished_at = now(), progress = 1
             where id = %s
            """,
            (key, job_id),
        )
    print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
