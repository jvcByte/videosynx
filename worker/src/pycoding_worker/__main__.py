"""Run a worker: python -m pycoding_worker --dsn ..."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .db import Queue, new_owner
from .storage import from_env
from .worker import EngineRenderer, Worker


def main(argv: list[str] | None = None) -> int:
    from pycoding.dotenv import load

    load()

    parser = argparse.ArgumentParser(description="pycoding render worker.")
    parser.add_argument("--dsn", default="postgresql:///pycoding",
                        help="Postgres connection string")
    parser.add_argument("--out", type=Path, default=Path("/tmp/pycoding/queue"),
                        help="where rendered videos are written (until Phase 6 "
                             "replaces this with an object store)")
    parser.add_argument("--tts", default="auto",
                        choices=["auto", "elevenlabs", "kokoro", "espeak"])
    parser.add_argument("--no-execute", action="store_true",
                        help="skip running snippets in the sandbox")
    parser.add_argument("--once", action="store_true",
                        help="process at most one job and exit")
    parser.add_argument("--install", action="store_true",
                        help="create the schema and exit")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    queue = Queue(args.dsn)
    if args.install:
        queue.install()
        print("schema installed")
        return 0

    worker = Worker(
        queue,
        EngineRenderer(
            args.out,
            tts=args.tts,
            execute=not args.no_execute,
            storage=from_env(),
        ),
        owner=new_owner(),
    )
    print(f"worker {worker.owner} watching {args.dsn}")
    if args.once:
        handled = worker.run_once()
        print("processed one job" if handled else "queue empty")
    else:
        worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
