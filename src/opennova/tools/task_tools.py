"""
Task Management Tools - Claude Code-style task management tools.

Provides:
- TaskCreate: Create structured tasks
- TaskList: List all tasks
- TaskGet: Get task details
- TaskUpdate: Update task status/dependencies
- TaskStop: Stop a running task
- TaskOutput: Get task output
"""

import asyncio
from typing import Any

from opennova.tasks import Task, TaskManager, TaskStatus, TaskType
from opennova.tools.base import BaseTool, ToolResult

# Global task manager instance (will be set by AgentRuntime)
_global_task_manager: TaskManager | None = None


def set_global_task_manager(manager: TaskManager) -> None:
    """Set the global task manager instance."""
    global _global_task_manager
    _global_task_manager = manager


def get_global_task_manager() -> TaskManager:
    """Get the global task manager instance."""
    if _global_task_manager is None:
        # Create a fallback if not set
        set_global_task_manager(TaskManager())
    return _global_task_manager


def _format_dependency_details(manager: TaskManager, task: Task) -> list[str]:
    """Build human-readable dependency details for task output."""
    details: list[str] = []

    if task.blocked_by:
        open_blockers = manager.get_open_blocker_ids(task.id)
        blocker_text = ", ".join(task.blocked_by)
        if open_blockers:
            details.append(f"blocked_by: {blocker_text} (open: {', '.join(open_blockers)})")
        else:
            details.append(f"blocked_by: {blocker_text} (open: none)")

    if task.blocks:
        details.append(f"blocks: {', '.join(task.blocks)}")

    if task.blocked_by:
        details.append(f"is_blocked: {manager.is_task_blocked(task.id)}")

    return details


class TaskCreateTool(BaseTool):
    """Create a new structured task for tracking work."""

    name = "task_create"
    description = "Create a new structured task for tracking work progress. Use this when you need to track a multi-step task, coordinate work with other tools, or want to organize complex work into smaller trackable units."

    def execute(
        self,
        subject: str,
        description: str,
        active_form: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """
        Create a new task.

        Args:
            subject: Brief, actionable title in imperative form (e.g., "Fix authentication bug")
            description: What needs to be done
            active_form: Present continuous form shown in spinner (e.g., "Fixing authentication bug")
            metadata: Optional additional metadata

        Returns:
            ToolResult with task ID
        """
        try:
            manager = get_global_task_manager()
            full_description = f"{subject}: {description}"
            task_type = TaskType.LOCAL_WORKFLOW

            task = manager.create_task(
                task_type=task_type,
                description=full_description,
                metadata={
                    "subject": subject,
                    "active_form": active_form,
                    **(metadata or {}),
                },
            )

            return ToolResult(
                success=True,
                output=f"Created task {task.id}: {subject}",
                metadata={"task_id": task.id, "task": task.to_dict()},
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class TaskListTool(BaseTool):
    """List all tasks in the task list."""

    name = "task_list"
    description = "List all tasks in the task list. Use this to see all available tasks, their status, and which tasks you can work on next."

    def execute(self, **kwargs: Any) -> ToolResult:
        """
        List all tasks.

        Returns:
            ToolResult with task list
        """
        try:
            manager = get_global_task_manager()
            tasks = manager.get_all_tasks()

            if not tasks:
                return ToolResult(success=True, output="No tasks in the task list.")

            output_lines = ["Tasks:"]
            for task in tasks:
                status_icon = {"pending": "○", "running": "⟳", "completed": "✓", "failed": "✗", "killed": "⊘"}.get(
                    task.status.value, "?"
                )
                owner = task.metadata.get("owner", "")
                dependency_details = _format_dependency_details(manager, task)

                output_lines.append(
                    f"  [{task.id}] {status_icon} {task.description[:60]}{'...' if len(task.description) > 60 else ''}"
                )
                if owner:
                    output_lines.append(f"      owner: {owner}")
                for detail in dependency_details:
                    output_lines.append(f"      {detail}")

            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={"tasks": [task.to_dict() for task in tasks]},
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class TaskGetTool(BaseTool):
    """Get a task by its ID."""

    name = "task_get"
    description = "Get a task by its ID from the task list. Use this to see the full description, status, dependencies, and context of a specific task before working on it."

    def execute(self, task_id: str, **kwargs: Any) -> ToolResult:
        """
        Get a task by ID.

        Args:
            task_id: The ID of the task to retrieve

        Returns:
            ToolResult with task details
        """
        try:
            manager = get_global_task_manager()
            task = manager.get_task(task_id)

            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task '{task_id}' not found",
                )

            output_lines = [
                f"Task: {task.id}",
                f"Description: {task.description}",
                f"Status: {task.status.value}",
                f"Type: {task.type.value}",
            ]

            dependency_details = _format_dependency_details(manager, task)
            if dependency_details:
                output_lines.append("")
                output_lines.append("Dependencies:")
                for detail in dependency_details:
                    output_lines.append(f"  {detail}")

            if task.start_time:
                output_lines.append(f"Started: {task.start_time.isoformat()}")
            if task.end_time:
                output_lines.append(f"Ended: {task.end_time.isoformat()}")

            if task.metadata:
                output_lines.append("\nMetadata:")
                for key, value in task.metadata.items():
                    if key != "description":
                        output_lines.append(f"  {key}: {value}")

            if task.usage and task.usage.total_tokens > 0:
                output_lines.append(
                    f"\nUsage: {task.usage.total_tokens} tokens, {task.usage.tool_uses} tool uses, {task.usage.duration_ms}ms"
                )

            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={"task": task.to_dict()},
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class TaskUpdateTool(BaseTool):
    """Update a task in the task list."""

    name = "task_update"
    description = "Update a task in the task list. Mark tasks as resolved when you complete work on them. Only mark a task as completed when you have FULLY accomplished it."

    def execute(
        self,
        task_id: str,
        status: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
        add_blocks: list[str] | None = None,
        add_blocked_by: list[str] | None = None,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """
        Update a task.

        Args:
            task_id: The ID of the task to update
            status: New status (pending, running, completed, failed, killed)
            subject: New brief title
            description: New detailed description
            active_form: New active form for spinner
            add_blocks: Tasks that this task blocks
            add_blocked_by: Tasks that must complete before this task
            owner: New task owner (agent name)
            metadata: Additional metadata to merge

        Returns:
            ToolResult with updated task info
        """
        try:
            manager = get_global_task_manager()
            task = manager.get_task(task_id)

            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task '{task_id}' not found",
                )

            if status:
                try:
                    task_status = TaskStatus(status)
                    manager.update_task_status(task_id, task_status)
                except ValueError:
                    return ToolResult(
                        success=False,
                        output="",
                        error=f"Invalid status: {status}. Must be one of: pending, running, completed, failed, killed",
                    )

            dependency_targets = []
            for dependent_task_id in add_blocks or []:
                success, error = manager.add_dependency(task_id, dependent_task_id)
                if not success:
                    return ToolResult(success=False, output="", error=error)
                dependency_targets.append(dependent_task_id)

            for prerequisite_task_id in add_blocked_by or []:
                success, error = manager.add_dependency(prerequisite_task_id, task_id)
                if not success:
                    return ToolResult(success=False, output="", error=error)
                dependency_targets.append(prerequisite_task_id)

            if subject:
                task.metadata["subject"] = subject
            if active_form:
                task.metadata["active_form"] = active_form
            if owner:
                task.metadata["owner"] = owner

            if description:
                subject_part = task.metadata.get("subject", "")
                task.description = f"{subject_part}: {description}"
                task.metadata["description"] = description

            if metadata:
                for key, value in metadata.items():
                    if value is None:
                        task.metadata.pop(key, None)
                    else:
                        task.metadata[key] = value

            return ToolResult(
                success=True,
                output=f"Updated task {task_id}",
                metadata={
                    "task": task.to_dict(),
                    "updated_dependencies": dependency_targets,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class TaskStopTool(BaseTool):
    """Stop a running background task."""

    name = "task_stop"
    description = "Stop a running background task. Use this to terminate a task that is in the wrong direction or no longer needed. Pass the task_id from the tool's launch result."

    def execute(self, task_id: str, **kwargs: Any) -> ToolResult:
        """
        Stop a task.

        Args:
            task_id: The ID of the task to stop

        Returns:
            ToolResult indicating success/failure
        """
        try:
            manager = get_global_task_manager()
            stopped = asyncio.run(manager.stop_task(task_id))

            if stopped:
                return ToolResult(
                    success=True,
                    output=f"Stopped task {task_id}",
                )
            else:
                task = manager.get_task(task_id)
                if not task:
                    return ToolResult(
                        success=False,
                        output="",
                        error=f"Task '{task_id}' not found",
                    )
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task '{task_id}' is not running (status: {task.status.value})",
                )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class TaskOutputTool(BaseTool):
    """Get output from a running or completed background task."""

    name = "task_output"
    description = "Get output from a running or completed background task. Use this to retrieve the full output from tasks that were started in the background."

    def execute(self, task_id: str, max_length: int = 10000, **kwargs: Any) -> ToolResult:
        """
        Get task output.

        Args:
            task_id: The ID of the task
            max_length: Maximum bytes to read (default 10000)

        Returns:
            ToolResult with task output
        """
        try:
            from opennova.utils.task_output import read_task_output

            manager = get_global_task_manager()
            task = manager.get_task(task_id)

            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task '{task_id}' not found",
                )

            content, offset = read_task_output(task_id, max_length, task.output_offset)

            if not content:
                return ToolResult(
                    success=True,
                    output=f"Task {task_id} has no output yet.",
                )

            return ToolResult(
                success=True,
                output=content,
                metadata={
                    "task_id": task_id,
                    "offset": offset,
                    "has_more": offset < task.output_offset + max_length,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
