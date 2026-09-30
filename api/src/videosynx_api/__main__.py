"""Run the API: python -m videosynx_api --dsn ..."""

from __future__ import annotations

import argparse
import os
import sys

from .app import create_app


def main(argv: list[str] | None = None) -> int:
    from videosynx.dotenv import load

    load()

    parser = argparse.ArgumentParser(description="videosynx HTTP API.")
    parser.add_argument("--dsn", default=os.environ.get("VIDEOSYNX_DSN", "postgresql:///videosynx"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(create_app(args.dsn), host=args.host, port=args.port,
                reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
