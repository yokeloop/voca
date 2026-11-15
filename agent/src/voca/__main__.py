import argparse
import sys
from datetime import datetime
from pathlib import Path

from aiohttp import web

from voca.app.web import create_app, default_dist_dir
from voca.config import Settings, config_path


def archive_search(path: Path, query: str, limit: int) -> None:
    from voca.session.store import SearchError, SessionStore, StoreError

    try:
        store = SessionStore.open_readonly(path)
        try:
            hits = store.search(query, limit)
        finally:
            store.close()
    except SearchError as error:
        print(f"voca archive: неверный запрос: {error}", file=sys.stderr)
        sys.exit(2)
    except StoreError as error:
        print(f"voca archive: {error}", file=sys.stderr)
        sys.exit(2)
    for hit in hits:
        snippet = hit.snippet.replace("\n", " ")
        print(
            f"{hit.id}\t{datetime.fromtimestamp(hit.at):%Y-%m-%d %H:%M}\t{hit.kind}\t{snippet}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="voca")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("web", help="serve the page and the token endpoint")
    agent = commands.add_parser("agent", help="run the LiveKit agent worker")
    agent.add_argument("mode", choices=["dev", "start", "console"])
    agent.add_argument("rest", nargs=argparse.REMAINDER)
    archive = commands.add_parser("archive", help="search the session archive")
    archive_commands = archive.add_subparsers(dest="archive_command", required=True)
    search = archive_commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.command == "agent":
        from voca.app.worker import run

        run(args.mode, args.rest)
        return
    if args.command == "archive":
        try:
            settings = Settings()
        except ValueError as error:
            print(
                f"voca archive: конфиг не прочитан ({config_path()}): {error}",
                file=sys.stderr,
            )
            sys.exit(2)
        archive_search(settings.session.path, args.query, args.limit)
        return
    settings = Settings()
    try:
        app = create_app(settings, default_dist_dir())
    except FileNotFoundError as error:
        print(f"voca web: {error}", file=sys.stderr)
        sys.exit(2)
    web.run_app(app, host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    main()
