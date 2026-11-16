import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Literal

from voca.delegation.model import (
    LOST,
    LOST_BEFORE_LAUNCH,
    TERMINAL,
    TIMEOUT,
    UNAVAILABLE,
    Task,
    TaskStatus,
)
from voca.delegation.ports import (
    ApprovalNotActive,
    DelegationBackend,
    DelegationError,
    OutcomeUnknown,
    Rejected,
    RunNotFound,
    RunState,
    TaskNotFound,
    TaskRepository,
    Unavailable,
)

POLL_S = 2.0
STOP_WINDOW_S = 30.0


class DelegationService:
    def __init__(
        self,
        repo: TaskRepository,
        backend: DelegationBackend,
        *,
        run_timeout: float,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_change: Callable[[Task], None] | None = None,
    ) -> None:
        self.repo = repo
        self.backend = backend
        self.run_timeout = run_timeout
        self.clock = clock
        self.sleep = sleep
        self.on_change = on_change

    async def _save(self, task: Task) -> Task:
        await self.repo.save(task)
        if self.on_change is not None:
            self.on_change(task)
        return task

    def _expired(self, task: Task) -> bool:
        return self.clock() - task.created_at > self.run_timeout

    async def _get(self, key: str) -> Task:
        task = await self.repo.get(key)
        if task is None:
            raise TaskNotFound(key)
        return task

    async def delegate(self, key: str, text: str) -> Task:
        task, created = await self.repo.insert(
            Task(key, text, TaskStatus.QUEUED, created_at=self.clock())
        )
        if not created:
            return task
        if self.on_change is not None:
            self.on_change(task)
        try:
            run_id = await self.backend.create_run(key, text)
        except Rejected as error:
            await self._save(task.transition(TaskStatus.FAILED, error=UNAVAILABLE))
            raise Unavailable(error.reason) from error
        except OutcomeUnknown:
            try:
                run_id = await self.backend.create_run(key, text)
            except DelegationError as error:
                await self._save(replace(task, error=UNAVAILABLE))
                raise Unavailable(str(error)) from error
        return await self._save(task.transition(TaskStatus.RUNNING, run_id=run_id))

    async def watch(self, key: str) -> Task:
        while True:
            task = await self._get(key)
            if not task.is_open or task.status == TaskStatus.QUEUED:
                return task
            if self._expired(task):
                return await self._expire(task)
            run_id = task.run_id
            assert run_id is not None
            try:
                state = await self.backend.get_run(
                    run_id, stop_requested=task.stop_requested_at is not None
                )
            except RunNotFound:
                return await self._save(task.transition(TaskStatus.FAILED, error=LOST))
            except DelegationError:
                await self.sleep(POLL_S)
                continue
            task = await self._apply(task, state)
            if state.status == TaskStatus.UNKNOWN and state.admission:
                try:
                    await self.backend.resolve_unknown(run_id, state.admission)
                except DelegationError:
                    pass
            if not task.is_open:
                return task
            await self.sleep(POLL_S)

    async def _apply(self, task: Task, state: RunState) -> Task:
        approval = (
            state.approval if state.status == TaskStatus.WAITING_APPROVAL else None
        )
        if state.status == task.status and approval == task.approval:
            return task
        if state.status == task.status:
            return await self._save(replace(task, approval=approval))
        return await self._save(
            task.transition(
                state.status,
                result=state.result,
                error=state.error,
                approval=approval,
            )
        )

    async def _expire(self, task: Task) -> Task:
        run_id = task.run_id
        assert run_id is not None
        task = await self._save(replace(task, stop_requested_at=self.clock()))
        try:
            state = await self.backend.stop(run_id)
        except DelegationError:
            state = None
        if state is not None and state.status in TERMINAL:
            return await self._apply(task, state)
        deadline = self.clock() + STOP_WINDOW_S
        while self.clock() < deadline:
            await self.sleep(POLL_S)
            try:
                state = await self.backend.get_run(run_id, stop_requested=True)
            except RunNotFound:
                return await self._save(task.transition(TaskStatus.FAILED, error=LOST))
            except DelegationError:
                continue
            if state.status in TERMINAL:
                return await self._apply(task, state)
        return await self._save(task.transition(TaskStatus.FAILED, error=TIMEOUT))

    async def _relaunch_refused(self, task: Task) -> None:
        try:
            run_id = await self.backend.create_run(task.tool_call_id, task.text)
        except Rejected:
            await self._save(task.transition(TaskStatus.FAILED, error=UNAVAILABLE))
            return
        except OutcomeUnknown:
            if self._expired(task):
                await self._save(task.transition(TaskStatus.FAILED, error=UNAVAILABLE))
            return
        try:
            await self.backend.stop(run_id)
        except DelegationError:
            pass
        await self._save(
            task.transition(TaskStatus.FAILED, run_id=run_id, error=UNAVAILABLE)
        )

    async def _launch(self, task: Task) -> bool:
        if self._expired(task):
            await self._save(
                task.transition(TaskStatus.FAILED, error=LOST_BEFORE_LAUNCH)
            )
            return False
        try:
            run_id = await self.backend.create_run(task.tool_call_id, task.text)
        except Rejected:
            await self._save(task.transition(TaskStatus.FAILED, error=UNAVAILABLE))
            return False
        except OutcomeUnknown:
            return False
        await self._save(task.transition(TaskStatus.RUNNING, run_id=run_id))
        return True

    async def recover(self) -> list[Task]:
        watched: list[str] = []
        for task in await self.repo.list_open():
            if task.status != TaskStatus.QUEUED:
                watched.append(task.tool_call_id)
            elif task.error == UNAVAILABLE:
                await self._relaunch_refused(task)
            elif await self._launch(task):
                watched.append(task.tool_call_id)
        await asyncio.gather(*(self.watch(key) for key in watched))
        return await self.repo.list_undelivered()

    async def approve(self, key: str, choice: Literal["once", "deny"]) -> None:
        task = await self._get(key)
        if task.status != TaskStatus.WAITING_APPROVAL or task.approval is None:
            raise ApprovalNotActive(key)
        assert task.run_id is not None
        await self.backend.approve(task.run_id, choice, task.approval)

    async def mark_delivered(self, key: str) -> Task:
        task = await self._get(key)
        return await self._save(replace(task, delivered_at=self.clock()))
