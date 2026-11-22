import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any

from livekit import rtc
from livekit.agents.metrics import LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import ErrorEvent, MetricsCollectedEvent

from voca.conversation.instructions import InstructionSet
from voca.delegation.model import Task
from voca.delegation.ports import TaskRepository

TOPIC = "voca.cards"
COMPONENTS = ("stt", "llm", "tts")


def component_of(error: object) -> str | None:
    kind = getattr(error, "type", None)
    if not isinstance(kind, str):
        return None
    component = kind.removesuffix("_error")
    return component if component in COMPONENTS else None


def task_item(task: Task) -> dict[str, Any]:
    a = task.approval
    approval = (
        None
        if a is None
        else {
            "description": a.get("description"),
            "command": a.get("command"),
            "choices": a.get("choices"),
            "request_id": a.get("request_id"),
        }
    )
    return {
        "tool_call_id": task.tool_call_id,
        "status": task.status.value,
        "text": task.text,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at,
        "delivered_at": task.delivered_at,
        "approval": approval,
    }


def tasks_message(tasks: Sequence[Task]) -> str:
    return json.dumps(
        {"type": "tasks", "tasks": [task_item(t) for t in tasks]},
        ensure_ascii=False,
    )


class CardState:
    def __init__(self, instructions: InstructionSet) -> None:
        self.components = {"stt": "ok", "llm": "ok", "tts": "ok"}
        self.instructions = instructions
        self.room: rtc.Room | None = None
        self.repo: TaskRepository | None = None
        self.sending: asyncio.Task[None] | None = None

    def message(self) -> str:
        return json.dumps(
            {
                "type": "state",
                "instructions_sha256": self.instructions.sha256,
                "instructions_source": self.instructions.source,
                "instructions_error": self.instructions.error,
                "components": {name: self.components[name] for name in COMPONENTS},
            },
            ensure_ascii=False,
        )

    def set_instructions(self, instructions: InstructionSet) -> None:
        current = self.instructions
        if (instructions.sha256, instructions.source, instructions.error) != (
            current.sha256,
            current.source,
            current.error,
        ):
            self.instructions = instructions
            self.publish()

    def set_component(self, component: str, value: str) -> None:
        if self.components[component] != value:
            self.components[component] = value
            self.publish()

    def on_error(self, ev: ErrorEvent) -> str | None:
        if getattr(ev.error, "recoverable", False):
            return None
        component = component_of(ev.error)
        if component is not None:
            self.set_component(component, "down")
        return component

    def on_metrics(self, ev: MetricsCollectedEvent) -> None:
        metrics = ev.metrics
        if isinstance(metrics, STTMetrics):
            self.set_component("stt", "ok")
        elif isinstance(metrics, LLMMetrics):
            self.set_component("llm", "ok")
        elif isinstance(metrics, TTSMetrics):
            self.set_component("tts", "ok")

    def bind(self, room: rtc.Room) -> None:
        self.room = room
        room.on("participant_connected", self.on_participant)

    def bind_tasks(self, repo: TaskRepository) -> None:
        self.repo = repo

    def on_participant(self, participant: rtc.RemoteParticipant) -> None:
        self.publish()
        self.send_tasks(participant.identity)

    def publish(self) -> None:
        if self.room is None:
            return
        self.sending = asyncio.create_task(self._send(self.message(), self.sending))

    def publish_task(self, task: Task) -> None:
        if self.room is None:
            return
        self.sending = asyncio.create_task(
            self._send(
                json.dumps({"type": "task", **task_item(task)}, ensure_ascii=False),
                self.sending,
            )
        )

    def send_tasks(self, identity: str) -> None:
        if self.room is None or self.repo is None:
            return
        self.sending = asyncio.create_task(
            self._send_tasks(self.repo, identity, self.sending)
        )

    async def _send_tasks(
        self, repo: TaskRepository, identity: str, previous: asyncio.Task[None] | None
    ) -> None:
        if previous is not None:
            await previous
        try:
            assert self.room is not None
            await self.room.local_participant.send_text(
                tasks_message(await repo.list_all()),
                topic=TOPIC,
                destination_identities=[identity],
            )
        except Exception:
            logging.getLogger("voca.cards").exception("send tasks failed")

    async def _send(self, text: str, previous: asyncio.Task[None] | None) -> None:
        if previous is not None:
            await previous
        try:
            assert self.room is not None
            await self.room.local_participant.send_text(text, topic=TOPIC)
        except Exception:
            logging.getLogger("voca.cards").exception("send state failed")
