"""Local automation scheduler foundation."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ScheduledTask:
    """A local scheduled automation task."""

    id: str
    name: str
    prompt: str
    next_run_at: float
    interval_seconds: float | None = None
    enabled: bool = True


@dataclass
class ScheduledRun:
    """Recorded execution result for a scheduled task."""

    task_id: str
    task_name: str
    ran_at: float
    success: bool
    output: str = ""
    error: str | None = None


class LocalAutomationScheduler:
    """Persisted local scheduler for one-shot and interval tasks."""

    def __init__(self, storage_path: str | Path, clock: Callable[[], float] = time.time):
        self.storage_path = Path(storage_path)
        self.clock = clock
        self.tasks: dict[str, ScheduledTask] = {}
        self.history: list[ScheduledRun] = []
        self._load()

    def schedule_once(self, name: str, prompt: str, run_at: float) -> str:
        task = ScheduledTask(
            id=str(uuid.uuid4()),
            name=name,
            prompt=prompt,
            next_run_at=run_at,
        )
        self.tasks[task.id] = task
        self.save()
        return task.id

    def schedule_interval(
        self,
        name: str,
        prompt: str,
        interval_seconds: float,
        start_at: float | None = None,
    ) -> str:
        now = self.clock()
        task = ScheduledTask(
            id=str(uuid.uuid4()),
            name=name,
            prompt=prompt,
            next_run_at=start_at if start_at is not None else now,
            interval_seconds=interval_seconds,
        )
        self.tasks[task.id] = task
        self.save()
        return task.id

    def get(self, task_id: str) -> ScheduledTask:
        return self.tasks[task_id]

    def list_tasks(self) -> list[ScheduledTask]:
        return sorted(self.tasks.values(), key=lambda task: task.next_run_at)

    def pause(self, task_id: str) -> None:
        self.tasks[task_id].enabled = False
        self.save()

    def resume(self, task_id: str) -> None:
        self.tasks[task_id].enabled = True
        self.save()

    def delete(self, task_id: str) -> None:
        self.tasks.pop(task_id)
        self.save()

    def due_tasks(self) -> list[ScheduledTask]:
        now = self.clock()
        return [
            task
            for task in self.tasks.values()
            if task.enabled and task.next_run_at <= now
        ]

    def run_now(self, task_id: str, runner: Callable[[ScheduledTask], object]) -> ScheduledRun:
        task = self.tasks[task_id]
        run = self._run_task(task, runner)
        if task.interval_seconds and task.enabled:
            task.next_run_at = self.clock() + task.interval_seconds
        else:
            task.enabled = False
        self.save()
        return run

    def run_due(self, runner: Callable[[ScheduledTask], object]) -> list[str]:
        ran: list[str] = []
        now = self.clock()
        for task in self.due_tasks():
            self._run_task(task, runner)
            ran.append(task.id)
            if task.interval_seconds:
                task.next_run_at = now + task.interval_seconds
            else:
                task.enabled = False
        if ran:
            self.save()
        return ran

    def _run_task(self, task: ScheduledTask, runner: Callable[[ScheduledTask], object]) -> ScheduledRun:
        try:
            output = runner(task)
            run = ScheduledRun(
                task_id=task.id,
                task_name=task.name,
                ran_at=self.clock(),
                success=True,
                output="" if output is None else str(output),
            )
        except Exception as exc:
            run = ScheduledRun(
                task_id=task.id,
                task_name=task.name,
                ran_at=self.clock(),
                success=False,
                error=str(exc),
            )
        self.history.append(run)
        return run

    def save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tasks": [asdict(task) for task in self.tasks.values()],
            "history": [asdict(run) for run in self.history],
        }
        self.storage_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for task_data in payload.get("tasks", []):
            task = ScheduledTask(**task_data)
            self.tasks[task.id] = task
        for run_data in payload.get("history", []):
            self.history.append(ScheduledRun(**run_data))
