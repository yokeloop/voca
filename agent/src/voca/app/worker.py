import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from livekit import rtc
from livekit.agents import JobContext, JobProcess, WorkerOptions, cli
from livekit.agents.voice import room_io
from livekit.plugins import silero

from voca.app.wiring import build_session
from voca.config import Settings
from voca.conversation.agent import VocaAgent
from voca.speech.stress import load_accent
from voca.telemetry.jsonl import JsonlLog, TurnTracker


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(
        min_silence_duration=Settings().vad_min_silence
    )
    proc.userdata["stress"] = load_accent()


async def entrypoint(ctx: JobContext) -> None:
    settings = Settings()
    await ctx.connect()
    log = JsonlLog(settings.state_dir / "events.jsonl", session=ctx.job.id)
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

    async def shutdown() -> None:
        executor.shutdown(wait=False)
        log.close()

    ctx.add_shutdown_callback(shutdown)
    await session.start(
        agent=VocaAgent(),
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
    os.environ.setdefault("LIVEKIT_AGENTS_PRELOAD_LOCAL_INFERENCE", "0")
    sys.argv = [sys.argv[0], mode, *rest]
    cli.run_app(server_options(Settings()))
