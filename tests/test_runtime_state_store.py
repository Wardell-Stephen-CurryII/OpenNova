from __future__ import annotations

from pathlib import Path

import pytest

from opennova.runtime.event_bus import RuntimeEventBus
from opennova.runtime.state import (
    AgentState,
    Plan,
    PlanApprovalStatus,
    PlanStep,
    StepStatus,
)
from opennova.runtime.store import (
    InvalidStateTransitionError,
    RuntimeAction,
    RuntimeStateStore,
)


def test_runtime_store_transaction_notifies_selector_once() -> None:
    state = AgentState()
    store = RuntimeStateStore(state, session_id="session-a")
    notifications: list[tuple[int, int]] = []
    store.subscribe(
        lambda snapshot: snapshot.run.iteration,
        lambda selected, event: notifications.append((selected, event.revision)),
    )

    store.transaction(
        [
            RuntimeAction("run_started", {"task": "work", "preserve_plan": False}),
            RuntimeAction("run_iteration_incremented"),
            RuntimeAction("run_iteration_incremented"),
        ]
    )

    assert state.iteration == 2
    assert store.get_state().revision == 1
    assert notifications == [(2, 1)]


def test_runtime_event_bus_supports_multiple_subscribers_and_unsubscribe() -> None:
    bus = RuntimeEventBus()
    calls: list[str] = []
    unsubscribe_first = bus.subscribe("plan", lambda: calls.append("first"))
    bus.subscribe("plan", lambda: calls.append("second"))

    bus.publish("plan")
    unsubscribe_first()
    bus.publish("plan")

    assert calls == ["first", "second", "second"]


def test_runtime_store_rejects_invalid_plan_transition() -> None:
    store = RuntimeStateStore(AgentState(), session_id="session-a")

    with pytest.raises(InvalidStateTransitionError):
        store.dispatch(RuntimeAction("plan_executing"))

    assert store.get_state().plan.lifecycle == PlanApprovalStatus.NONE


def test_plan_todos_are_derived_and_runtime_todos_are_session_scoped() -> None:
    first_state = AgentState()
    first = RuntimeStateStore(first_state, session_id="session-a")
    second = RuntimeStateStore(AgentState(), session_id="session-b")
    first_state.set_plan(
        Plan(
            task="Ship",
            steps=[PlanStep("step_1", "Implement"), PlanStep("step_2", "Verify")],
        )
    )
    first_state.mark_step_running("step_1")
    first.replace_agent_todos(
        [{"id": "note", "content": "Keep compatibility", "status": "pending"}]
    )

    assert first.current_todos() == [
        {"id": "step_1", "content": "Implement", "status": "in_progress"},
        {"id": "step_2", "content": "Verify", "status": "pending"},
        {"id": "note", "content": "Keep compatibility", "status": "pending"},
    ]
    assert second.current_todos() == []


def test_stale_run_action_is_ignored() -> None:
    state = AgentState()
    store = RuntimeStateStore(state, session_id="session-a")
    state.reset("first")
    first_run_id = state.run_id
    state.reset("second")

    store.dispatch(
        RuntimeAction(
            "run_action_recorded",
            {"action": "late", "result": "stale"},
            expected_run_id=first_run_id,
        )
    )

    assert state.current_task == "second"
    assert state.last_action is None


def test_plan_file_merge_preserves_step_identity_and_completed_status() -> None:
    state = AgentState()
    store = RuntimeStateStore(state, session_id="session-a")
    original = Plan(
        task="Refactor",
        steps=[
            PlanStep("step_1", "Old description", status=StepStatus.DONE),
            PlanStep("step_2", "Second"),
        ],
    )
    state.set_plan(original)
    original_uid = state.current_plan.steps[0].uid
    incoming = Plan(
        task="Refactor",
        steps=[
            PlanStep("step_1", "Updated description", uid=original_uid),
            PlanStep("step_8", "Inserted"),
            PlanStep("step_9", "Second"),
        ],
    )

    store.dispatch(
        RuntimeAction(
            "plan_file_changed",
            {"plan": incoming, "file_hash": "new"},
            expected_plan_revision=state.plan_revision,
        )
    )

    assert [step.id for step in state.current_plan.steps] == ["step_1", "step_2", "step_3"]
    assert state.current_plan.steps[0].uid == original_uid
    assert state.current_plan.steps[0].status == StepStatus.DONE
    assert state.current_plan.steps[0].description == "Updated description"


def test_runtime_store_v2_round_trip_restores_plan_and_agent_todos(tmp_path: Path) -> None:
    state = AgentState()
    store = RuntimeStateStore(state, session_id="session-a")
    state.set_plan(Plan(task="Persist", steps=[PlanStep("step_1", "Write state")]))
    state.set_plan_file_path(tmp_path / "plan.md", "hash")
    state.mark_plan_awaiting_approval()
    store.replace_agent_todos([{"id": "note", "content": "Remember", "status": "pending"}])
    payload = store.serialize()

    restored_state = AgentState()
    restored = RuntimeStateStore(restored_state, session_id="other")
    restored.restore(payload)

    assert restored_state.current_plan.task == "Persist"
    assert restored_state.plan_approval_status == PlanApprovalStatus.AWAITING_APPROVAL
    assert restored.current_todos()[-1] == {
        "id": "note",
        "content": "Remember",
        "status": "pending",
    }
