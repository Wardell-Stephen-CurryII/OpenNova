"""
Working Memory - Short-term task memory.

Manages memory within a single task:
- Current task state
- Action history
- File changes observed
- Intermediate results
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class ActionStatus(str, Enum):
    """Status of an action."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ActionRecord:
    """Record of a single action execution."""

    id: str
    tool_name: str
    arguments: dict[str, Any]
    status: ActionStatus = ActionStatus.PENDING
    result: str | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: int | None = None


@dataclass
class FileObservation:
    """Record of a file change observation."""

    file_path: str
    change_type: str
    content_preview: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TaskState:
    """State of the current task."""

    description: str
    status: str = "pending"
    progress: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class WorkingMemory:
    """
    Short-term working memory for a task.

    Tracks:
    - Current task state
    - Actions taken
    - Files observed
    - Key decisions made
    """

    def __init__(self, task: str = ""):
        """
        Initialize working memory.

        Args:
            task: Initial task description
        """
        self.task_state = TaskState(description=task)
        self.actions: list[ActionRecord] = []
        self.observations: list[FileObservation] = []
        self.decisions: list[str] = []
        self.context_items: dict[str, Any] = {}
        self._action_counter = 0

    def set_task(self, task: str) -> None:
        """Set the current task."""
        self.task_state.description = task
        self.task_state.status = "pending"
        self.task_state.progress = 0.0

    def start_task(self) -> None:
        """Mark task as started."""
        self.task_state.status = "running"
        self.task_state.started_at = datetime.now()

    def update_progress(self, progress: float) -> None:
        """Update task progress (0.0 to 1.0)."""
        self.task_state.progress = min(1.0, max(0.0, progress))

    def complete_task(self, success: bool = True, error: str | None = None) -> None:
        """Mark task as complete."""
        self.task_state.status = "completed" if success else "failed"
        self.task_state.completed_at = datetime.now()
        self.task_state.progress = 1.0
        if error:
            self.task_state.error = error

    def record_action(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ActionRecord:
        """
        Record a new action.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            ActionRecord for tracking
        """
        self._action_counter += 1
        record = ActionRecord(
            id=f"action_{self._action_counter}",
            tool_name=tool_name,
            arguments=arguments,
            timestamp=datetime.now(),
        )
        self.actions.append(record)
        return record

    def update_action(
        self,
        action_id: str,
        status: ActionStatus,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        """
        Update action status.

        Args:
            action_id: Action ID to update
            status: New status
            result: Result of the action
            error: Error if failed
        """
        for action in self.actions:
            if action.id == action_id:
                action.status = status
                action.result = result
                action.error = error
                break

    def observe_file(
        self,
        file_path: str,
        change_type: str,
        content_preview: str | None = None,
    ) -> None:
        """
        Record a file observation.

        Args:
            file_path: Path to the file
            change_type: Type of change (read/modified/created/deleted)
            content_preview: Preview of content
        """
        observation = FileObservation(
            file_path=file_path,
            change_type=change_type,
            content_preview=content_preview,
        )
        self.observations.append(observation)

    def add_decision(self, decision: str) -> None:
        """
        Record a key decision made during task.

        Args:
            decision: Decision description
        """
        self.decisions.append(decision)

    def set_context(self, key: str, value: Any) -> None:
        """
        Store a context item.

        Args:
            key: Context key
            value: Context value
        """
        self.context_items[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        """
        Get a context item.

        Args:
            key: Context key
            default: Default value if not found

        Returns:
            Context value or default
        """
        return self.context_items.get(key, default)

    def get_action_history(self) -> list[dict[str, Any]]:
        """Get history of all actions."""
        return [
            {
                "id": a.id,
                "tool": a.tool_name,
                "args": a.arguments,
                "status": a.status.value,
                "result": a.result,
                "error": a.error,
            }
            for a in self.actions
        ]

    def get_files_modified(self) -> list[str]:
        """Get list of files that were modified."""
        return list(
            {
                obs.file_path
                for obs in self.observations
                if obs.change_type in ("modified", "created", "deleted")
            }
        )

    def get_files_read(self) -> list[str]:
        """Get list of files that were read."""
        return list(
            {obs.file_path for obs in self.observations if obs.change_type == "read"}
        )

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of working memory."""
        return {
            "task": self.task_state.description,
            "status": self.task_state.status,
            "progress": self.task_state.progress,
            "total_actions": len(self.actions),
            "successful_actions": sum(
                1 for a in self.actions if a.status == ActionStatus.SUCCESS
            ),
            "failed_actions": sum(
                1 for a in self.actions if a.status == ActionStatus.FAILED
            ),
            "files_read": len(self.get_files_read()),
            "files_modified": len(self.get_files_modified()),
            "decisions_made": len(self.decisions),
        }

    def clear(self) -> None:
        """Clear all working memory."""
        self.task_state = TaskState(description="")
        self.actions.clear()
        self.observations.clear()
        self.decisions.clear()
        self.context_items.clear()
        self._action_counter = 0

    def __repr__(self) -> str:
        return (
            f"WorkingMemory(task={self.task_state.description[:30]}..., "
            f"actions={len(self.actions)}, "
            f"status={self.task_state.status})"
        )
