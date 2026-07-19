"""Context workbench and per-turn activity presentation tests."""

from __future__ import annotations

from types import SimpleNamespace

from opennova.cli.tool_cards import ToolCardStore
from opennova.cli.tui_activity import TurnActivityAccumulator
from opennova.cli.tui_workbench import build_workbench_panel_state, snapshot_tasks
from opennova.memory.context import ContextManager
from opennova.memory.working import WorkingMemory
from opennova.providers.base import Message
from opennova.runtime.state import Plan, PlanStep, StepStatus


def test_context_presentation_snapshot_exposes_budget_and_compression():
    context = ContextManager(model="test", context_window=10_000)
    context.compression_threshold = 0.6
    context.set_system_prompt("system instructions")
    context.add_message(Message(role="user", content="hello world"))
    context.set_compressed_summary("earlier summary")

    snapshot = context.get_presentation_snapshot()

    assert snapshot.total_messages == 1
    assert snapshot.total_tokens > 0
    assert snapshot.context_window == 10_000
    assert snapshot.compression_count == 1
    assert snapshot.has_compressed_summary is True
    assert snapshot.compression_threshold_percent == 60.0


def test_context_workbench_aggregates_task_files_decisions_and_sources():
    context = ContextManager(model="test", context_window=10_000)
    context.set_system_prompt("system")
    context.add_message(Message(role="user", content="Improve the TUI"))
    working = WorkingMemory("Improve the TUI")
    working.start_task()
    working.observe_file("src/opennova/cli/tui.py", "read")
    working.observe_file("src/opennova/cli/tui.py", "modified")
    working.observe_file("tests/test_tui_blocks.py", "read")
    working.add_decision("Keep transcript schema compatible")
    plan = Plan(
        task="Improve the TUI",
        steps=[
            PlanStep("step_1", "Inspect context", status=StepStatus.DONE),
            PlanStep("step_2", "Render context", status=StepStatus.RUNNING),
        ],
    )
    state = SimpleNamespace(
        current_plan=plan,
        current_task="Improve the TUI",
        plan_approval_status=SimpleNamespace(value="executing"),
        plan_file_path=None,
    )
    agent = SimpleNamespace(
        context_manager=context,
        working_memory=working,
        state=state,
        state_store=None,
        project_memory=object(),
    )

    panel = build_workbench_panel_state(
        agent=agent,
        tool_cards=ToolCardStore(),
        active_tab="context",
    )

    assert panel.context is not None
    assert panel.context.task == "Improve the TUI"
    assert panel.context.current_step.startswith("step_2")
    assert panel.context.active_files[0].path == "tests/test_tui_blocks.py"
    assert panel.context.active_files[1].activity == "modified"
    assert panel.context.recent_decisions == ("Keep transcript schema compatible",)
    assert "system instructions" in panel.context.sources
    assert "plan · 2 steps" in panel.context.sources


def test_task_snapshot_prefers_plan_and_computes_progress():
    plan = SimpleNamespace(
        steps=[
            SimpleNamespace(id="step_1", status="done", description="Inspect"),
            SimpleNamespace(id="step_2", status="running", description="Implement"),
            SimpleNamespace(id="step_3", status="pending", description="Verify"),
        ]
    )

    tasks = snapshot_tasks(
        plan,
        [{"id": "step_1", "status": "done", "source": "plan"}],
    )

    assert tasks.completed == 1
    assert tasks.total == 3
    assert tasks.current_item == "Implement"
    assert dict(tasks.status_counts) == {"done": 1, "pending": 1, "running": 1}


def test_task_snapshot_merges_agent_todos_without_duplicating_plan_todos():
    plan = SimpleNamespace(
        steps=[SimpleNamespace(id="step_1", status="done", description="Inspect")]
    )
    todos = [
        {"id": "step_1", "content": "Inspect", "status": "done", "source": "plan"},
        {"id": "agent_1", "content": "Write release note", "status": "pending"},
    ]

    tasks = snapshot_tasks(plan, todos)

    assert tasks.total == 2
    assert dict(tasks.status_counts) == {"done": 1, "pending": 1}


def test_turn_activity_accumulator_counts_successful_changes_files_and_failures():
    activity = TurnActivityAccumulator()
    activity.apply_event(
        {
            "type": "tool_start",
            "tool_id": "tool_1",
            "tool_name": "read_file",
            "arguments": {"file_path": "README.md"},
        }
    )
    activity.apply_event(
        {
            "type": "tool_result",
            "tool_id": "tool_1",
            "tool_name": "read_file",
            "success": True,
            "duration_ms": 12,
        }
    )
    activity.apply_event(
        {
            "type": "tool_start",
            "tool_id": "tool_2",
            "tool_name": "edit_file",
            "arguments": {"file_path": "src/app.py"},
        }
    )
    activity.apply_event(
        {
            "type": "tool_error",
            "tool_id": "tool_2",
            "tool_name": "edit_file",
            "success": False,
            "duration_ms": 8,
        }
    )

    summary = activity.snapshot()

    assert summary.tool_count == 2
    assert summary.file_count == 2
    assert summary.change_count == 0
    assert summary.failed_count == 1
    assert summary.duration_ms == 20
    assert summary.status == "failed"


def test_turn_activity_counts_only_successful_file_mutations_as_changes():
    activity = TurnActivityAccumulator()
    activity.apply_event(
        {
            "type": "tool_start",
            "tool_id": "tool_1",
            "tool_name": "edit_file",
            "arguments": {"file_path": "src/app.py"},
        }
    )
    activity.apply_event(
        {
            "type": "tool_result",
            "tool_id": "tool_1",
            "tool_name": "edit_file",
            "success": True,
        }
    )

    assert activity.snapshot().change_count == 1


def test_turn_activity_transcript_events_are_grouped_without_schema_changes():
    activity = TurnActivityAccumulator()
    activity.apply_transcript_event(
        {"kind": "tool_start", "tool_name": "execute_command", "detail": "tool_1"}
    )
    activity.apply_transcript_event(
        {
            "kind": "tool_result",
            "tool_name": "execute_command",
            "summary_markup": "[green]Result:[/green] execute_command in 24ms",
            "error": "",
        }
    )

    summary = activity.consume()

    assert summary.tool_count == 1
    assert summary.failed_count == 0
    assert summary.duration_ms == 24
    assert activity.snapshot().tool_count == 0
