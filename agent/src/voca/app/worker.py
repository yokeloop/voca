import logging
import math
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from livekit import rtc
from livekit.agents import JobContext, JobProcess, WorkerOptions, cli, llm
from livekit.agents.voice import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    FunctionToolsExecutedEvent,
    room_io,
)
from livekit.plugins import silero

from voca.app.wiring import (
    build_session,
    check_context,
    compaction_limits,
    make_summarizer,
)
from voca.clientio.cards import CardState
from voca.clientio.history import HistoryChannel, send_on_wait
from voca.clientio.metrics import bind_metrics
from voca.config import Settings, config_path
from voca.conversation.agent import VocaAgent, records
from voca.conversation.instructions import InstructionError, InstructionLoader
from voca.delegation.hermes import HermesBackend
from voca.delegation.service import DelegationService
from voca.delegation.sqlite import SqliteTaskRepository, TaskStoreError
from voca.session.model import ArchiveRow
from voca.session.store import SessionRecorder, SessionStore, StoreError
from voca.speech.stress import load_accent
from voca.telemetry.jsonl import JsonlLog, TurnTracker


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(
        min_silence_duration=Settings().agent.vad_min_silence
    )
    proc.userdata["stress"] = load_accent()


async def entrypoint(ctx: JobContext) -> None:
    settings = Settings()
    loader = InstructionLoader(settings.instructions_dir)
    instructions = loader.load()
    await ctx.connect()
    log = JsonlLog(settings.state_dir / "events.jsonl", session=ctx.job.id)
    log.write(
        "instructions",
        sha256=instructions.sha256,
        source=instructions.source,
        error=None,
    )
    await check_context(settings, log)
    store = SessionStore.open(settings.session.path)
    current = store.open_session()
    tasks: SqliteTaskRepository | None = None
    try:
        tasks = SqliteTaskRepository.open(settings.delegation.path)
    except (TaskStoreError, sqlite3.Error, OSError):
        logging.getLogger("voca.tasks").exception(
            "task store not opened: %s", settings.delegation.path
        )
    backend: HermesBackend | None = None
    delegation: DelegationService | None = None
    if tasks is not None and settings.hermes_url and settings.hermes_key is not None:
        backend = HermesBackend(
            settings.hermes_url, settings.hermes_key.get_secret_value()
        )
        delegation = DelegationService(
            tasks, backend, run_timeout=settings.delegation.run_timeout
        )
    elif tasks is not None:
        logging.getLogger("voca.tasks").warning(
            "delegation off: VOCA_HERMES_URL or VOCA_HERMES_KEY not set"
        )
    restored = llm.ChatContext.from_dict(current.context)
    log.write(
        "session_restored",
        session_id=current.id,
        items=len(restored.items),
        estimate=sum(item.tokens for item in records(restored)),
    )
    cards = CardState(instructions)
    cards.bind(ctx.room)
    if tasks is not None:
        cards.bind_tasks(tasks)
    history = HistoryChannel(store, current.id, settings.session.history_on_join)
    history.bind(ctx.room)
    bind_metrics(ctx.room, log.write)
    summarizer = make_summarizer(settings)
    agent = VocaAgent(
        loader=loader,
        instructions=instructions,
        log=log,
        cards=cards,
        ctx_high=settings.trim_at_tokens(),
        ctx_low=settings.trim_to_tokens(),
        chat_ctx=restored,
        limits=compaction_limits(settings),
        summarizer=summarizer,
        compacted_at=current.compacted_at
        if current.compacted_at is not None
        else current.started_at,
        delegation=delegation,
    )
    if delegation is not None:
        delegation.on_change = agent.on_task_change
    recorder = SessionRecorder(store, current.id)

    def save() -> None:
        try:
            recorder.flush(
                agent.chat_ctx.to_dict(
                    exclude_function_call=False,
                    exclude_timestamp=False,
                    exclude_config_update=True,
                ),
            )
        except sqlite3.Error as error:
            log.write("session_write_failed", error=str(error))

    def on_compacted(
        item_id: str, at: float, body: str, data: dict[str, object]
    ) -> None:
        recorder.add_row(
            ArchiveRow(
                kind="summary",
                item_id=item_id,
                at=at,
                call_id=None,
                name=None,
                text=body,
                data=dict(data),
            ),
            compacted_at=time.time(),
        )
        save()

    agent.on_compacted = on_compacted

    tracker = TurnTracker(log, on_turn=save)
    executor = ThreadPoolExecutor(max_workers=1)
    session = build_session(
        settings,
        vad=ctx.proc.userdata["vad"],
        stress=ctx.proc.userdata["stress"],
        executor=executor,
        on_prepared=tracker.on_prepared,
    )

    def on_item(ev: ConversationItemAddedEvent) -> None:
        if isinstance(ev.item, llm.ChatMessage):
            recorder.add(ev.item.model_dump(mode="json", exclude_none=True))

    def on_tools(ev: FunctionToolsExecutedEvent) -> None:
        for call, output in ev.zipped():
            recorder.add(call.model_dump(mode="json", exclude_none=True))
            recorder.add(output.model_dump(mode="json", exclude_none=True))

    session.on("conversation_item_added", on_item)
    session.on("function_tools_executed", on_tools)

    def on_state(ev: AgentStateChangedEvent) -> None:
        if ev.new_state == "listening":
            agent.maybe_compact()

    session.on("agent_state_changed", on_state)
    tracker.attach(session)
    session.on("metrics_collected", cards.on_metrics)

    def on_error(ev: ErrorEvent) -> None:
        component = cards.on_error(ev)
        if component is not None:
            agent.on_component_error(component)

    session.on("error", on_error)

    async def shutdown() -> None:
        await agent.stop_delegation()
        await session.aclose()
        agent.cancel_compaction()
        await summarizer.close()
        save()
        if backend is not None:
            await backend.aclose()
        if tasks is not None:
            tasks.close()
        store.close()
        executor.shutdown(wait=False)
        log.close()

    ctx.add_shutdown_callback(shutdown)
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(close_on_disconnect=False),
        record=False,
    )

    def users_present(leaving: str | None = None) -> bool:
        return any(
            identity.startswith("user-") and identity != leaving
            for identity in ctx.room.remote_participants
        )

    def on_participant(participant: rtc.RemoteParticipant) -> None:
        if participant.identity.startswith("user-"):
            log.device = participant.identity.removeprefix("user-")
            agent.set_present(True)
            agent.check_due()

    def on_left(participant: rtc.RemoteParticipant) -> None:
        if participant.identity.startswith("user-") and not users_present(
            participant.identity
        ):
            agent.set_present(False)

    ctx.room.on("participant_connected", on_participant)
    ctx.room.on("participant_disconnected", on_left)
    participant = await send_on_wait(ctx, history)
    log.device = participant.identity.removeprefix("user-")
    cards.publish()
    cards.send_tasks(participant.identity)
    agent.set_present(users_present())
    agent.start_delivery(session, history.sent)
    await agent.enqueue_undelivered()
    agent.start_recover()
    agent.check_due()


def server_options(settings: Settings) -> WorkerOptions:
    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
        ws_url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        host="127.0.0.1",
        port=settings.agent_port,
        num_idle_processes=1,
        load_threshold=math.inf,
        initialize_process_timeout=60.0,
        drain_timeout=5,
    )


def run(mode: str, rest: list[str]) -> None:
    try:
        settings = Settings()
    except ValueError as error:
        print(
            f"voca agent: конфиг не прочитан ({config_path()}): {error}; "
            "образец — config.example.toml, путь — VOCA_CONFIG",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        InstructionLoader(settings.instructions_dir).load()
    except InstructionError as error:
        print(
            f"voca agent: инструкции не загружены: {error}; "
            "образец — instructions.example/, каталог — VOCA_INSTRUCTIONS_DIR",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        SessionStore.open(settings.session.path).close()
    except (StoreError, sqlite3.Error, OSError) as error:
        print(
            f"voca agent: хранилище сессии не открыто ({settings.session.path}): {error}",
            file=sys.stderr,
        )
        sys.exit(1)
    os.environ.setdefault("LIVEKIT_AGENTS_PRELOAD_LOCAL_INFERENCE", "0")
    sys.argv = [sys.argv[0], mode, *rest]
    cli.run_app(server_options(settings))
