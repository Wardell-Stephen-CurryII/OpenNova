"""Structured render blocks for the OpenNova Textual TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from opennova.cli.tool_cards import ToolCardPanelState
from opennova.cli.tui_activity import TurnActivitySummary
from opennova.cli.tui_workbench import (
    ContextWorkbenchSnapshot,
    TaskWorkbenchSnapshot,
    WorkbenchPanelState,
    normalize_workbench_tab,
    snapshot_tasks,
)

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


def render_turn_activity_summary(summary: TurnActivitySummary) -> Panel:
    """Render one compact activity line for an entire conversational turn."""
    status_style = {
        "done": TUI_THEME.success,
        "waiting": TUI_THEME.warning,
        "failed": TUI_THEME.error,
    }[summary.status]
    body = Text()
    body.append("Activity", style=f"bold {TUI_THEME.accent}")
    body.append(f"  ·  {summary.tool_count} tool(s)")
    if summary.file_count:
        body.append(f"  ·  {summary.file_count} file(s)")
    if summary.change_count:
        body.append(f"  ·  {summary.change_count} change(s)", style=TUI_THEME.warning)
    if summary.failed_count:
        body.append(f"  ·  {summary.failed_count} failed", style=TUI_THEME.error)
    if summary.waiting_count:
        body.append(f"  ·  {summary.waiting_count} waiting", style=TUI_THEME.warning)
    body.append(f"  ·  {_format_duration(summary.duration_ms)}", style="dim")
    return Panel(body, border_style=status_style, padding=(0, 1))


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


def render_workbench_panel(state: WorkbenchPanelState) -> list[Any]:
    """Render the right-side Context / Tasks / Activity workbench panel."""
    active_tab = normalize_workbench_tab(state.active_tab)
    renderables: list[Any] = [_render_workbench_tabs(state)]
    if active_tab == "context":
        renderables.extend(_render_workbench_context(state.context))
    elif active_tab == "tasks":
        renderables.extend(
            _render_workbench_tasks(state.tasks or snapshot_tasks(state.plan, state.todos))
        )
    else:
        renderables.extend(render_tool_detail_panel(state.tools))
    renderables.append(
        Panel(
            Text(state.key_hint, style="dim"),
            title="Keys",
            border_style=TUI_THEME.panel_border,
            padding=(0, 1),
        )
    )
    return renderables


def _render_workbench_tabs(state: WorkbenchPanelState) -> Panel:
    context_usage = int((state.context or ContextWorkbenchSnapshot()).utilization_percent)
    tasks = state.tasks
    task_progress = f" {tasks.completed}/{tasks.total}" if tasks and tasks.total else ""
    activity_count = len(state.tools.cards)
    activity_alert = any(
        card.status in {"failed", "waiting_for_permission"} for card in state.tools.cards
    )
    labels = [
        ("context", f"Context {context_usage}%"),
        ("tasks", f"Tasks{task_progress}"),
        ("activity", f"Activity {activity_count}{' !' if activity_alert else ''}"),
    ]
    active_tab = normalize_workbench_tab(state.active_tab)
    text = Text()
    for index, (tab, label) in enumerate(labels):
        if index:
            text.append("  ")
        if active_tab == tab:
            text.append(f"[ {label} ]", style=f"bold {TUI_THEME.accent}")
        else:
            text.append(label, style="dim")
    return Panel(text, title="Workbench", border_style=TUI_THEME.accent_soft, padding=(0, 1))


def _render_workbench_context(
    context: ContextWorkbenchSnapshot | None,
) -> list[Any]:
    context = context or ContextWorkbenchSnapshot()
    now = Text()
    now.append(context.task or "No active task.", style=f"bold {TUI_THEME.accent}")
    now.append("\n")
    now.append(f"phase: {context.run_phase or 'idle'}", style="dim")
    if context.current_step:
        now.append(f"\nstep: {context.current_step}", style=TUI_THEME.warning)

    usage_style = _context_usage_style(
        context.utilization_percent,
        context.compression_threshold_percent,
    )
    budget = Text()
    budget.append(
        _progress_bar(context.utilization_percent),
        style=usage_style,
    )
    budget.append(f"  {context.utilization_percent:.1f}%\n", style=usage_style)
    budget.append(
        f"{_compact_number(context.total_tokens)} / "
        f"{_compact_number(context.context_window)} tokens  "
        f"· {context.total_messages} messages",
        style="dim",
    )
    if context.compression_count:
        budget.append(
            f"\n{context.compression_count} compression(s) · earlier context summarized",
            style=TUI_THEME.warning,
        )

    files = Text()
    if context.active_files:
        for index, item in enumerate(context.active_files):
            if index:
                files.append("\n")
            files.append(f"{item.activity:<9} ", style=_file_activity_style(item.activity))
            files.append(item.path)
    else:
        files.append("No files observed in this task.", style="dim")

    decisions = Text()
    if context.recent_decisions:
        for index, decision in enumerate(context.recent_decisions):
            if index:
                decisions.append("\n")
            decisions.append("· ", style=TUI_THEME.accent)
            decisions.append(decision)
    else:
        decisions.append("No explicit decisions recorded.", style="dim")

    sources = Text()
    if context.sources:
        for index, source in enumerate(context.sources):
            if index:
                sources.append("\n")
            sources.append(source, style="dim")
    else:
        sources.append("No context sources recorded.", style="dim")

    return [
        Panel(now, title="Now", border_style=TUI_THEME.accent_soft, padding=(0, 1)),
        Panel(budget, title="Context Budget", border_style=usage_style, padding=(0, 1)),
        Panel(files, title="Active Files", border_style=TUI_THEME.panel_border, padding=(0, 1)),
        Panel(
            decisions,
            title="Recent Decisions",
            border_style=TUI_THEME.panel_border,
            padding=(0, 1),
        ),
        Panel(sources, title="Sources", border_style=TUI_THEME.panel_border, padding=(0, 1)),
    ]


def _render_workbench_tasks(tasks: TaskWorkbenchSnapshot | None) -> list[Any]:
    if tasks is None or not tasks.total:
        return [
            Panel(
                Text("No active tasks.", style="dim"),
                title="Tasks",
                border_style=TUI_THEME.panel_border,
                padding=(0, 1),
            )
        ]

    progress = Text()
    percent = (tasks.completed / tasks.total) * 100 if tasks.total else 0.0
    progress.append(_progress_bar(percent), style=TUI_THEME.success)
    progress.append(f"  {tasks.completed}/{tasks.total} complete\n")
    counts = " · ".join(f"{status} {count}" for status, count in tasks.status_counts)
    progress.append(counts, style="dim")
    if tasks.current_item:
        progress.append(f"\ncurrent: {tasks.current_item}", style=TUI_THEME.warning)

    renderables: list[Any] = [
        Panel(progress, title="Progress", border_style=TUI_THEME.accent_soft, padding=(0, 1))
    ]
    if tasks.plan is not None:
        state = WorkbenchPanelState(
            active_tab="tasks",
            tools=ToolCardPanelState(cards=[], selected_tool_id=None, actions={}),
            plan=tasks.plan,
            todos=list(tasks.todos),
        )
        renderables.extend(_render_workbench_plan(state))
        plan_ids = {step.id for step in tasks.plan.steps}
        agent_todos = [
            todo
            for todo in tasks.todos
            if todo.get("source") != "plan" and str(todo.get("id", "")) not in plan_ids
        ]
        if agent_todos:
            state.todos = agent_todos
            renderables.extend(_render_workbench_todos(state))
    elif tasks.todos:
        state = WorkbenchPanelState(
            active_tab="tasks",
            tools=ToolCardPanelState(cards=[], selected_tool_id=None, actions={}),
            plan=None,
            todos=list(tasks.todos),
        )
        renderables.extend(_render_workbench_todos(state))
    return renderables


def _render_workbench_plan(state: WorkbenchPanelState) -> list[Any]:
    plan = state.plan
    if plan is None:
        return [
            Panel(
                Text("No active plan.", style="dim"),
                title="Plan",
                border_style=TUI_THEME.panel_border,
                padding=(0, 1),
            )
        ]

    summary = Text()
    summary.append(f"{plan.task}\n", style=f"bold {TUI_THEME.accent}")
    summary.append(f"status: {plan.status}  approval: {plan.approval_status}\n", style="dim")
    if plan.plan_file_path:
        summary.append(f"file: {plan.plan_file_path}\n", style="dim")

    steps = Text()
    if not plan.steps:
        steps.append("No plan steps.", style="dim")
    for step in plan.steps:
        style = _status_style(step.status)
        steps.append(f"{step.id} ", style=f"bold {style}")
        steps.append(f"{step.status} ", style=style)
        steps.append(step.description)
        if step.result_summary:
            steps.append(f"\n  result: {step.result_summary}", style="dim")
        if step.error:
            steps.append(f"\n  error: {step.error}", style=TUI_THEME.error)
        steps.append("\n")

    return [
        Panel(summary, title="Plan", border_style=TUI_THEME.panel_border, padding=(0, 1)),
        Panel(steps, title="Steps", border_style=TUI_THEME.accent_soft, padding=(0, 1)),
    ]


def _render_workbench_todos(state: WorkbenchPanelState) -> list[Any]:
    todos = state.todos
    if not todos:
        return [
            Panel(
                Text("No todos recorded.", style="dim"),
                title="Todos",
                border_style=TUI_THEME.panel_border,
                padding=(0, 1),
            )
        ]

    counts: dict[str, int] = {}
    body = Text()
    for todo in todos:
        status = str(todo.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1
        body.append(f"{todo.get('id', '')} ", style=f"bold {_status_style(status)}")
        body.append(f"{status} ", style=_status_style(status))
        body.append(str(todo.get("content", "")))
        body.append("\n")
    count_text = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
    body.append(f"\n{len(todos)} todo(s) - {count_text}", style="dim")
    return [Panel(body, title="Todos", border_style=TUI_THEME.accent_soft, padding=(0, 1))]


def _status_style(status: str) -> str:
    if status in {"done", "succeeded"}:
        return TUI_THEME.success
    if status in {"failed", "cancelled"}:
        return TUI_THEME.error
    if status in {"running", "in_progress", "executing", "interrupted"}:
        return TUI_THEME.warning
    return TUI_THEME.muted


def _context_usage_style(utilization: float, threshold: float) -> str:
    if utilization >= max(85.0, threshold + 20.0):
        return TUI_THEME.error
    if utilization >= threshold:
        return TUI_THEME.warning
    return TUI_THEME.success


def _progress_bar(percent: float, width: int = 18) -> str:
    bounded = min(100.0, max(0.0, percent))
    filled = round((bounded / 100.0) * width)
    return f"[{'=' * filled}{'-' * (width - filled)}]"


def _compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _file_activity_style(activity: str) -> str:
    if activity in {"modified", "created", "deleted"}:
        return TUI_THEME.warning
    return TUI_THEME.muted


def _format_duration(duration_ms: float) -> str:
    if duration_ms >= 1000:
        return f"{duration_ms / 1000:.1f}s"
    return f"{duration_ms:.0f}ms"


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
    body.append(" workbench")
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
    permission_mode: str = "auto",
    phase: str = "idle",
    current_step: str = "",
    context_utilization: float = 0.0,
    elapsed_seconds: float = 0.0,
) -> str:
    """Render a stable one-line workspace status bar."""
    del session_id, model
    workbench = "workbench:on" if tool_panel_visible else "workbench:off"
    context_style = _context_usage_style(context_utilization, 55.0)
    parts = [
        f"[dim]phase[/dim] {phase or 'idle'}",
    ]
    if current_step:
        parts.append(f"[dim]step[/dim] {current_step}")
    parts.extend(
        [
            f"[dim]context[/dim] [{context_style}]{context_utilization:.0f}%[/{context_style}]",
            f"[dim]permissions[/dim] {permission_mode}",
            f"[dim]{workbench}[/dim]",
        ]
    )
    if elapsed_seconds > 0:
        minutes, seconds = divmod(int(elapsed_seconds), 60)
        parts.append(f"[dim]{minutes:02d}:{seconds:02d}[/dim]")
    if message:
        parts.append(message)
    return "  ".join(parts)


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
