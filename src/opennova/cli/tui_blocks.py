"""Structured render blocks for the OpenNova Textual TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from opennova.cli.tool_cards import ToolCardPanelState

SUPPRESSED_RESULT_TOOLS = {"list_directory", "read_file"}
DEFAULT_MAX_OUTPUT_LINES = 20


@dataclass(frozen=True)
class TUITheme:
    """Calm workspace color tokens shared by TUI render blocks."""

    background: str = "#0f1419"
    surface: str = "#151b22"
    surface_soft: str = "#1b232c"
    panel_border: str = "#32414f"
    accent: str = "#74a6a6"
    accent_soft: str = "#3d5a5a"
    success: str = "#7aa874"
    error: str = "#b36a6a"
    warning: str = "#b59f6a"
    muted: str = "#7d8994"


TUI_THEME = TUITheme()


def render_user_block(text: str) -> Panel:
    """Render a user message as a distinct conversation block."""
    body = Text(text, style=TUI_THEME.accent)
    return Panel(
        body,
        title="You",
        title_align="right",
        border_style=TUI_THEME.accent_soft,
        padding=(0, 1),
    )


def render_assistant_block(markdown_text: str) -> Panel:
    """Render an assistant response as a calm markdown block."""
    return Panel(
        Markdown(markdown_text),
        title="OpenNova",
        title_align="left",
        border_style=TUI_THEME.panel_border,
        padding=(0, 1),
    )


def render_tool_start_block(tool_name: str, detail: str = "") -> Panel:
    """Render a compact tool start block."""
    suffix = f"  {detail}" if detail else ""
    header = Text.assemble(
        ("tool", "dim"),
        (" - ", "dim"),
        (tool_name, TUI_THEME.accent),
        (suffix, "dim"),
    )
    return Panel(header, border_style=TUI_THEME.panel_border, padding=(0, 1))


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
    border_style = TUI_THEME.error if status == "failed" else TUI_THEME.accent_soft
    header = Text.assemble(
        ("tool", "dim"),
        (" - ", "dim"),
        (tool_name, TUI_THEME.accent),
        (" - ", "dim"),
        (status, TUI_THEME.error if status == "failed" else TUI_THEME.success),
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
        renderables.append(Panel(Text(visible_output), title="output", border_style=TUI_THEME.panel_border))

    visible_diff = _limit_lines(diff, diff_max_lines, label="diff")
    if visible_diff:
        renderables.append(Panel(Text(visible_diff), title="diff", border_style=TUI_THEME.warning))

    if error:
        renderables.append(Panel(Text(error, style=TUI_THEME.error), title="error", border_style=TUI_THEME.error))

    return renderables


def render_tool_detail_panel(panel_state: ToolCardPanelState) -> list[Any]:
    """Render the session-scoped tool detail side panel."""
    if not panel_state.cards:
        return [
            Panel(
                Text("No tool activity yet.", style="dim"),
                title="Tool Details",
                border_style=TUI_THEME.panel_border,
                padding=(0, 1),
            )
        ]

    selected_id = panel_state.selected_tool_id
    selected = next(
        (card for card in panel_state.cards if card.tool_id == selected_id),
        panel_state.cards[0],
    )
    list_text = Text()
    for card in panel_state.cards:
        marker = ">" if card.tool_id == selected.tool_id else " "
        style = f"bold {TUI_THEME.accent}" if card.tool_id == selected.tool_id else "dim"
        list_text.append(f"{marker} {card.tool_id} ", style=style)
        list_text.append(f"{card.tool_name} ", style=style)
        list_text.append(f"{card.status}\n", style=style)

    state = "expanded" if selected.expanded else "collapsed"
    detail = Text()
    detail.append(f"{selected.tool_name} ({selected.tool_id})\n", style=f"bold {TUI_THEME.accent}")
    detail.append(f"status: {selected.status}  view: {state}\n", style="dim")
    detail.append("\n")
    detail.append(selected.rendered)

    renderables: list[Any] = [
        Panel(list_text, title="Tools", border_style=TUI_THEME.panel_border, padding=(0, 1)),
        Panel(detail, title="Tool Details", border_style=TUI_THEME.accent_soft, padding=(0, 1)),
    ]

    if selected.diff_panel:
        renderables.append(
            Panel(
                Text(_limit_lines(selected.diff_panel, DEFAULT_MAX_OUTPUT_LINES, label="diff")),
                title="diff",
                border_style=TUI_THEME.warning,
            )
        )

    help_text = Text()
    help_text.append("alt+j/k", style=TUI_THEME.accent)
    help_text.append(" select  ")
    help_text.append("alt+enter", style=TUI_THEME.accent)
    help_text.append(" expand/collapse  ")
    help_text.append("alt+t", style=TUI_THEME.accent)
    help_text.append(" hide")
    renderables.append(Panel(help_text, title="Keys", border_style=TUI_THEME.panel_border, padding=(0, 1)))
    return renderables


def render_welcome_block(
    *,
    version: str,
    provider: str,
    model: str,
    session_id: str,
) -> Panel:
    """Render the compact workspace welcome panel."""
    body = Text()
    body.append("OpenNova", style=f"bold {TUI_THEME.accent}")
    body.append(f"  v{version}\n", style="dim")
    body.append("AI coding workspace\n\n", style=TUI_THEME.muted)
    body.append("provider ", style="dim")
    body.append(provider, style=TUI_THEME.success)
    body.append("   model ", style="dim")
    body.append(model, style=TUI_THEME.warning)
    body.append("\n")
    body.append("session  ", style="dim")
    body.append(session_id, style=TUI_THEME.accent)
    body.append("\n\n")
    body.append("/help", style=TUI_THEME.accent)
    body.append(" commands   ")
    body.append("/resume", style=TUI_THEME.accent)
    body.append(" sessions   ")
    body.append("alt+t", style=TUI_THEME.accent)
    body.append(" tools")
    return Panel(
        body,
        title="Workspace",
        border_style=TUI_THEME.accent_soft,
        padding=(1, 2),
    )


def render_status_bar(
    *,
    session_id: str,
    model: str,
    message: str = "",
    tool_panel_visible: bool = False,
) -> str:
    """Render a stable one-line workspace status bar."""
    short_session = session_id[:12] if session_id else "no-session"
    tools = "tools:on" if tool_panel_visible else "tools:off"
    state = message or "idle"
    return (
        f"[dim]session[/dim] {short_session}  "
        f"[dim]model[/dim] {model or 'unknown'}  "
        f"[dim]{tools}[/dim]  {state}"
    )


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
