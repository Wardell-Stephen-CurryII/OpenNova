from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from opennova.memory.context import ContextManager
from opennova.providers.base import Message
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.state import AgentState, Plan, PlanApprovalStatus, PlanStep, StepStatus
from opennova.runtime.store import (
    RuntimeAction,
    RuntimeEvent,
    RuntimeStateStore,
    TodoItem,
)
from opennova.session import LoadedSession, SessionManager


def test_runtime_action_codec_round_trips_structured_values(tmp_path: Path) -> None:
    plan = Plan(task="Codec", steps=[PlanStep("step_1", "Persist")])
    action = RuntimeAction(
        "codec_test",
        {
            "plan": plan,
            "path": tmp_path / "plan.md",
            "todos": (TodoItem("1", "Check"),),
            "status": StepStatus.INTERRUPTED,
        },
        expected_run_id="run-1",
        expected_plan_revision=3,
        expected_session_id="session-1",
    )

    restored = RuntimeAction.from_dict(action.to_dict())

    assert restored.type == "codec_test"
    assert restored.payload["plan"].steps[0].uid == plan.steps[0].uid
    assert restored.payload["path"] == tmp_path / "plan.md"
    assert restored.payload["todos"][0].content == "Check"
    assert restored.payload["status"] == StepStatus.INTERRUPTED
    assert restored.expected_plan_revision == 3


def test_runtime_store_replays_events_after_snapshot_once() -> None:
    source_state = AgentState()
    source = RuntimeStateStore(source_state, session_id="session-1")
    source_state.reset("Replay")
    snapshot_payload = source.serialize()
    source.transaction(
        [
            RuntimeAction("run_iteration_incremented"),
            RuntimeAction("run_action_recorded", {"action": "read_file", "result": "ok"}),
        ]
    )
    event = source.recent_events()[-1]

    target_state = AgentState()
    target = RuntimeStateStore(target_state, session_id="session-1")
    target.restore(snapshot_payload)
    notifications: list[int] = []
    target.subscribe(
        lambda snapshot: snapshot.revision,
        lambda selected, change: notifications.append(selected),
    )
    replayed = target.replay([event.to_dict()])

    assert replayed.warnings == ()
    assert target_state.iteration == 1
    assert target_state.last_action == "read_file"
    assert target_state.last_result == "ok"
    assert notifications == [event.revision]


def test_runtime_store_stops_replay_at_revision_gap() -> None:
    state = AgentState()
    store = RuntimeStateStore(state, session_id="session-1")
    state.reset("Gap")
    current_revision = store.get_state().revision
    event = RuntimeEvent(
        event_id="gap-event",
        revision=current_revision + 2,
        timestamp="2026-01-01T00:00:00+00:00",
        session_id="session-1",
        run_id=state.run_id,
        plan_revision=0,
        actions=(RuntimeAction("run_iteration_incremented"),),
    )

    result = store.replay([event])

    assert result.last_valid_revision == current_revision
    assert "revision gap" in result.warnings[0]
    assert state.iteration == 0


def test_session_v2_appends_events_and_compacts_into_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project), persistence_config={"debounce_ms": 60_000})
    session_id = manager.start_session()
    state = AgentState()
    store = RuntimeStateStore(state, session_id=session_id)
    state.reset("Persist")
    first_event = store.recent_events()[-1]

    manager.append_runtime_event(first_event, durable=True)
    loaded_before_snapshot = manager.load_session_with_summary(session_id)
    manager.save_runtime_snapshot(
        [Message(role="user", content="Persist")],
        runtime_state=store.serialize(),
    )
    loaded_after_snapshot = manager.load_session_with_summary(session_id)

    assert loaded_before_snapshot.schema_version == 2
    assert loaded_before_snapshot.state_events[0]["event_id"] == first_event.event_id
    assert loaded_after_snapshot.runtime_state["revision"] == first_event.revision
    assert loaded_after_snapshot.state_events == []
    session_file = manager._sessions_dir / f"{session_id}.jsonl"
    entries = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["type"] == "session_header"
    assert any(entry["type"] == "runtime_snapshot" for entry in entries)
    assert not list(session_file.parent.glob(f".{session_file.name}.*.tmp"))


def test_session_v2_debounces_noncritical_events_until_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project), persistence_config={"debounce_ms": 60_000})
    session_id = manager.start_session()
    state = AgentState()
    store = RuntimeStateStore(state, session_id=session_id)
    state.reset("Debounce")
    event = store.recent_events()[-1]

    manager.append_runtime_event(event, durable=False)
    session_file = manager._sessions_dir / f"{session_id}.jsonl"
    assert not session_file.exists()

    manager.flush_runtime_events()

    assert session_file.exists()
    assert manager.load_session_with_summary(session_id).state_events


def test_session_v2_reports_truncated_tail_and_replays_valid_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project))
    session_id = manager.start_session()
    state = AgentState()
    store = RuntimeStateStore(state, session_id=session_id)
    state.reset("Recover")
    event = store.recent_events()[-1]
    manager.append_runtime_event(event, durable=True)
    session_file = manager._sessions_dir / f"{session_id}.jsonl"
    with open(session_file, "a", encoding="utf-8") as stream:
        stream.write('{"type":"runtime_event"')

    loaded = manager.load_session_with_summary(session_id)

    assert loaded.state_events[0]["event_id"] == event.event_id
    assert "truncated session tail" in loaded.recovery_warnings[0]


def test_session_v2_stops_loading_runtime_events_after_middle_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project))
    session_id = manager.start_session()
    state = AgentState()
    store = RuntimeStateStore(state, session_id=session_id)
    state.reset("Recover")
    first_event = store.recent_events()[-1]
    manager.append_runtime_event(first_event, durable=True)
    session_file = manager._sessions_dir / f"{session_id}.jsonl"
    with open(session_file, "a", encoding="utf-8") as stream:
        stream.write('{"type":"runtime_event"\n')
    state.increment_iteration(state.run_id)
    manager.append_runtime_event(store.recent_events()[-1], durable=True)

    loaded = manager.load_session_with_summary(session_id)

    assert [event["event_id"] for event in loaded.state_events] == [first_event.event_id]
    assert "Corrupt session entry" in loaded.recovery_warnings[0]


def test_v1_session_is_upgraded_on_first_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project))
    session_id = manager.start_session()
    session_file = manager._sessions_dir / f"{session_id}.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "message",
                "session_id": session_id,
                "message": {"role": "user", "content": "legacy"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manager.resume_session(session_id)

    manager.save_message(Message(role="assistant", content="upgraded"))

    entries = [json.loads(line) for line in session_file.read_text().splitlines()]
    headers = [entry for entry in entries if entry.get("type") == "session_header"]
    assert len(headers) == 1
    assert headers[0]["schema_version"] == 2


def test_session_v2_rejects_newer_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project))
    session_id = manager.start_session()
    session_file = manager._sessions_dir / f"{session_id}.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "session_header",
                "schema_version": 99,
                "session_id": session_id,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="newer than supported"):
        manager.load_session_with_summary(session_id)


def test_runtime_resume_marks_running_plan_as_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project))
    session_id = manager.start_session()
    source_state = AgentState()
    source = RuntimeStateStore(source_state, session_id=session_id)
    source_state.reset("Interrupted work")
    source_state.set_plan(Plan(task="Resume", steps=[PlanStep("step_1", "Continue")]))
    source_state.mark_plan_awaiting_approval()
    source_state.mark_plan_approved()
    source_state.mark_plan_executing()
    source_state.mark_step_running("step_1")
    manager.save_runtime_snapshot([], runtime_state=source.serialize())

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.context_manager = ContextManager(model="gpt-4o")
    runtime.session_manager = manager
    runtime.session_transcript = []
    runtime.state = AgentState()
    runtime.state_store = RuntimeStateStore(runtime.state, session_id="temporary")
    runtime._state_persistence_ready = False
    runtime._emit = lambda *args, **kwargs: None

    loaded = AgentRuntime.resume_session(runtime, session_id)

    assert loaded.schema_version == 2
    assert runtime.state.plan_approval_status == PlanApprovalStatus.INTERRUPTED
    assert runtime.state.current_plan.steps[0].status == StepStatus.INTERRUPTED
    assert runtime.state_store.get_state().run.phase == "interrupted"


def test_tui_resume_interrupted_plan_prompts_and_keeps_revision_mode() -> None:
    from opennova.cli.tui import OpenNovaTUI

    state = AgentState()
    store = RuntimeStateStore(state, session_id="session-1")
    state.reset("Interrupted")
    state.set_plan(Plan(task="Resume", steps=[PlanStep("step_1", "Continue")]))
    state.mark_plan_awaiting_approval()
    state.mark_plan_approved()
    state.mark_plan_executing()
    state.mark_step_running("step_1")
    store.dispatch(RuntimeAction("session_interrupted_recovered"))
    writes: list[str] = []

    class Log:
        def write(self, value: Any) -> None:
            writes.append(str(value))

    class Agent:
        def __init__(self) -> None:
            self.state = state
            self.state_store = store

        def resume_session(self, session_id: str) -> LoadedSession:
            return LoadedSession(session_id=session_id, messages=[], transcript_events=[])

    async def choose_revision(_self, _user_message: str) -> str:
        return "revise"

    app = type(
        "TUI",
        (),
        {
            "agent": Agent(),
            "_workbench_tab": "tools",
            "query_one": lambda self, selector: Log(),
            "_restore_loaded_session": lambda self, log, loaded: None,
            "_ask_plan_decision_dialog": choose_revision,
            "_refresh_workbench_panel": lambda self: None,
        },
    )()

    resumed = asyncio.run(OpenNovaTUI._resume_session_by_id(app, "session-1"))

    assert resumed is True
    assert state.plan_approval_status == PlanApprovalStatus.DRAFT
    assert any("kept for revision" in item for item in writes)
