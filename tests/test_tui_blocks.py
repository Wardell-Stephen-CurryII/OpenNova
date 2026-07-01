"""Tests for structured Textual TUI conversation block renderers."""

from __future__ import annotations

from typing import Any

from rich.console import Console


def _plain(renderable: Any) -> str:
    console = Console(no_color=True, force_terminal=False, width=100)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def _plain_many(renderables: list[Any]) -> str:
    return "\n".join(_plain(renderable) for renderable in renderables)


def test_user_block_contains_user_text():
    from opennova.cli.tui_blocks import render_user_block

    text = _plain(render_user_block("请读取 README.md"))

    assert "You" in text
    assert "请读取 README.md" in text


def test_assistant_block_preserves_markdown_content():
    from opennova.cli.tui_blocks import render_assistant_block

    text = _plain(render_assistant_block("结果是 **通过**。"))

    assert "结果是" in text
    assert "通过" in text


def test_tool_start_block_contains_tool_name_and_detail():
    from opennova.cli.tui_blocks import render_tool_start_block

    text = _plain(render_tool_start_block("read_file", "README.md"))

    assert "tool" in text
    assert "read_file" in text
    assert "README.md" in text


def test_read_and_list_tool_results_hide_raw_output():
    from opennova.cli.tui_blocks import render_tool_result_block

    read_text = _plain_many(
        render_tool_result_block(
            tool_name="read_file",
            summary_markup="[green]Result:[/green] read_file succeeded",
            output="1: SECRET_FILE_CONTENT",
        )
    )
    list_text = _plain_many(
        render_tool_result_block(
            tool_name="list_directory",
            summary_markup="[green]Result:[/green] list_directory succeeded",
            output="private.py\nhidden.env",
        )
    )

    assert "read_file" in read_text
    assert "SECRET_FILE_CONTENT" not in read_text
    assert "list_directory" in list_text
    assert "hidden.env" not in list_text


def test_execute_command_output_is_limited_to_twenty_lines():
    from opennova.cli.tui_blocks import render_tool_result_block

    output = "\n".join(f"line {index}" for index in range(1, 26))
    text = _plain_many(
        render_tool_result_block(
            tool_name="execute_command",
            summary_markup="[green]Result:[/green] execute_command succeeded",
            output=output,
        )
    )

    assert "line 1" in text
    assert "line 20" in text
    assert "line 21" not in text
    assert "collapsed" in text


def test_error_result_contains_error_and_failed_state():
    from opennova.cli.tui_blocks import render_tool_result_block

    text = _plain_many(
        render_tool_result_block(
            tool_name="write_file",
            summary_markup="[red]Result:[/red] write_file failed",
            error="Permission denied",
        )
    )

    assert "write_file" in text
    assert "failed" in text
    assert "Permission denied" in text


def test_tool_detail_panel_renders_empty_state():
    from opennova.cli.tool_cards import ToolCardPanelState
    from opennova.cli.tui_blocks import render_tool_detail_panel

    text = _plain_many(
        render_tool_detail_panel(
            ToolCardPanelState(cards=[], selected_tool_id=None, actions={})
        )
    )

    assert "No tool activity" in text


def test_tool_detail_panel_renders_selected_card_preview_diff_and_error():
    from opennova.cli.tool_cards import ToolCardPanelState, ToolCardViewState
    from opennova.cli.tui_blocks import render_tool_detail_panel

    state = ToolCardPanelState(
        cards=[
            ToolCardViewState(
                tool_id="tool_1",
                tool_name="execute_command",
                status="failed",
                expanded=False,
                rendered="[failed] execute_command duration=42ms\npreview output",
                diff_panel="+ changed",
                approval_state="none",
            )
        ],
        selected_tool_id="tool_1",
        diff_panel="+ changed",
        actions={"toggle": True, "cancel": False, "approve": False},
    )

    text = _plain_many(render_tool_detail_panel(state))

    assert "Tool Details" in text
    assert "execute_command" in text
    assert "preview output" in text
    assert "+ changed" in text
    assert "alt+enter" in text


def test_tool_detail_panel_renders_expanded_full_output():
    from opennova.cli.tool_cards import ToolCardPanelState, ToolCardViewState
    from opennova.cli.tui_blocks import render_tool_detail_panel

    state = ToolCardPanelState(
        cards=[
            ToolCardViewState(
                tool_id="tool_1",
                tool_name="read_file",
                status="succeeded",
                expanded=True,
                rendered="[succeeded] read_file\nFULL SECRET CONTENT",
            )
        ],
        selected_tool_id="tool_1",
        actions={"toggle": True},
    )

    text = _plain_many(render_tool_detail_panel(state))

    assert "expanded" in text
    assert "FULL SECRET CONTENT" in text


def test_welcome_block_contains_workspace_context():
    from opennova.cli.tui_blocks import render_welcome_block

    text = _plain(
        render_welcome_block(
            version="0.3.0",
            provider="deepseek",
            model="deepseek-v4-pro",
            session_id="session-123456",
        )
    )

    assert "OpenNova" in text
    assert "0.3.0" in text
    assert "deepseek" in text
    assert "deepseek-v4-pro" in text
    assert "session-123456" in text
    assert "/help" in text
    assert "alt+t" in text


def test_status_bar_renderer_includes_session_model_and_message():
    from opennova.cli.tui_blocks import render_status_bar

    status = render_status_bar(
        session_id="session-abcdef",
        model="deepseek-v4-pro",
        message="Working on grep_code",
        tool_panel_visible=True,
    )

    assert "session-abcd" in status
    assert "deepseek-v4-pro" in status
    assert "Working on grep_code" in status
    assert "tools:on" in status


def test_blocks_share_calm_workspace_theme():
    from opennova.cli.tui_blocks import TUI_THEME

    assert TUI_THEME.background.startswith("#")
    assert TUI_THEME.panel_border.startswith("#")
    assert TUI_THEME.accent.startswith("#")
    assert TUI_THEME.error.startswith("#")


def test_workbench_panel_renders_tab_header_and_tools_tab():
    from opennova.cli.tool_cards import ToolCardPanelState, ToolCardViewState
    from opennova.cli.tui_blocks import render_workbench_panel
    from opennova.cli.tui_workbench import WorkbenchPanelState

    state = WorkbenchPanelState(
        active_tab="tools",
        tools=ToolCardPanelState(
            cards=[
                ToolCardViewState(
                    tool_id="tool_1",
                    tool_name="execute_command",
                    status="succeeded",
                    expanded=True,
                    rendered="[succeeded] execute_command\nfull output",
                )
            ],
            selected_tool_id="tool_1",
            actions={"toggle": True},
        ),
        plan=None,
        todos=[],
    )

    text = _plain_many(render_workbench_panel(state))

    assert "Tools" in text
    assert "Plan" in text
    assert "Todos" in text
    assert "execute_command" in text
    assert "full output" in text


def test_workbench_panel_renders_plan_tab_snapshot():
    from opennova.cli.tool_cards import ToolCardPanelState
    from opennova.cli.tui_blocks import render_workbench_panel
    from opennova.cli.tui_workbench import (
        PlanStepSnapshot,
        PlanWorkbenchSnapshot,
        WorkbenchPanelState,
    )

    state = WorkbenchPanelState(
        active_tab="plan",
        tools=ToolCardPanelState(cards=[], selected_tool_id=None, actions={}),
        plan=PlanWorkbenchSnapshot(
            task="Refine UI",
            status="executing",
            approval_status="executing",
            plan_file_path=".opennova/plan/plan.md",
            steps=[
                PlanStepSnapshot(
                    id="step_1",
                    description="Build side panel",
                    status="running",
                    result_summary="started",
                    error="",
                )
            ],
        ),
        todos=[],
    )

    text = _plain_many(render_workbench_panel(state))

    assert "Refine UI" in text
    assert "executing" in text
    assert ".opennova/plan/plan.md" in text
    assert "step_1" in text
    assert "Build side panel" in text
    assert "started" in text


def test_workbench_panel_renders_todos_tab_and_empty_state():
    from opennova.cli.tool_cards import ToolCardPanelState
    from opennova.cli.tui_blocks import render_workbench_panel
    from opennova.cli.tui_workbench import WorkbenchPanelState

    populated = WorkbenchPanelState(
        active_tab="todos",
        tools=ToolCardPanelState(cards=[], selected_tool_id=None, actions={}),
        plan=None,
        todos=[
            {"id": "1", "content": "Inspect TUI", "status": "done"},
            {"id": "2", "content": "Implement panel", "status": "in_progress"},
        ],
    )
    empty = WorkbenchPanelState(
        active_tab="todos",
        tools=ToolCardPanelState(cards=[], selected_tool_id=None, actions={}),
        plan=None,
        todos=[],
    )

    populated_text = _plain_many(render_workbench_panel(populated))
    empty_text = _plain_many(render_workbench_panel(empty))

    assert "2 todo" in populated_text
    assert "Inspect TUI" in populated_text
    assert "in_progress" in populated_text
    assert "No todos" in empty_text
