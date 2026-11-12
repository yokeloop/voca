import argparse
import sys

from aiohttp import web

from voca.app.web import create_app, default_dist_dir
from voca.app.worker import run
from voca.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="voca")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("web", help="serve the page and the token endpoint")
    agent = commands.add_parser("agent", help="run the LiveKit agent worker")
    agent.add_argument("mode", choices=["dev", "start", "console"])
    agent.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "agent":
        run(args.mode, args.rest)
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
