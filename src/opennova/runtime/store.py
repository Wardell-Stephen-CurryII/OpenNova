"""Authoritative, session-scoped runtime state store.

The store deliberately has no UI dependency. Runtime code dispatches typed actions,
while consumers subscribe to stable selectors and render their own projections.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar
from uuid import uuid4

from opennova.runtime.state import (
    AgentState,
    Plan,
    PlanApprovalStatus,
    PlanStatus,
    StepStatus,
)

RunPhase = Literal["idle", "running", "waiting_input", "completed", "failed", "cancelled"]
SelectorValue = TypeVar("SelectorValue")


@dataclass(frozen=True)
class TodoItem:
    id: str
    content: str
    status: str = "pending"
    source: Literal["agent", "plan"] = "agent"
    plan_step_uid: str | None = None

    def to_dict(self, *, include_source: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "content": self.content, "status": self.status}
        if include_source:
            result.update({"source": self.source, "plan_step_uid": self.plan_step_uid})
        return result


@dataclass(frozen=True)
class RunState:
    run_id: str | None = None
    task: str = ""
    phase: RunPhase = "idle"
    iteration: int = 0
    error_count: int = 0
    last_action: str | None = None
    last_result: str | None = None


@dataclass(frozen=True)
class PlanState:
    plan: Plan | None = None
    file_path: Path | None = None
    lifecycle: PlanApprovalStatus = PlanApprovalStatus.NONE
    revision: int = 0
    file_hash: str | None = None


@dataclass(frozen=True)
class TodoState:
    by_agent: tuple[tuple[str, tuple[TodoItem, ...]], ...] = ()

    def for_agent(self, agent_id: str) -> tuple[TodoItem, ...]:
        return next((items for key, items in self.by_agent if key == agent_id), ())

    def replace_agent(self, agent_id: str, items: Iterable[TodoItem]) -> TodoState:
        mapping = dict(self.by_agent)
        mapping[agent_id] = tuple(items)
        return TodoState(tuple(sorted(mapping.items(), key=lambda item: item[0])))


@dataclass(frozen=True)
class InteractionState:
    waiting_for: str | None = None
    confirmation_required: bool = False
    cancel_requested: bool = False


@dataclass(frozen=True)
class RuntimeSnapshot:
    schema_version: int = 2
    revision: int = 0
    session_id: str = ""
    run: RunState = field(default_factory=RunState)
    plan: PlanState = field(default_factory=PlanState)
    todos: TodoState = field(default_factory=TodoState)
    interaction: InteractionState = field(default_factory=InteractionState)


@dataclass(frozen=True)
class RuntimeAction:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    expected_run_id: str | None = None
    expected_plan_revision: int | None = None
    expected_session_id: str | None = None


@dataclass(frozen=True)
class StateChanged:
    action_type: str
    previous_revision: int
    revision: int
    run_id: str | None
    plan_revision: int
    session_id: str = ""
    critical: bool = False


@dataclass
class _Subscription(Generic[SelectorValue]):
    selector: Callable[[RuntimeSnapshot], SelectorValue]
    listener: Callable[[SelectorValue, StateChanged], None]
    selected: SelectorValue


class InvalidStateTransitionError(ValueError):
    """Raised when an action would violate runtime state invariants."""


_CRITICAL_ACTIONS = {
    "plan_created",
    "plan_path_set",
    "plan_awaiting_approval",
    "plan_approved",
    "plan_executing",
    "plan_completed",
    "plan_failed",
    "plan_cleared",
    "plan_step_started",
    "plan_step_completed",
    "plan_step_failed",
    "todos_replaced",
    "run_completed",
    "run_cancelled",
    "state_restored",
}


class RuntimeStateStore:
    """Thread-safe state store with atomic transitions and selector subscriptions."""

    def __init__(self, facade: AgentState, session_id: str = "") -> None:
        self._lock = threading.RLock()
        self._facade = facade
        self._snapshot = RuntimeSnapshot(
            session_id=session_id,
            run=RunState(
                task=facade.current_task,
                phase="completed" if facade.is_complete else "idle",
                iteration=facade.iteration,
                error_count=facade.error_count,
                last_action=facade.last_action,
                last_result=facade.last_result,
            ),
            plan=PlanState(
                plan=copy.deepcopy(facade.current_plan),
                file_path=facade.plan_file_path,
                lifecycle=facade.plan_approval_status,
                revision=facade.plan_revision,
            ),
        )
        self._subscriptions: list[_Subscription[Any]] = []
        self._events: list[StateChanged] = []
        facade.attach_store(self)
        self._sync_facade(self._snapshot)

    def get_state(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot

    def bind_session(self, session_id: str) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, session_id=session_id)
            self._sync_facade(self._snapshot)

    def subscribe(
        self,
        selector: Callable[[RuntimeSnapshot], SelectorValue],
        listener: Callable[[SelectorValue, StateChanged], None],
        *,
        fire_immediately: bool = False,
    ) -> Callable[[], None]:
        with self._lock:
            selected = selector(self._snapshot)
            subscription = _Subscription(selector, listener, selected)
            self._subscriptions.append(subscription)
            revision = self._snapshot.revision
        if fire_immediately:
            listener(
                selected,
                StateChanged(
                    action_type="subscribed",
                    previous_revision=revision,
                    revision=revision,
                    run_id=self._snapshot.run.run_id,
                    plan_revision=self._snapshot.plan.revision,
                    session_id=self._snapshot.session_id,
                ),
            )

        def unsubscribe() -> None:
            with self._lock:
                if subscription in self._subscriptions:
                    self._subscriptions.remove(subscription)

        return unsubscribe

    def dispatch(self, action: RuntimeAction) -> RuntimeSnapshot:
        return self.transaction([action])

    def transaction(self, actions: Iterable[RuntimeAction]) -> RuntimeSnapshot:
        action_list = list(actions)
        if not action_list:
            return self.get_state()
        with self._lock:
            previous = self._snapshot
            next_state = previous
            applied: list[RuntimeAction] = []
            for action in action_list:
                if (
                    action.expected_session_id
                    and action.expected_session_id != next_state.session_id
                ):
                    continue
                if action.expected_run_id and action.expected_run_id != next_state.run.run_id:
                    continue
                if (
                    action.expected_plan_revision is not None
                    and action.expected_plan_revision != next_state.plan.revision
                ):
                    continue
                next_state = self._reduce(next_state, action)
                applied.append(action)
            if not applied:
                return previous
            next_state = replace(next_state, revision=previous.revision + 1)
            self._validate(next_state)
            self._snapshot = next_state
            self._sync_facade(next_state)
            event = StateChanged(
                action_type=applied[-1].type if len(applied) == 1 else "transaction",
                previous_revision=previous.revision,
                revision=next_state.revision,
                run_id=next_state.run.run_id,
                plan_revision=next_state.plan.revision,
                session_id=next_state.session_id,
                critical=any(action.type in _CRITICAL_ACTIONS for action in applied),
            )
            self._events.append(event)
            self._events = self._events[-500:]
            notifications: list[tuple[Callable[..., None], Any]] = []
            for subscription in list(self._subscriptions):
                selected = subscription.selector(next_state)
                if selected != subscription.selected:
                    subscription.selected = selected
                    notifications.append((subscription.listener, selected))
        for listener, selected in notifications:
            listener(selected, event)
        return next_state

    def replace_agent_todos(
        self, todos: Iterable[dict[str, Any]], *, agent_id: str = "main"
    ) -> list[dict[str, Any]]:
        items = tuple(
            TodoItem(
                id=str(item["id"]),
                content=str(item["content"]),
                status=str(item.get("status", "pending")),
            )
            for item in todos
        )
        self.dispatch(RuntimeAction("todos_replaced", {"agent_id": agent_id, "items": items}))
        return [item.to_dict() for item in items]

    def current_todos(self, *, agent_id: str = "main") -> list[dict[str, Any]]:
        snapshot = self.get_state()
        plan_items = self._plan_todos(snapshot.plan.plan)
        agent_items = list(snapshot.todos.for_agent(agent_id))
        plan_ids = {item.id for item in plan_items}
        merged = [*plan_items, *(item for item in agent_items if item.id not in plan_ids)]
        return [item.to_dict() for item in merged]

    def recent_events(self) -> list[StateChanged]:
        with self._lock:
            return list(self._events)

    def serialize(self) -> dict[str, Any]:
        snapshot = self.get_state()
        return {
            "schema_version": snapshot.schema_version,
            "revision": snapshot.revision,
            "session_id": snapshot.session_id,
            "run": {
                "run_id": snapshot.run.run_id,
                "task": snapshot.run.task,
                "phase": snapshot.run.phase,
                "iteration": snapshot.run.iteration,
                "error_count": snapshot.run.error_count,
                "last_action": snapshot.run.last_action,
                "last_result": snapshot.run.last_result,
            },
            "plan": {
                "current_plan": snapshot.plan.plan.to_dict() if snapshot.plan.plan else None,
                "plan_file_path": str(snapshot.plan.file_path) if snapshot.plan.file_path else None,
                "lifecycle": snapshot.plan.lifecycle.value,
                "revision": snapshot.plan.revision,
                "file_hash": snapshot.plan.file_hash,
            },
            "todos": {
                agent_id: [item.to_dict(include_source=True) for item in items]
                for agent_id, items in snapshot.todos.by_agent
            },
            "interaction": {
                "waiting_for": snapshot.interaction.waiting_for,
                "confirmation_required": snapshot.interaction.confirmation_required,
                "cancel_requested": snapshot.interaction.cancel_requested,
            },
        }

    def restore(self, payload: dict[str, Any]) -> RuntimeSnapshot:
        run_data = payload.get("run") or {}
        plan_data = payload.get("plan") or {}
        todo_data = payload.get("todos") or {}
        interaction_data = payload.get("interaction") or {}
        plan_payload = plan_data.get("current_plan")
        plan = Plan.from_dict(plan_payload) if isinstance(plan_payload, dict) else None
        todo_state = TodoState()
        for agent_id, raw_items in todo_data.items():
            items = [
                TodoItem(
                    id=str(item.get("id", "")),
                    content=str(item.get("content", "")),
                    status=str(item.get("status", "pending")),
                    source="agent",
                )
                for item in raw_items
                if item.get("content")
            ]
            todo_state = todo_state.replace_agent(str(agent_id), items)
        restored = RuntimeSnapshot(
            schema_version=2,
            revision=int(payload.get("revision", 0)),
            session_id=str(payload.get("session_id") or self._snapshot.session_id),
            run=RunState(
                run_id=run_data.get("run_id"),
                task=str(run_data.get("task", "")),
                phase=str(run_data.get("phase", "idle")),
                iteration=int(run_data.get("iteration", 0)),
                error_count=int(run_data.get("error_count", 0)),
                last_action=run_data.get("last_action"),
                last_result=run_data.get("last_result"),
            ),
            plan=PlanState(
                plan=plan,
                file_path=Path(plan_data["plan_file_path"])
                if plan_data.get("plan_file_path")
                else None,
                lifecycle=PlanApprovalStatus(plan_data.get("lifecycle", "none")),
                revision=int(plan_data.get("revision", 0)),
                file_hash=plan_data.get("file_hash"),
            ),
            todos=todo_state,
            interaction=InteractionState(
                waiting_for=interaction_data.get("waiting_for"),
                confirmation_required=bool(interaction_data.get("confirmation_required", False)),
                cancel_requested=bool(interaction_data.get("cancel_requested", False)),
            ),
        )
        with self._lock:
            previous = self._snapshot
            restored = replace(restored, revision=max(previous.revision, restored.revision) + 1)
            self._validate(restored)
            self._snapshot = restored
            self._sync_facade(restored)
            event = StateChanged(
                "state_restored",
                previous.revision,
                restored.revision,
                restored.run.run_id,
                restored.plan.revision,
                restored.session_id,
                True,
            )
            self._events.append(event)
            notifications = []
            for subscription in list(self._subscriptions):
                selected = subscription.selector(restored)
                if selected != subscription.selected:
                    subscription.selected = selected
                    notifications.append((subscription.listener, selected))
        for listener, selected in notifications:
            listener(selected, event)
        return restored

    def _reduce(self, state: RuntimeSnapshot, action: RuntimeAction) -> RuntimeSnapshot:
        payload = action.payload
        if action.type == "run_started":
            preserve_plan = bool(payload.get("preserve_plan"))
            plan_state = state.plan if preserve_plan else PlanState()
            return replace(
                state,
                run=RunState(run_id=uuid4().hex, task=str(payload.get("task", "")), phase="running"),
                plan=plan_state,
                todos=state.todos if preserve_plan else TodoState(),
                interaction=InteractionState(),
            )
        if action.type == "run_iteration_incremented":
            return replace(state, run=replace(state.run, iteration=state.run.iteration + 1))
        if action.type == "run_error_incremented":
            return replace(state, run=replace(state.run, error_count=state.run.error_count + 1))
        if action.type == "run_action_recorded":
            return replace(
                state,
                run=replace(
                    state.run,
                    last_action=payload.get("action"),
                    last_result=payload.get("result"),
                ),
            )
        if action.type == "run_completed":
            phase: RunPhase = "completed" if payload.get("success", True) else "failed"
            return replace(state, run=replace(state.run, phase=phase, last_result=payload.get("result")))
        if action.type == "run_cancelled":
            return replace(state, run=replace(state.run, phase="cancelled"))
        if action.type == "mode_changed":
            mode = payload.get("mode")
            if mode == "plan" and state.plan.lifecycle == PlanApprovalStatus.NONE:
                return replace(state, plan=replace(state.plan, lifecycle=PlanApprovalStatus.DRAFT))
            if mode == "act" and state.plan.plan is None:
                return replace(state, plan=replace(state.plan, lifecycle=PlanApprovalStatus.NONE))
            return state
        if action.type == "plan_created":
            plan = copy.deepcopy(payload["plan"])
            plan.reindex_steps()
            plan._update_plan_status()
            return replace(
                state,
                plan=PlanState(
                    plan=plan,
                    file_path=state.plan.file_path,
                    lifecycle=PlanApprovalStatus.DRAFT,
                    revision=state.plan.revision + 1,
                ),
            )
        if action.type == "plan_path_set":
            return replace(
                state,
                plan=replace(
                    state.plan,
                    file_path=Path(payload["path"]),
                    file_hash=payload.get("file_hash", state.plan.file_hash),
                    revision=state.plan.revision + 1,
                ),
            )
        if action.type == "plan_file_persisted":
            return replace(
                state,
                plan=replace(state.plan, file_hash=payload.get("file_hash")),
            )
        if action.type == "plan_awaiting_approval":
            return replace(
                state,
                plan=replace(
                    state.plan,
                    lifecycle=PlanApprovalStatus.AWAITING_APPROVAL,
                    revision=state.plan.revision + 1,
                ),
                interaction=InteractionState(waiting_for="plan_approval", confirmation_required=True),
            )
        if action.type == "plan_approved":
            return replace(
                state,
                plan=replace(
                    state.plan,
                    lifecycle=PlanApprovalStatus.APPROVED,
                    revision=state.plan.revision + 1,
                ),
                interaction=InteractionState(),
            )
        if action.type == "plan_executing":
            plan = copy.deepcopy(state.plan.plan)
            if plan is not None:
                plan.status = PlanStatus.EXECUTING
            return replace(
                state,
                plan=replace(
                    state.plan,
                    plan=plan,
                    lifecycle=PlanApprovalStatus.EXECUTING,
                    revision=state.plan.revision + 1,
                ),
                interaction=InteractionState(),
            )
        if action.type in {"plan_completed", "plan_failed"}:
            lifecycle = (
                PlanApprovalStatus.COMPLETED
                if action.type == "plan_completed"
                else PlanApprovalStatus.FAILED
            )
            plan = copy.deepcopy(state.plan.plan)
            if plan:
                plan._update_plan_status()
            return replace(
                state,
                plan=replace(state.plan, plan=plan, lifecycle=lifecycle, revision=state.plan.revision + 1),
                interaction=InteractionState(),
            )
        if action.type == "plan_cleared":
            return replace(state, plan=PlanState(), interaction=InteractionState())
        if action.type in {"plan_step_started", "plan_step_completed", "plan_step_failed"}:
            if state.plan.plan is None:
                raise InvalidStateTransitionError(f"{action.type} requires an active plan")
            plan = copy.deepcopy(state.plan.plan)
            step_id = str(payload["step_id"])
            if action.type == "plan_step_started":
                for step in plan.steps:
                    if step.status == StepStatus.RUNNING and step.id != step_id:
                        step.status = StepStatus.PENDING
                plan.mark_step_running(step_id)
                lifecycle = PlanApprovalStatus.EXECUTING
            elif action.type == "plan_step_completed":
                plan.mark_step_done(step_id, payload.get("result"))
                lifecycle = state.plan.lifecycle
            else:
                plan.mark_step_failed(step_id, str(payload.get("error", "")))
                lifecycle = PlanApprovalStatus.FAILED
            return replace(
                state,
                plan=replace(
                    state.plan,
                    plan=plan,
                    lifecycle=lifecycle,
                    revision=state.plan.revision + 1,
                ),
            )
        if action.type == "plan_steps_requeued":
            if state.plan.plan is None:
                raise InvalidStateTransitionError("plan_steps_requeued requires an active plan")
            plan = copy.deepcopy(state.plan.plan)
            for step in plan.steps:
                if step.status in {StepStatus.RUNNING, StepStatus.FAILED}:
                    step.status = StepStatus.PENDING
                    step.error = None
            plan._update_plan_status()
            return replace(
                state,
                plan=replace(
                    state.plan,
                    plan=plan,
                    lifecycle=PlanApprovalStatus.APPROVED,
                    revision=state.plan.revision + 1,
                ),
            )
        if action.type == "plan_file_changed":
            incoming = copy.deepcopy(payload["plan"])
            plan = self._merge_plan_file(state.plan.plan, incoming)
            return replace(
                state,
                plan=replace(
                    state.plan,
                    plan=plan,
                    file_hash=payload.get("file_hash"),
                    revision=state.plan.revision + 1,
                ),
            )
        if action.type == "todos_replaced":
            return replace(
                state,
                todos=state.todos.replace_agent(str(payload.get("agent_id", "main")), payload["items"]),
            )
        raise ValueError(f"Unknown runtime state action: {action.type}")

    @staticmethod
    def _merge_plan_file(current: Plan | None, incoming: Plan) -> Plan:
        if current is None:
            return incoming.reindex_steps()
        current_by_uid = {step.uid: step for step in current.steps}
        current_by_id = {step.id: step for step in current.steps}
        for step in incoming.steps:
            existing = current_by_uid.get(step.uid) or current_by_id.get(step.id)
            if existing and existing.status in {StepStatus.DONE, StepStatus.RUNNING, StepStatus.FAILED}:
                step.uid = existing.uid
                step.status = existing.status
                step.result_summary = existing.result_summary
                step.error = existing.error
        incoming.reindex_steps()
        incoming._update_plan_status()
        return incoming

    @staticmethod
    def _plan_todos(plan: Plan | None) -> list[TodoItem]:
        if plan is None:
            return []
        status_map = {
            StepStatus.PENDING: "pending",
            StepStatus.RUNNING: "in_progress",
            StepStatus.DONE: "done",
            StepStatus.FAILED: "cancelled",
            StepStatus.SKIPPED: "cancelled",
        }
        return [
            TodoItem(
                id=step.id,
                content=step.description.strip() or step.id,
                status=status_map[step.status],
                source="plan",
                plan_step_uid=step.uid,
            )
            for step in plan.steps
        ]

    @staticmethod
    def _validate(state: RuntimeSnapshot) -> None:
        plan = state.plan.plan
        lifecycle = state.plan.lifecycle
        if lifecycle in {
            PlanApprovalStatus.AWAITING_APPROVAL,
            PlanApprovalStatus.APPROVED,
            PlanApprovalStatus.EXECUTING,
            PlanApprovalStatus.COMPLETED,
            PlanApprovalStatus.FAILED,
        } and plan is None:
            raise InvalidStateTransitionError(
                f"Plan lifecycle {lifecycle.value} requires an active plan"
            )
        if plan is None:
            return
        running = [step for step in plan.steps if step.status == StepStatus.RUNNING]
        if len(running) > 1:
            raise InvalidStateTransitionError("A plan may have at most one running step")
        if lifecycle == PlanApprovalStatus.COMPLETED and any(
            step.status not in {StepStatus.DONE, StepStatus.SKIPPED} for step in plan.steps
        ):
            raise InvalidStateTransitionError(
                "A completed plan cannot contain unfinished steps"
            )

    def _sync_facade(self, snapshot: RuntimeSnapshot) -> None:
        plan_lifecycle = snapshot.plan.lifecycle
        mode = "plan" if plan_lifecycle in {
            PlanApprovalStatus.DRAFT,
            PlanApprovalStatus.AWAITING_APPROVAL,
        } else "act"
        object.__setattr__(self._facade, "current_task", snapshot.run.task)
        object.__setattr__(self._facade, "mode", mode)
        object.__setattr__(self._facade, "iteration", snapshot.run.iteration)
        object.__setattr__(self._facade, "is_complete", snapshot.run.phase == "completed")
        object.__setattr__(
            self._facade,
            "requires_confirmation",
            plan_lifecycle == PlanApprovalStatus.AWAITING_APPROVAL,
        )
        object.__setattr__(self._facade, "current_plan", snapshot.plan.plan)
        object.__setattr__(self._facade, "plan_file_path", snapshot.plan.file_path)
        object.__setattr__(self._facade, "plan_approval_status", plan_lifecycle)
        object.__setattr__(self._facade, "error_count", snapshot.run.error_count)
        object.__setattr__(self._facade, "last_action", snapshot.run.last_action)
        object.__setattr__(self._facade, "last_result", snapshot.run.last_result)
        object.__setattr__(self._facade, "run_id", snapshot.run.run_id)
        object.__setattr__(self._facade, "plan_revision", snapshot.plan.revision)
