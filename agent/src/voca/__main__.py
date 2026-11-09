import argparse
import sys

from aiohttp import web

from voca.app.web import create_app, default_dist_dir
from voca.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="voca")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("web", help="serve the page and the token endpoint")
    parser.parse_args()
    settings = Settings()
    try:
        app = create_app(settings, default_dist_dir())
    except FileNotFoundError as error:
        print(f"voca web: {error}", file=sys.stderr)
        sys.exit(2)
    web.run_app(app, host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    main()
