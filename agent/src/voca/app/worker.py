import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from livekit import rtc
from livekit.agents import JobContext, JobProcess, WorkerOptions, cli
from livekit.agents.voice import ErrorEvent, room_io
from livekit.plugins import silero

from voca.app.wiring import build_session
from voca.clientio.cards import CardState
from voca.config import Settings
from voca.conversation.agent import VocaAgent
from voca.conversation.instructions import InstructionError, InstructionLoader
from voca.speech.stress import load_accent
from voca.telemetry.jsonl import JsonlLog, TurnTracker


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(
        min_silence_duration=Settings().vad_min_silence
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
    cards = CardState(instructions)
    cards.bind(ctx.room)
    agent = VocaAgent(
        loader=loader,
        instructions=instructions,
        log=log,
        cards=cards,
        ctx_high=settings.ctx_high_tokens(),
        ctx_low=settings.ctx_low_tokens(),
    )
    tracker = TurnTracker(log)
    executor = ThreadPoolExecutor(max_workers=1)
    session = build_session(
        settings,
        vad=ctx.proc.userdata["vad"],
        stress=ctx.proc.userdata["stress"],
        executor=executor,
        on_prepared=tracker.on_prepared,
    )
    tracker.attach(session)
    session.on("metrics_collected", cards.on_metrics)

    def on_error(ev: ErrorEvent) -> None:
        component = cards.on_error(ev)
        if component is not None:
            agent.on_component_error(component)

    session.on("error", on_error)

    async def shutdown() -> None:
        executor.shutdown(wait=False)
        log.close()

    ctx.add_shutdown_callback(shutdown)
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(close_on_disconnect=False),
        record=False,
    )

    def on_participant(participant: rtc.RemoteParticipant) -> None:
        if participant.identity.startswith("user-"):
            log.device = participant.identity.removeprefix("user-")

    ctx.room.on("participant_connected", on_participant)
    participant = await ctx.wait_for_participant()
    log.device = participant.identity.removeprefix("user-")
    cards.publish()


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
    settings = Settings()
    try:
        InstructionLoader(settings.instructions_dir).load()
    except InstructionError as error:
        print(
            f"voca agent: инструкции не загружены: {error}; "
            "образец — instructions.example/, каталог — VOCA_INSTRUCTIONS_DIR",
            file=sys.stderr,
        )
        sys.exit(1)
    os.environ.setdefault("LIVEKIT_AGENTS_PRELOAD_LOCAL_INFERENCE", "0")
    sys.argv = [sys.argv[0], mode, *rest]
    cli.run_app(server_options(settings))
