"""Tests for TUI tool progress state formatting."""

from __future__ import annotations

from opennova.cli.tool_cards import ToolCardStore
from opennova.cli.tool_progress import ToolProgressTracker
from opennova.runtime.events import ToolEvent
from opennova.tools.base import ToolResult


def test_tool_progress_tracker_reports_running_tool_with_elapsed_time():
    tracker = ToolProgressTracker(clock=lambda: 10.0)

    event = tracker.start_tool("grep_code", {"pattern": "needle"})
    tracker.clock = lambda: 12.4

    assert event["tool_id"].startswith("tool_")
    assert event["started_at"] == 10.0
    assert tracker.current_tool_name == "grep_code"
    assert "grep_code" in tracker.status_text(frame="*")
    assert "2.4s" in tracker.status_text(frame="*")


def test_tool_progress_tracker_summarizes_result_and_clears_current_tool():
    tracker = ToolProgressTracker(clock=lambda: 1.0)
    tracker.start_tool("edit_file", {})
    tracker.clock = lambda: 2.5

    result = ToolResult(success=True, output="Edited file", metadata={"diff": "--- a\n+++ b"})
    event = tracker.finish_tool(result)

    assert "edit_file" in event["summary"]
    assert event["duration_ms"] == 1500
    assert event["diff"] == "--- a\n+++ b"
    assert event["collapsible"] is False
    assert tracker.current_tool_name == ""


def test_tool_progress_tracker_reports_permission_prompt():
    tracker = ToolProgressTracker(clock=lambda: 3.0)

    tracker.start_interaction({"questions": [{"header": "Confirm"}]})

    assert tracker.waiting_for_interaction is True
    assert "Confirm" in tracker.status_text(frame="!")


def test_tool_progress_tracker_marks_long_output_collapsible():
    tracker = ToolProgressTracker(clock=lambda: 1.0, collapse_threshold=10)
    tracker.start_tool("execute_command", {})

    event = tracker.finish_tool(ToolResult(success=True, output="x" * 20))

    assert event["collapsible"] is True
    assert event["output_preview"] == "xxxxxxxxxx\n... (output collapsed)"


def test_tool_card_store_selects_next_and_previous_stably():
    store = ToolCardStore(collapse_threshold=5)
    store.apply_event(ToolEvent(type="tool_start", tool_id="tool_1", tool_name="read_file"))
    store.apply_event(ToolEvent(type="tool_start", tool_id="tool_2", tool_name="execute_command"))

    assert store.interaction.selected_tool_id == "tool_1"
    assert store.select_next() == "tool_2"
    assert store.select_previous() == "tool_1"
    assert store.select_previous() == "tool_2"


def test_tool_card_store_expanded_view_uses_full_output():
    from opennova.cli.tool_cards import build_tool_card_panel

    store = ToolCardStore(collapse_threshold=5)
    store.apply_event(ToolEvent(type="tool_start", tool_id="tool_1", tool_name="execute_command"))
    store.apply_event(
        ToolEvent(
            type="tool_result",
            tool_id="tool_1",
            tool_name="execute_command",
            success=True,
            output="abcdefghi",
            duration_ms=42,
        )
    )

    collapsed = build_tool_card_panel(store).cards[0]
    assert "abcde" in collapsed.rendered
    assert "abcdefghi" not in collapsed.rendered

    store.toggle_expanded("tool_1")
    expanded = build_tool_card_panel(store).cards[0]
    assert "abcdefghi" in expanded.rendered


def test_workbench_non_tools_tab_does_not_toggle_tool_expansion():
    from opennova.cli.tui import OpenNovaTUI

    store = ToolCardStore(collapse_threshold=5)
    store.apply_event(ToolEvent(type="tool_start", tool_id="tool_1", tool_name="execute_command"))
    store.apply_event(
        ToolEvent(
            type="tool_result",
            tool_id="tool_1",
            tool_name="execute_command",
            success=True,
            output="abcdefghi",
        )
    )
    app = type(
        "FakeTUI",
        (),
        {
            "_tool_cards": store,
            "_workbench_tab": "plan",
            "_refresh_workbench_panel": lambda self: None,
        },
    )()

    OpenNovaTUI.action_tool_toggle_expanded(app)

    assert store.interaction.expanded_tool_ids == set()
