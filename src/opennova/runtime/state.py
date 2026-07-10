"""
Agent State Management.

Defines the state data structures for tracking agent execution:
- AgentState: Current state of the agent runtime
- Plan/PlanStep: Task planning structures (Phase 2)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from opennova.runtime.store import RuntimeStateStore


class AgentMode(StrEnum):
    """Agent operation modes."""

    PLAN = "plan"
    ACT = "act"


class StepStatus(StrEnum):
    """Status of a plan step."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"


class PlanStatus(StrEnum):
    """Overall plan status."""

    PLANNING = "planning"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class PlanApprovalStatus(StrEnum):
    """Approval lifecycle for the current plan."""

    NONE = "none"
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DISCARDED = "discarded"


@dataclass
class PlanStep:
    """A single step in a plan."""

    id: str
    description: str
    uid: str = field(default_factory=lambda: uuid4().hex)
    status: StepStatus = StepStatus.PENDING
    tool_hint: str | None = None
    result_summary: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "uid": self.uid,
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
                self.status = PlanStatus.EXECUTING
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

        self._update_plan_status()

    def _update_plan_status(self) -> None:
        """Update overall plan status based on steps."""
        all_done = bool(self.steps) and all(
            s.status in {StepStatus.DONE, StepStatus.SKIPPED} for s in self.steps
        )
        any_failed = any(s.status == StepStatus.FAILED for s in self.steps)
        any_interrupted = any(s.status == StepStatus.INTERRUPTED for s in self.steps)

        if any_failed:
            self.status = PlanStatus.FAILED
        elif any_interrupted:
            self.status = PlanStatus.INTERRUPTED
        elif all_done:
            self.status = PlanStatus.DONE
        elif any(s.status == StepStatus.RUNNING for s in self.steps):
            self.status = PlanStatus.EXECUTING

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "task": self.task,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at.isoformat(),
        }

    def reindex_steps(self) -> "Plan":
        """Normalize top-level step ids to a stable contiguous step_N sequence."""
        for index, step in enumerate(self.steps, start=1):
            step.id = f"step_{index}"
        return self

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        """Create Plan from dictionary."""
        steps = [
            PlanStep(
                id=s["id"],
                description=s["description"],
                uid=str(s.get("uid") or uuid4().hex),
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
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if data.get("created_at")
                else datetime.now()
            ),
            status=PlanStatus(data.get("status", "planning")),
        ).reindex_steps()


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
    run_id: str | None = None
    plan_revision: int = 0
    _store: "RuntimeStateStore | None" = field(default=None, init=False, repr=False, compare=False)

    def attach_store(self, store: "RuntimeStateStore") -> None:
        """Attach the compatibility facade to its authoritative runtime store."""
        object.__setattr__(self, "_store", store)

    @property
    def store(self) -> "RuntimeStateStore | None":
        return self._store

    def _dispatch(
        self,
        action_type: str,
        *,
        expected_run_id: str | None = None,
        expected_plan_revision: int | None = None,
        **payload: Any,
    ) -> bool:
        store = self._store
        if store is None:
            return False
        from opennova.runtime.store import RuntimeAction

        store.dispatch(
            RuntimeAction(
                type=action_type,
                payload=payload,
                expected_run_id=expected_run_id,
                expected_plan_revision=expected_plan_revision,
            )
        )
        return True

    def reset(self, task: str = "") -> None:
        """Reset state for a new task."""
        if self._dispatch("run_started", task=task, preserve_plan=False):
            return
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

    def reset_execution(self, task: str = "") -> None:
        """Reset per-run execution fields while preserving approved plan state."""
        if self._dispatch("run_started", task=task, preserve_plan=True):
            return
        self.current_task = task
        self.mode = "act"
        self.iteration = 0
        self.is_complete = False
        self.requires_confirmation = False
        self.error_count = 0
        self.last_action = None
        self.last_result = None

    def increment_iteration(self, run_id: str | None = None) -> None:
        """Increment iteration counter."""
        if self._dispatch("run_iteration_incremented", expected_run_id=run_id):
            return
        self.iteration += 1

    def increment_error(self, run_id: str | None = None) -> None:
        """Increment error counter."""
        if self._dispatch("run_error_incremented", expected_run_id=run_id):
            return
        self.error_count += 1

    def has_too_many_errors(self) -> bool:
        """Check if error count exceeds threshold."""
        return self.error_count >= self.max_errors

    def mark_complete(self, result: str | None = None, run_id: str | None = None) -> None:
        """Mark task as complete."""
        if self._dispatch(
            "run_completed", expected_run_id=run_id, result=result, success=True
        ):
            return
        self.is_complete = True
        self.last_result = result

    def finish_run(self, result: str, *, success: bool, run_id: str | None = None) -> None:
        """Finalize a run if the supplied run identity is still current."""
        if self._dispatch(
            "run_completed",
            expected_run_id=run_id,
            result=result,
            success=success,
        ):
            return
        self.is_complete = success
        self.last_result = result

    def cancel_run(self, run_id: str | None = None) -> None:
        """Cancel the current run if its identity is still active."""
        self._dispatch("run_cancelled", expected_run_id=run_id)

    def begin_interaction(self, interaction_type: str) -> None:
        self._dispatch("interaction_waiting", interaction_type=interaction_type)

    def end_interaction(self) -> None:
        self._dispatch("interaction_cleared")

    def set_mode(self, mode: Literal["plan", "act"]) -> None:
        """Set agent mode."""
        if self._dispatch("mode_changed", mode=mode):
            return
        self.mode = mode

    def set_plan(self, plan: Plan) -> None:
        """Set the current plan."""
        if self._dispatch("plan_created", plan=plan):
            return
        plan.reindex_steps()
        self.current_plan = plan
        self.mode = "plan"
        self.plan_approval_status = PlanApprovalStatus.DRAFT
        self.requires_confirmation = False

    def set_plan_file_path(self, path: str | Path, file_hash: str | None = None) -> None:
        """Set the saved plan file path."""
        if self._dispatch("plan_path_set", path=Path(path), file_hash=file_hash):
            return
        self.plan_file_path = Path(path)

    def mark_plan_awaiting_approval(self) -> None:
        """Mark the current plan as ready for user approval."""
        if self._dispatch("plan_awaiting_approval"):
            return
        self.mode = "plan"
        self.plan_approval_status = PlanApprovalStatus.AWAITING_APPROVAL
        self.requires_confirmation = True

    def mark_plan_approved(self) -> None:
        """Mark the current plan as approved for execution."""
        if self._dispatch("plan_approved"):
            return
        self.plan_approval_status = PlanApprovalStatus.APPROVED
        self.requires_confirmation = False

    def mark_plan_executing(self) -> None:
        """Mark the current plan as executing."""
        if self._dispatch("plan_executing"):
            return
        self.mode = "act"
        self.plan_approval_status = PlanApprovalStatus.EXECUTING
        self.requires_confirmation = False

    def clear_plan_state(self) -> None:
        """Clear any active plan lifecycle state."""
        if self._dispatch("plan_cleared"):
            return
        self.current_plan = None
        self.plan_file_path = None
        self.plan_approval_status = PlanApprovalStatus.NONE
        self.requires_confirmation = False

    def mark_plan_failed(self) -> None:
        """Mark the current plan as failed while preserving it for inspection."""
        if self._dispatch("plan_failed"):
            return
        self.mode = "act"
        self.plan_approval_status = PlanApprovalStatus.FAILED
        self.requires_confirmation = False

    def mark_plan_completed(self) -> None:
        """Keep the completed plan available for inspection and derived todos."""
        if self._dispatch("plan_completed"):
            return
        self.mode = "act"
        self.plan_approval_status = PlanApprovalStatus.COMPLETED
        self.requires_confirmation = False

    def mark_step_running(self, step_id: str) -> None:
        if self._dispatch("plan_step_started", step_id=step_id):
            return
        if self.current_plan:
            self.current_plan.mark_step_running(step_id)

    def mark_step_done(
        self,
        step_id: str,
        result: str | None = None,
        *,
        expected_plan_revision: int | None = None,
    ) -> None:
        if self._dispatch(
            "plan_step_completed",
            expected_plan_revision=expected_plan_revision,
            step_id=step_id,
            result=result,
        ):
            return
        if self.current_plan:
            self.current_plan.mark_step_done(step_id, result)

    def mark_step_failed(
        self,
        step_id: str,
        error: str,
        *,
        expected_plan_revision: int | None = None,
    ) -> None:
        if self._dispatch(
            "plan_step_failed",
            expected_plan_revision=expected_plan_revision,
            step_id=step_id,
            error=error,
        ):
            return
        if self.current_plan:
            self.current_plan.mark_step_failed(step_id, error)

    def requeue_interrupted_plan_steps(self) -> None:
        """Move failed/running steps back to pending through one transition."""
        if self._dispatch("plan_steps_requeued"):
            return
        if self.current_plan:
            for step in self.current_plan.steps:
                if step.status in {
                    StepStatus.RUNNING,
                    StepStatus.FAILED,
                    StepStatus.INTERRUPTED,
                }:
                    step.status = StepStatus.PENDING
                    step.error = None

    def record_action_result(
        self,
        action: str,
        result: str | None,
        *,
        run_id: str | None = None,
    ) -> None:
        """Record a tool result through the state store when available."""
        if self._dispatch(
            "run_action_recorded", expected_run_id=run_id, action=action, result=result
        ):
            return
        self.last_action = action
        self.last_result = result

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
            "run_id": self.run_id,
            "plan_revision": self.plan_revision,
        }
