"""
Agent State Management.

Defines the state data structures for tracking agent execution:
- AgentState: Current state of the agent runtime
- Plan/PlanStep: Task planning structures (Phase 2)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class AgentMode(str, Enum):
    """Agent operation modes."""

    PLAN = "plan"
    ACT = "act"


class StepStatus(str, Enum):
    """Status of a plan step."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStatus(str, Enum):
    """Overall plan status."""

    PLANNING = "planning"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


class PlanApprovalStatus(str, Enum):
    """Approval lifecycle for the current plan."""

    NONE = "none"
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"


@dataclass
class PlanStep:
    """A single step in a plan."""

    id: str
    description: str
    status: StepStatus = StepStatus.PENDING
    tool_hint: str | None = None
    result_summary: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "tool_hint": self.tool_hint,
            "result_summary": self.result_summary,
            "error": self.error,
        }


@dataclass
class Plan:
    """A task plan with multiple steps."""

    task: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    status: PlanStatus = PlanStatus.PLANNING

    def get_next_step(self) -> PlanStep | None:
        """Get the next pending step."""
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                return step
        return None

    def mark_step_running(self, step_id: str) -> None:
        """Mark a step as running."""
        for step in self.steps:
            if step.id == step_id:
                step.status = StepStatus.RUNNING
                break

    def mark_step_done(self, step_id: str, result: str | None = None) -> None:
        """Mark a step as completed."""
        for step in self.steps:
            if step.id == step_id:
                step.status = StepStatus.DONE
                step.result_summary = result
                break

        self._update_plan_status()

    def mark_step_failed(self, step_id: str, error: str) -> None:
        """Mark a step as failed."""
        for step in self.steps:
            if step.id == step_id:
                step.status = StepStatus.FAILED
                step.error = error
                break

    def _update_plan_status(self) -> None:
        """Update overall plan status based on steps."""
        all_done = all(s.status == StepStatus.DONE for s in self.steps)
        any_failed = any(s.status == StepStatus.FAILED for s in self.steps)

        if any_failed:
            self.status = PlanStatus.FAILED
        elif all_done:
            self.status = PlanStatus.DONE

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "task": self.task,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        """Create Plan from dictionary."""
        steps = [
            PlanStep(
                id=s["id"],
                description=s["description"],
                status=StepStatus(s.get("status", "pending")),
                tool_hint=s.get("tool_hint"),
                result_summary=s.get("result_summary"),
                error=s.get("error"),
            )
            for s in data.get("steps", [])
        ]

        return cls(
            task=data["task"],
            steps=steps,
            status=PlanStatus(data.get("status", "planning")),
        )


@dataclass
class AgentState:
    """
    Current state of the agent runtime.

    Tracks the current task, mode, iteration count, and execution status.
    """

    current_task: str = ""
    mode: Literal["plan", "act"] = "act"
    iteration: int = 0
    is_complete: bool = False
    requires_confirmation: bool = False
    current_plan: Plan | None = None
    plan_file_path: Path | None = None
    plan_approval_status: PlanApprovalStatus = PlanApprovalStatus.NONE
    error_count: int = 0
    max_errors: int = 3
    last_action: str | None = None
    last_result: str | None = None

    def reset(self, task: str = "") -> None:
        """Reset state for a new task."""
        self.current_task = task
        self.mode = "act"
        self.iteration = 0
        self.is_complete = False
        self.requires_confirmation = False
        self.current_plan = None
        self.plan_file_path = None
        self.plan_approval_status = PlanApprovalStatus.NONE
        self.error_count = 0
        self.last_action = None
        self.last_result = None

    def increment_iteration(self) -> None:
        """Increment iteration counter."""
        self.iteration += 1

    def increment_error(self) -> None:
        """Increment error counter."""
        self.error_count += 1

    def has_too_many_errors(self) -> bool:
        """Check if error count exceeds threshold."""
        return self.error_count >= self.max_errors

    def mark_complete(self, result: str | None = None) -> None:
        """Mark task as complete."""
        self.is_complete = True
        self.last_result = result

    def set_mode(self, mode: Literal["plan", "act"]) -> None:
        """Set agent mode."""
        self.mode = mode

    def set_plan(self, plan: Plan) -> None:
        """Set the current plan."""
        self.current_plan = plan
        self.mode = "plan"
        self.plan_approval_status = PlanApprovalStatus.DRAFT
        self.requires_confirmation = False

    def set_plan_file_path(self, path: str | Path) -> None:
        """Set the saved plan file path."""
        self.plan_file_path = Path(path)

    def mark_plan_awaiting_approval(self) -> None:
        """Mark the current plan as ready for user approval."""
        self.mode = "plan"
        self.plan_approval_status = PlanApprovalStatus.AWAITING_APPROVAL
        self.requires_confirmation = True

    def mark_plan_approved(self) -> None:
        """Mark the current plan as approved for execution."""
        self.plan_approval_status = PlanApprovalStatus.APPROVED
        self.requires_confirmation = False

    def mark_plan_executing(self) -> None:
        """Mark the current plan as executing."""
        self.mode = "act"
        self.plan_approval_status = PlanApprovalStatus.EXECUTING
        self.requires_confirmation = False

    def clear_plan_state(self) -> None:
        """Clear any active plan lifecycle state."""
        self.current_plan = None
        self.plan_file_path = None
        self.plan_approval_status = PlanApprovalStatus.NONE
        self.requires_confirmation = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "current_task": self.current_task,
            "mode": self.mode,
            "iteration": self.iteration,
            "is_complete": self.is_complete,
            "error_count": self.error_count,
            "requires_confirmation": self.requires_confirmation,
            "plan_approval_status": self.plan_approval_status.value,
            "plan_file_path": str(self.plan_file_path) if self.plan_file_path else None,
            "current_plan": self.current_plan.to_dict() if self.current_plan else None,
        }
