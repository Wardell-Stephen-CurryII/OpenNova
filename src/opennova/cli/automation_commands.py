"""Shared automation slash-command handling."""

from __future__ import annotations

import shlex
from collections.abc import Callable

from opennova.automation import LocalAutomationScheduler, ScheduledTask
from opennova.tools.base import ToolResult


def handle_automation_command(
    scheduler: LocalAutomationScheduler,
    args: str,
    runner: Callable[[ScheduledTask], object] | None = None,
) -> ToolResult:
    """Handle `/automations` subcommands."""
    runner = runner or (lambda task: task.prompt)
    tokens = shlex.split(args or "list")
    subcommand = tokens[0] if tokens else "list"

    try:
        if subcommand == "list":
            tasks = scheduler.list_tasks()
            output = "\n".join(
                f"{task.id[:8]} {task.name} enabled={task.enabled} next={task.next_run_at}"
                for task in tasks
            ) or "No local automations scheduled."
            return ToolResult(success=True, output=output, metadata={"tasks": tasks})

        if subcommand == "once" and len(tokens) >= 4:
            name = tokens[1]
            run_at = float(tokens[2])
            prompt = " ".join(tokens[3:])
            task_id = scheduler.schedule_once(name=name, prompt=prompt, run_at=run_at)
            return ToolResult(
                success=True,
                output=f"Scheduled once: {name} ({task_id})",
                metadata={"task_id": task_id},
            )

        if subcommand == "interval" and len(tokens) >= 4:
            name = tokens[1]
            interval = float(tokens[2])
            prompt = " ".join(tokens[3:])
            task_id = scheduler.schedule_interval(name=name, prompt=prompt, interval_seconds=interval)
            return ToolResult(
                success=True,
                output=f"Scheduled interval: {name} ({task_id})",
                metadata={"task_id": task_id},
            )

        if subcommand in {"pause", "resume", "delete", "run-now"} and len(tokens) == 2:
            task_id = tokens[1]
            if subcommand == "pause":
                scheduler.pause(task_id)
                return ToolResult(success=True, output=f"Paused automation: {task_id}")
            if subcommand == "resume":
                scheduler.resume(task_id)
                return ToolResult(success=True, output=f"Resumed automation: {task_id}")
            if subcommand == "delete":
                scheduler.delete(task_id)
                return ToolResult(success=True, output=f"Deleted automation: {task_id}")
            run = scheduler.run_now(task_id, runner=runner)
            return ToolResult(success=run.success, output=run.output or "", error=run.error, metadata={"run": run})
    except Exception as exc:
        return ToolResult(success=False, output="", error=str(exc))

    return ToolResult(
        success=False,
        output="",
        error=(
            "Usage: /automations [list|once <name> <run_at> <prompt>|"
            "interval <name> <seconds> <prompt>|pause <id>|resume <id>|delete <id>|run-now <id>]"
        ),
    )
