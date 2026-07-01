"""Structured render blocks for the OpenNova Textual TUI."""

from __future__ import annotations

from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

SUPPRESSED_RESULT_TOOLS = {"list_directory", "read_file"}
DEFAULT_MAX_OUTPUT_LINES = 20


def render_user_block(text: str) -> Panel:
    """Render a user message as a distinct conversation block."""
    body = Text(text, style="bright_cyan")
    return Panel(
        body,
        title="You",
        title_align="right",
        border_style="#2f8f9d",
        padding=(0, 1),
    )


def render_assistant_block(markdown_text: str) -> Panel:
    """Render an assistant response as a calm markdown block."""
    return Panel(
        Markdown(markdown_text),
        title="OpenNova",
        title_align="left",
        border_style="#3f5f4f",
        padding=(0, 1),
    )


def render_tool_start_block(tool_name: str, detail: str = "") -> Panel:
    """Render a compact tool start block."""
    suffix = f"  {detail}" if detail else ""
    header = Text.assemble(
        ("tool", "dim"),
        (" - ", "dim"),
        (tool_name, "cyan"),
        (suffix, "dim"),
    )
    return Panel(header, border_style="#405060", padding=(0, 1))


def render_tool_result_block(
    *,
    tool_name: str,
    summary_markup: str,
    output: str = "",
    error: str = "",
    diff: str = "",
    diff_max_lines: int = DEFAULT_MAX_OUTPUT_LINES,
    max_output_lines: int = DEFAULT_MAX_OUTPUT_LINES,
) -> list[Any]:
    """Render a compact tool result block and optional folded details."""
    status = _status_from_summary(summary_markup, error)
    border_style = "#604040" if status == "failed" else "#405844"
    header = Text.assemble(
        ("tool", "dim"),
        (" - ", "dim"),
        (tool_name, "cyan"),
        (" - ", "dim"),
        (status, "red" if status == "failed" else "green"),
    )
    summary = Text.from_markup(summary_markup)
    body = Text()
    body.append_text(header)
    body.append("\n")
    body.append_text(summary)

    if tool_name in SUPPRESSED_RESULT_TOOLS:
        body.append("\n")
        body.append("output hidden in chat transcript", style="dim")

    renderables: list[Any] = [Panel(body, border_style=border_style, padding=(0, 1))]

    visible_output = "" if tool_name in SUPPRESSED_RESULT_TOOLS else _limit_lines(output, max_output_lines)
    if visible_output:
        renderables.append(Panel(Text(visible_output), title="output", border_style="#303846"))

    visible_diff = _limit_lines(diff, diff_max_lines, label="diff")
    if visible_diff:
        renderables.append(Panel(Text(visible_diff), title="diff", border_style="#4c4a32"))

    if error:
        renderables.append(Panel(Text(error, style="red"), title="error", border_style="red"))

    return renderables


def _status_from_summary(summary_markup: str, error: str) -> str:
    text = Text.from_markup(summary_markup).plain.lower()
    if error or "failed" in text or "error" in text:
        return "failed"
    if "cancelled" in text or "canceled" in text:
        return "cancelled"
    return "done"


def _limit_lines(
    value: str,
    max_lines: int,
    *,
    label: str = "output",
) -> str:
    if not value:
        return ""
    lines = value.splitlines()
    if len(lines) <= max_lines:
        return value
    visible = lines[:max_lines]
    visible.append(f"... ({label} collapsed, {max_lines}/{len(lines)} lines shown)")
    return "\n".join(visible)
