"""
Task System - Core task management for OpenNova.

Implements Claude Code-style task management:
- Task types with ID prefixes (agent, bash, workflow, etc.)
- Task status tracking (pending, running, completed, failed, killed)
- Task output persistence to disk
- Progress tracking and notifications
"""

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from opennova.utils.task_output import get_task_output_path


class TaskType(str, Enum):
    """Types of tasks that can be executed."""

    LOCAL_BASH = "local_bash"
    LOCAL_AGENT = "local_agent"
    LOCAL_WORKFLOW = "local_workflow"
    MONITOR_MCP = "monitor_mcp"


class TaskStatus(str, Enum):
    """Status of a task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


def is_terminal_status(status: TaskStatus) -> bool:
    """Check if status is terminal (task will not transition further)."""
    return status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED)


# Task ID prefixes based on task type
TASK_ID_PREFIXES: dict[TaskType, str] = {
    TaskType.LOCAL_BASH: "b",
    TaskType.LOCAL_AGENT: "a",
    TaskType.LOCAL_WORKFLOW: "w",
    TaskType.MONITOR_MCP: "m",
}

# Case-insensitive alphabet for task IDs
TASK_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def generate_task_id(task_type: TaskType) -> str:
    """Generate a unique task ID with prefix and random suffix."""
    prefix = TASK_ID_PREFIXES.get(task_type, "x")
    random_bytes = secrets.token_bytes(8)
    suffix = "".join(TASK_ID_ALPHABET[b % len(TASK_ID_ALPHABET)] for b in random_bytes)
    return f"{prefix}{suffix}"


@dataclass
class TaskProgressData:
    """Progress data for a task."""

    last_activity: str | None = None
    token_count: int = 0
    tool_use_count: int = 0
    last_tool_name: str | None = None


@dataclass
class TaskUsage:
    """Usage statistics for a task."""

    total_tokens: int = 0
    tool_uses: int = 0
    duration_ms: int = 0


@dataclass
class Task:
    """
    A task represents an executable unit of work.

    Tasks can be shell commands, agent executions, workflows, or monitors.
    Each task has a unique ID, type, status, and persistent output file.
    """

    id: str
    type: TaskType
    description: str
    status: TaskStatus = TaskStatus.PENDING
    tool_use_id: str | None = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    output_file: str = field(init=False)
    output_offset: int = 0
    notified: bool = False
    progress: TaskProgressData = field(default_factory=TaskProgressData)
    usage: TaskUsage = field(default_factory=TaskUsage)
    messages: list[dict[str, Any]] = field(default_factory=list)
    session_state: dict[str, Any] = field(default_factory=dict)
    message_queue: list[dict[str, Any]] = field(default_factory=list)
    retain: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize output file path."""
        self.output_file = get_task_output_path(self.id)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "type": self.type.value,
            "description": self.description,
            "status": self.status.value,
            "tool_use_id": self.tool_use_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "output_file": self.output_file,
            "output_offset": self.output_offset,
            "notified": self.notified,
            "progress": {
                "last_activity": self.progress.last_activity,
                "token_count": self.progress.token_count,
                "tool_use_count": self.progress.tool_use_count,
                "last_tool_name": self.progress.last_tool_name,
            },
            "usage": {
                "total_tokens": self.usage.total_tokens,
                "tool_uses": self.usage.tool_uses,
                "duration_ms": self.usage.duration_ms,
            },
            "messages": self.messages,
            "session_state": self.session_state,
            "message_queue": self.message_queue,
            "retain": self.retain,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        """Create task from dictionary."""
        progress = TaskProgressData(
            last_activity=data.get("progress", {}).get("last_activity"),
            token_count=data.get("progress", {}).get("token_count", 0),
            tool_use_count=data.get("progress", {}).get("tool_use_count", 0),
            last_tool_name=data.get("progress", {}).get("last_tool_name"),
        )

        usage = TaskUsage(
            total_tokens=data.get("usage", {}).get("total_tokens", 0),
            tool_uses=data.get("usage", {}).get("tool_uses", 0),
            duration_ms=data.get("usage", {}).get("duration_ms", 0),
        )

        task = cls(
            id=data["id"],
            type=TaskType(data["type"]),
            description=data["description"],
            status=TaskStatus(data.get("status", "pending")),
            tool_use_id=data.get("tool_use_id"),
            start_time=datetime.fromisoformat(data["start_time"]) if data.get("start_time") else datetime.now(),
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            output_offset=data.get("output_offset", 0),
            notified=data.get("notified", False),
            progress=progress,
            usage=usage,
            messages=data.get("messages", []),
            session_state=data.get("session_state", {}),
            message_queue=data.get("message_queue", []),
            retain=data.get("retain", True),
            metadata=data.get("metadata", {}),
        )
        # Set output_file explicitly since it's computed
        task.output_file = data.get("output_file", get_task_output_path(task.id))
        return task

    def get_activity_description(self) -> str:
        """Get a human-readable activity description."""
        if self.progress.last_activity:
            return self.progress.last_activity
        return f"{self.type.value}: {self.description[:50]}..."

    def update_progress(self, activity: str | None = None, token_count: int = 0, tool_use_count: int = 0) -> None:
        """Update progress data."""
        if activity:
            self.progress.last_activity = activity
        if token_count:
            self.progress.token_count = token_count
        if tool_use_count:
            self.progress.tool_use_count = tool_use_count

    def update_usage(self, tokens: int = 0, duration_ms: int = 0) -> None:
        """Update usage statistics."""
        if tokens:
            self.usage.total_tokens = tokens
        if duration_ms:
            self.usage.duration_ms = duration_ms

    def get_output(self, max_length: int = 10000) -> str:
        """
        Read task output from file.

        Args:
            max_length: Maximum bytes to read (for preview)

        Returns:
            Output content string
        """
        if not os.path.exists(self.output_file):
            return ""

        try:
            with open(self.output_file, "r", encoding="utf-8") as f:
                f.seek(self.output_offset)
                content = f.read(max_length)
            return content
        except Exception:
            return ""


@dataclass
class TaskResult:
    """Result returned when a task completes."""

    task_id: str
    status: TaskStatus
    summary: str
    result: str | None = None
    usage: TaskUsage | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None

    def to_notification(self) -> dict[str, Any]:
        """Convert to notification format."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "summary": self.summary,
            "result": self.result,
            "usage": {
                "total_tokens": self.usage.total_tokens if self.usage else 0,
                "tool_uses": self.usage.tool_uses if self.usage else 0,
                "duration_ms": self.usage.duration_ms if self.usage else 0,
            } if self.usage else None,
            "worktree_path": self.worktree_path,
            "worktree_branch": self.worktree_branch,
        }


@dataclass
class TaskHandle:
    """Handle for managing a running task."""

    task_id: str
    cleanup: Callable[[], None] | None = None

    async def stop(self) -> None:
        """Stop the task."""
        if self.cleanup:
            self.cleanup()


class TaskManager:
    """
    Manages all tasks in the system.

    Provides:
    - Task registration and retrieval
    - Status updates
    - Output streaming
    - Task lifecycle management
    """

    def __init__(self):
        """Initialize task manager."""
        self._tasks: dict[str, Task] = {}
        self._cleanup_callbacks: dict[str, Callable[[], None]] = {}

    def create_task(
        self,
        task_type: TaskType,
        description: str,
        tool_use_id: str | None = None,
        retain: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """
        Create and register a new task.

        Args:
            task_type: Type of task to create
            description: Human-readable description
            tool_use_id: Associated tool use ID
            retain: Whether to keep output after completion
            metadata: Additional task metadata

        Returns:
            Created Task
        """
        task_id = generate_task_id(task_type)
        task = Task(
            id=task_id,
            type=task_type,
            description=description,
            tool_use_id=tool_use_id,
            retain=retain,
            metadata=metadata or {},
        )
        self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> list[Task]:
        """Get all tasks."""
        return list(self._tasks.values())

    def get_tasks_by_type(self, task_type: TaskType) -> list[Task]:
        """Get all tasks of a specific type."""
        return [t for t in self._tasks.values() if t.type == task_type]

    def get_active_tasks(self) -> list[Task]:
        """Get all currently running tasks."""
        return [t for t in self._tasks.values() if t.status == TaskStatus.RUNNING]

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        end_time: datetime | None = None,
    ) -> bool:
        """
        Update task status.

        Args:
            task_id: Task ID
            status: New status
            end_time: Optional end time (auto-set if terminal status)

        Returns:
            True if task was found and updated
        """
        task = self._tasks.get(task_id)
        if not task:
            return False

        task.status = status
        if is_terminal_status(status) and end_time is None:
            task.end_time = datetime.now()
        elif end_time:
            task.end_time = end_time

        return True

    def add_message(self, task_id: str, message: dict[str, Any]) -> bool:
        """Add a message to task history."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.messages.append(message)
        return True

    def update_task_progress(
        self,
        task_id: str,
        activity: str | None = None,
        token_count: int = 0,
        tool_use_increment: int = 0,
        last_tool_name: str | None = None,
        mark_complete: bool = False,
    ) -> bool:
        """Update progress and aggregate usage for a task."""
        task = self._tasks.get(task_id)
        if not task:
            return False

        if activity:
            task.progress.last_activity = activity
        if token_count:
            task.progress.token_count = token_count
            task.usage.total_tokens += token_count
        if tool_use_increment:
            task.progress.tool_use_count += tool_use_increment
            task.usage.tool_uses += tool_use_increment
        if last_tool_name:
            task.progress.last_tool_name = last_tool_name
        if mark_complete and task.start_time:
            task.usage.duration_ms = int((datetime.now() - task.start_time).total_seconds() * 1000)

        return True

    def set_session_state(self, task_id: str, **state: Any) -> bool:
        """Merge session state for a task."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.session_state.update(state)
        return True

    def dequeue_messages(self, task_id: str) -> list[dict[str, Any]]:
        """Remove and return queued messages for a task."""
        task = self._tasks.get(task_id)
        if not task:
            return []

        queued = task.message_queue.copy()
        task.message_queue.clear()
        return queued

    def has_pending_messages(self, task_id: str) -> bool:
        """Check whether a task has queued follow-up messages."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        return bool(task.message_queue)

    def set_cleanup_callback(self, task_id: str, callback: Callable[[], None]) -> None:
        """Set cleanup callback for a task."""
        self._cleanup_callbacks[task_id] = callback

    async def stop_task(self, task_id: str) -> bool:
        """
        Stop a running task.

        Args:
            task_id: Task ID to stop

        Returns:
            True if task was stopped
        """
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False

        # Update status first
        self.update_task_status(task_id, TaskStatus.KILLED)

        # Call cleanup if registered
        if task_id in self._cleanup_callbacks:
            try:
                self._cleanup_callbacks[task_id]()
            except Exception:
                pass
            del self._cleanup_callbacks[task_id]

        return True

    def remove_task(self, task_id: str) -> bool:
        """Remove a task from tracking."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            if task_id in self._cleanup_callbacks:
                del self._cleanup_callbacks[task_id]
            return True
        return False

    def cleanup_completed_tasks(self, max_age_hours: int = 24) -> int:
        """
        Remove old completed tasks.

        Args:
            max_age_hours: Maximum age in hours to keep

        Returns:
            Number of tasks removed
        """
        now = datetime.now()
        to_remove = []

        for task_id, task in self._tasks.items():
            if is_terminal_status(task.status) and task.end_time:
                age = now - task.end_time
                if age.total_seconds() > max_age_hours * 3600:
                    to_remove.append(task_id)

        for task_id in to_remove:
            self.remove_task(task_id)

        return len(to_remove)

    def to_dict(self) -> dict[str, Any]:
        """Convert all tasks to dictionary."""
        return {
            "tasks": [task.to_dict() for task in self._tasks.values()],
            "count": len(self._tasks),
            "active": len(self.get_active_tasks()),
        }
