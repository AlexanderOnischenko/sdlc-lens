"""CLI for passive collection and the read-only observability server."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

from .collector import collect
from .store import Store


def default_repo() -> Path:
    return (Path(__file__).resolve().parents[2] / "Touristico").resolve()


def database_url(value: str | None) -> str:
    if value:
        return value
    configured = os.environ.get("SDLC_LENS_DATABASE_URL")
    if configured:
        return configured
    path = Path.cwd() / ".local" / "sdlc-lens.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdlc-lens")
    parser.add_argument("--repo", type=Path, default=default_repo(), help="Read-only source repository")
    parser.add_argument("--repository-id", default="touristico")
    parser.add_argument("--database-url", help="SQLAlchemy URL; defaults to local SQLite")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="Capture a new immutable snapshot if files changed")
    watch = sub.add_parser("watch", help="Poll the source repository for changes")
    watch.add_argument("--interval", type=float, default=10.0)
    serve = sub.add_parser("serve", help="Run the read-only HTTP API and console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--interval", type=float, default=10.0,
                       help="Polling interval; 0 disables background scans")
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    if not (root / "docs").is_dir():
        parser.error(f"source repository has no docs directory: {root}")
    store = Store(database_url(args.database_url))
    try:
        if args.command == "scan":
            print(json.dumps(collect(root, store, args.repository_id), ensure_ascii=False, indent=2))
            return 0
        if args.command == "watch":
            if args.interval <= 0:
                parser.error("--interval must be positive")
            import time
            while True:
                try:
                    result = collect(root, store, args.repository_id)
                    if result["changed"]:
                        print(json.dumps(result, ensure_ascii=False), flush=True)
                except Exception as exc:  # watcher must keep retrying after transient read errors
                    print(f"scan failed: {exc}", file=sys.stderr, flush=True)
                time.sleep(args.interval)
        if args.command == "serve":
            from .api import create_app
            import uvicorn

            initial = collect(root, store, args.repository_id)
            print(json.dumps(initial, ensure_ascii=False), flush=True)
            stop = threading.Event()

            def loop() -> None:
                while not stop.wait(args.interval):
                    try:
                        collect(root, store, args.repository_id)
                    except Exception as exc:
                        print(f"background scan failed: {exc}", file=sys.stderr, flush=True)

            if args.interval > 0:
                worker = threading.Thread(target=loop, daemon=True, name="sdlc-scan")
                worker.start()
            else:
                worker = None
            try:
                uvicorn.run(create_app(store, args.repository_id), host=args.host, port=args.port)
            finally:
                stop.set()
                if worker is not None:
                    worker.join()
            return 0
    except KeyboardInterrupt:
        return 130
    finally:
        store.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
