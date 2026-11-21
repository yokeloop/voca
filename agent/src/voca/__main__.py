import argparse
import asyncio
import sys
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from voca.app.web import create_app, default_dist_dir
from voca.config import Settings, config_path

if TYPE_CHECKING:
    from voca.delegation.model import Task


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


def task_printer() -> Callable[["Task"], None]:
    shown: dict[str, str] = {}

    def print_task(task: "Task") -> None:
        line = f"{task.tool_call_id}\t{task.status}\t{task.run_id or '-'}"
        if task.stop_requested_at is not None:
            line += "\tstop"
            if task.stop_reason is not None:
                line += f" {task.stop_reason}"
        if task.status == "waiting_approval" and task.approval is not None:
            line += (
                f"\n  description: {task.approval.get('description')}"
                f"\n  command: {task.approval.get('command')}"
                f"\n  choices: {', '.join(map(str, task.approval.get('choices') or []))}"
            )
        if shown.get(task.tool_call_id) != line:
            shown[task.tool_call_id] = line
            print(line, flush=True)

    return print_task


def print_outcome(task: "Task") -> int:
    if task.status == "succeeded":
        print("result:")
        print(task.result or "")
        return 0
    if task.status == "failed":
        print(f"error: {task.error}")
        return 1
    return 1


async def bench_delegate(settings: Settings, args: argparse.Namespace) -> int:
    from voca.delegation.hermes import HermesBackend
    from voca.delegation.ports import (
        ApprovalNotActive,
        DelegationError,
        TaskNotFound,
        Unavailable,
    )
    from voca.delegation.service import DelegationService
    from voca.delegation.sqlite import SqliteTaskRepository, TaskStoreError

    assert settings.hermes_key is not None
    try:
        repo = SqliteTaskRepository.open(settings.delegation.path)
    except TaskStoreError as error:
        print(f"voca bench: {error}", file=sys.stderr)
        return 2
    backend = HermesBackend(settings.hermes_url, settings.hermes_key.get_secret_value())
    print_task = task_printer()
    service = DelegationService(
        repo,
        backend,
        run_timeout=args.timeout or settings.delegation.run_timeout,
        on_change=print_task,
    )
    try:
        if args.approve:
            try:
                await service.approve(args.key, args.approve)
            except TaskNotFound:
                print(f"voca bench: нет задачи {args.key}", file=sys.stderr)
                return 2
            except ApprovalNotActive:
                print("voca bench: запрос подтверждения не активен", file=sys.stderr)
                return 1
            except DelegationError as error:
                print(f"voca bench: {error}", file=sys.stderr)
                return 1
            print(f"approval: {args.approve} отправлено")
            return 0
        if args.recover:
            code = 0
            for task in await service.recover():
                print_task(task)
                if print_outcome(task):
                    code = 1
                if task.tool_call_id.startswith("cli-"):
                    await service.mark_delivered(task.tool_call_id)
            return code
        key = args.key or f"cli-{uuid.uuid4().hex}"
        print(f"key: {key}", flush=True)
        try:
            task = await service.delegate(key, args.text)
        except Unavailable as error:
            print(f"voca bench: делегирование недоступно ({error})", file=sys.stderr)
            return 1
        if task.is_open:
            task = await service.watch(key)
        print_task(task)
        if key.startswith("cli-") and task.delivered_at is None and not task.is_open:
            await service.mark_delivered(key)
        return print_outcome(task)
    finally:
        await backend.aclose()
        repo.close()


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
    bench = commands.add_parser("bench", help="stand tools without the voice")
    bench_commands = bench.add_subparsers(dest="bench_command", required=True)
    delegate = bench_commands.add_parser("delegate")
    delegate.add_argument("text", nargs="?")
    delegate.add_argument("--key")
    delegate.add_argument("--recover", action="store_true")
    delegate.add_argument("--timeout", type=float)
    delegate.add_argument("--approve", choices=["once", "deny"])
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
    if args.command == "bench":
        try:
            settings = Settings()
        except ValueError as error:
            print(
                f"voca bench: конфиг не прочитан ({config_path()}): {error}",
                file=sys.stderr,
            )
            sys.exit(2)
        approving = bool(args.approve and args.key) and not (args.text or args.recover)
        recovering = args.recover and not (args.text or args.key or args.approve)
        sending = bool(args.text) and not (args.recover or args.approve)
        if not (approving or recovering or sending):
            print(
                "voca bench delegate: нужен текст, --recover или --approve с --key",
                file=sys.stderr,
            )
            sys.exit(2)
        if not settings.hermes_url or settings.hermes_key is None:
            print(
                "voca bench: VOCA_HERMES_URL и VOCA_HERMES_KEY не заданы "
                "(set -a; . ./.env; set +a)",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            code = asyncio.run(bench_delegate(settings, args))
        except KeyboardInterrupt:
            print(
                "voca bench: прервано; довести задачи — voca bench delegate --recover",
                file=sys.stderr,
            )
            sys.exit(130)
        sys.exit(code)
    settings = Settings()
    try:
        app = create_app(settings, default_dist_dir())
    except FileNotFoundError as error:
        print(f"voca web: {error}", file=sys.stderr)
        sys.exit(2)
    web.run_app(app, host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    main()
