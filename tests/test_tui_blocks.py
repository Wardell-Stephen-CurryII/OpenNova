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
