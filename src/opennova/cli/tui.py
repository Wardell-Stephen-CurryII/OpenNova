"""
Textual TUI for OpenNova — split-pane chat interface.

┌─────────────────────────────┐
│ Message List                │
│                             │
├─────────────────────────────┤
│ Input Box                   │
└─────────────────────────────┘
"""

import asyncio
import platform
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from rich.markdown import Markdown
from rich.segment import Segment
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.selection import Selection
from textual.widgets import Header, Input, Label, RichLog

from opennova.cli.ask_question_dialog import AskQuestionDialog
from opennova.cli.commands import SlashCommandRegistry
from opennova.cli.plan_decision_dialog import PlanDecision, PlanDecisionDialog
from opennova.cli.session_picker_dialog import SessionPickerDialog
from opennova.cli.tool_cards import ToolCardStore
from opennova.cli.tool_progress import ToolProgressTracker
from opennova.cli.tui_activity import TurnActivityAccumulator, TurnActivitySummary
from opennova.cli.tui_blocks import (
    TUI_THEME,
    render_assistant_block,
    render_status_bar,
    render_tool_result_block,
    render_tool_start_block,
    render_turn_activity_summary,
    render_user_block,
    render_welcome_block,
    render_workbench_panel,
)
from opennova.cli.tui_workbench import (
    PlanWorkbenchSnapshot,
    WorkbenchTab,
    build_workbench_panel_state,
    next_workbench_tab,
    normalize_workbench_tab,
    previous_workbench_tab,
    snapshot_plan,
)
from opennova.config import Config
from opennova.providers.base import StreamChunk
from opennova.runtime.agent import AgentRuntime
from opennova.session import LoadedSession, SessionMeta, format_session_title_snippet
from opennova.tools.base import ToolResult

# Tool names whose result outputs are not displayed (verbose file ops).
_SUPPRESSED_RESULT_TOOLS = {"list_directory", "read_file"}

# Tool names where the "Result:" label is shown but raw stdout is hidden.
_SUPPRESSED_RESULT_OUTPUT: set[str] = set()

# Parameter names whose values are hidden in the action display (too long/unreadable).
_REDACTED_ACTION_PARAMS = {"content"}

# Max tool output lines shown in-session; fallback is 20.
_MAX_OUTPUT_LINES = 20

# Max diff lines shown per tool; fallback is 20.
_MAX_DIFF_LINES: dict[str, int] = {}

_INPUT_PLACEHOLDER = "Ask OpenNova, or type / for commands..."
_WORKING_PLACEHOLDER = "OpenNova is working... Ctrl+C to cancel"
_MAC_OPTION_DIGIT_TABS = {
    "¡": "context",
    "™": "tasks",
    "£": "activity",
}

# 2a2a2a 001a1a
_USER_MESSAGE_STYLE = "bright_cyan on #001a1a"
_USER_MESSAGE_LABEL_STYLE = "bold bright_cyan on #001a1a"
_TOOL_ICON = "⏺"


def _format_user_message(text: str) -> Text:
    """Render a user input line with a subtle background."""
    message = Text("You: ", style=_USER_MESSAGE_LABEL_STYLE)
    message.append(text, style=_USER_MESSAGE_STYLE)
    return message


def _has_pending_plan_decision(state: Any) -> bool:
    """Return whether the TUI should ask how to handle the current plan."""
    if not getattr(state, "current_plan", None):
        return False
    approval_status = getattr(getattr(state, "plan_approval_status", None), "value", "")
    return approval_status in {
        "awaiting_approval",
        "approved",
        "executing",
        "failed",
        "interrupted",
    }


def _has_plan_revision_in_progress(state: Any) -> bool:
    """Return whether the next user turn should revise the retained plan."""
    if not getattr(state, "current_plan", None):
        return False
    approval_status = getattr(getattr(state, "plan_approval_status", None), "value", "")
    return approval_status == "draft"


def _format_tool_execution(tool_name: str, detail: str) -> str:
    """Render a tool execution line with a leading marker."""
    suffix = f" {detail}" if detail else ""
    return f"{_TOOL_ICON} [cyan]Executing:[/cyan] {tool_name}{suffix}"


def _truncate_tool_output(tool_name: str, output: str, max_lines: int = _MAX_OUTPUT_LINES) -> str:
    """Trim verbose tool output for the chat transcript."""
    if not output or tool_name in _SUPPRESSED_RESULT_TOOLS:
        return ""

    lines = output.splitlines()
    if len(lines) <= max_lines:
        return output

    visible = lines[:max_lines]
    visible.append(f"... (output truncated, {max_lines}/{len(lines)} lines)")
    return "\n".join(visible)


class _SelectableRichLog(RichLog):
    """RichLog with Textual screen-selection extraction and highlighting."""

    can_focus = False

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return text from the rendered log lines for Textual's screen selection."""
        text = "\n".join(line.text.rstrip() for line in self.lines)
        selected_text = selection.extract(text)
        if not selected_text:
            return None
        return selected_text, "\n"

    def render_line(self, y: int):
        scroll_x, scroll_y = self.scroll_offset
        content_y = scroll_y + y
        line = self._render_line(
            content_y,
            scroll_x,
            self.scrollable_content_region.width,
        )
        line = line.apply_offsets(scroll_x, content_y).apply_style(self.rich_style)

        if (selection := self.text_selection) is not None:
            span = selection.get_span(content_y)
            if span is not None:
                start, end = span
                visible_start = max(start - scroll_x, 0)
                visible_end = None if end == -1 else max(end - scroll_x, 0)
                line = _style_strip_range(
                    line,
                    visible_start,
                    visible_end,
                    self.selection_style,
                )
        return line


class _MessagesLog(_SelectableRichLog):
    """Selectable RichLog that stores plain text alongside rich renderables."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._plain_lines: list[str] = []

    def _is_following_tail(self) -> bool:
        """Return whether new messages should keep the log pinned to the bottom."""
        try:
            return bool(self.is_vertical_scroll_end)
        except Exception:
            return True

    def write(self, text: Any, *args: Any, **kwargs: Any) -> None:
        if len(args) < 4 and kwargs.get("scroll_end") is None:
            kwargs["scroll_end"] = self._is_following_tail()
        super().write(text, *args, **kwargs)
        self._plain_lines.append(_to_plain(text))

    def clear_messages(self) -> None:
        self.clear()
        self._plain_lines.clear()

    def get_plain_text(self) -> str:
        return "\n".join(self._plain_lines)


def _style_strip_range(
    line: Any,
    start: int,
    end: int | None,
    style: Style,
):
    """Apply a style to a character range in a rendered Strip."""
    if end is None:
        end = len(line.text)
    if end <= start:
        return line

    styled_segments: list[Segment] = []
    cursor = 0
    for segment in line:
        segment_text = segment.text
        segment_end = cursor + len(segment_text)
        overlap_start = max(start, cursor)
        overlap_end = min(end, segment_end)

        if overlap_start >= overlap_end:
            styled_segments.append(segment)
            cursor = segment_end
            continue

        local_start = overlap_start - cursor
        local_end = overlap_end - cursor
        if local_start:
            styled_segments.append(
                Segment(segment_text[:local_start], segment.style, segment.control)
            )

        selected_style = segment.style + style if segment.style is not None else style
        styled_segments.append(
            Segment(segment_text[local_start:local_end], selected_style, segment.control)
        )

        if local_end < len(segment_text):
            styled_segments.append(
                Segment(segment_text[local_end:], segment.style, segment.control)
            )
        cursor = segment_end

    return type(line)(styled_segments)


def _copy_to_system_clipboard(
    text: str,
    *,
    system_name: str | None = None,
    run: Any = None,
    which: Any = None,
) -> bool:
    """Copy text to the OS clipboard using native command-line tools."""
    if not text:
        return False

    system = system_name or platform.system()
    run = run or subprocess.run
    which = which or shutil.which

    try:
        if system == "Darwin":
            result = run(["pbcopy"], input=text, text=True, check=False)
            return getattr(result, "returncode", 1) == 0
        if system == "Windows":
            result = run(["clip"], input=text, text=True, check=False)
            return getattr(result, "returncode", 1) == 0
        if system == "Linux":
            if which("wl-copy"):
                result = run(["wl-copy"], input=text, text=True, check=False)
                return getattr(result, "returncode", 1) == 0
            if which("xclip"):
                result = run(
                    ["xclip", "-selection", "clipboard"],
                    input=text,
                    text=True,
                    check=False,
                )
                return getattr(result, "returncode", 1) == 0
    except Exception:
        return False
    return False


def _to_plain(text: Any) -> str:
    """Convert Rich renderables / markup strings to plain text (no ANSI codes)."""
    try:
        if isinstance(text, Text):
            return text.plain
        if hasattr(text, "__rich_console__") or hasattr(text, "__rich__"):
            from rich.console import Console as RichConsole

            console = RichConsole(no_color=True, width=120, force_terminal=False)
            with console.capture() as capture:
                console.print(text)
            import re

            # Strip any residual ANSI escape sequences
            return re.sub(r"\x1b\[[0-9;]*m", "", capture.get()).rstrip("\n")
        if isinstance(text, str):
            return Text.from_markup(text).plain
        return str(text)
    except Exception:
        return str(text)


def _get_driver_class() -> type[Any] | None:
    """Return the Textual driver class OpenNova should use for this platform."""
    if sys.platform != "win32":
        return None

    from opennova.cli.windows_tui_driver import get_ime_friendly_windows_driver_class

    return get_ime_friendly_windows_driver_class()


class OpenNovaTUI(App):
    """Textual TUI application for OpenNova with split-pane layout."""

    CSS = f"""
    Screen {{
        background: {TUI_THEME.background};
        color: #d7dde3;
    }}

    #main-area {{
        height: 1fr;
        background: {TUI_THEME.background};
    }}

    #messages-area {{
        height: 1fr;
        width: 1fr;
        padding: 1 1 0 1;
        background: {TUI_THEME.background};
    }}

    #messages {{
        height: 1fr;
        overflow-y: auto;
        background: {TUI_THEME.background};
        border: tall {TUI_THEME.surface_soft};
        padding: 0 1;
    }}

    #tool-panel {{
        width: 88;
        height: 1fr;
        overflow-y: auto;
        border-left: solid {TUI_THEME.panel_border};
        background: {TUI_THEME.surface};
        padding: 1;
    }}

    #input-container {{
        height: auto;
        padding: 0 1 1 1;
        background: {TUI_THEME.background};
        border-top: solid {TUI_THEME.surface_soft};
    }}

    #input {{
        width: 100%;
        background: {TUI_THEME.surface};
        color: #d7dde3;
        border: tall {TUI_THEME.panel_border};
    }}

    #suggestions {{
        width: 100%;
        height: 1;
        color: {TUI_THEME.muted};
        background: {TUI_THEME.background};
    }}

    #status-bar {{
        height: 1;
        background: {TUI_THEME.surface};
        color: {TUI_THEME.muted};
    }}

    #status-text {{
        width: 100%;
        padding: 0 1;
    }}

    RichLog {{
        scrollbar-size: 1 1;
    }}
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel", show=True, priority=True),
        Binding("ctrl+shift+c", "copy_selection", "Copy", show=True, priority=True),
        Binding(
            "super+c",
            "copy_selection",
            "Copy",
            show=False,
            key_display="Cmd+C",
            priority=True,
        ),
        Binding("ctrl+d", "quit_app", "Quit", show=True),
        Binding("up", "history_prev", "Previous", show=False),
        Binding("down", "history_next", "Next", show=False),
        Binding("tab", "complete", "Complete", show=False, priority=True),
        Binding("escape", "focus_input", "", show=False),
        Binding("alt+j", "tool_next", "Next tool", show=False),
        Binding("alt+down", "tool_next", "Next tool", show=False),
        Binding("alt+k", "tool_previous", "Previous tool", show=False),
        Binding("alt+up", "tool_previous", "Previous tool", show=False),
        Binding("alt+enter", "tool_toggle_expanded", "Expand tool", show=False),
        Binding("alt+t", "toggle_tool_panel", "Tools", show=True),
        Binding("alt+1", "workbench_context", "Context tab", show=False),
        Binding("alt+2", "workbench_tasks", "Tasks tab", show=False),
        Binding("alt+3", "workbench_activity", "Activity tab", show=False),
        Binding("escape,1", "workbench_context", "Context tab", show=False),
        Binding("escape,2", "workbench_tasks", "Tasks tab", show=False),
        Binding("escape,3", "workbench_activity", "Activity tab", show=False),
        Binding("¡", "workbench_context", "Context tab", show=False),
        Binding("™", "workbench_tasks", "Tasks tab", show=False),
        Binding("£", "workbench_activity", "Activity tab", show=False),
        Binding("alt+[", "workbench_previous_tab", "Previous tab", show=False),
        Binding("alt+]", "workbench_next_tab", "Next tab", show=False),
        Binding("escape,[", "workbench_previous_tab", "Previous tab", show=False),
        Binding("escape,]", "workbench_next_tab", "Next tab", show=False),
    ]

    def __init__(
        self,
        agent: AgentRuntime,
        config: Config | None = None,
        history_file: str | None = None,
        startup_resume_mode: str | None = None,
    ):
        super().__init__(driver_class=_get_driver_class())
        self.agent = agent
        self.config = config
        history_path = Path(history_file) if history_file else Path.home() / ".opennova" / "history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_path = history_path
        self._history_entries: list[str] = []
        self._history_index: int = -1
        self._saved_input: str = ""

        self._task_active: bool = False
        self._agent_task: asyncio.Task | None = None
        self._interaction_future: asyncio.Future | None = None
        self._interaction_mode: bool = False
        self._completion_state: dict[str, Any] = {}
        self._start_time: float = 0.0
        self._tool_progress = ToolProgressTracker()
        self._tool_cards = ToolCardStore()
        self._turn_activity = TurnActivityAccumulator()
        self._last_compression_count = 0
        self._context_status_cache: tuple[float, float] = (0.0, 0.0)
        self._tool_panel_visible = False
        self._workbench_visible = False
        self._workbench_tab: WorkbenchTab = "context"
        self._last_plan_snapshot: PlanWorkbenchSnapshot | None = None
        self._last_plan_chat_signature: tuple[Any, ...] | None = None
        self._state_unsubscribe = None
        self._runtime_unsubscribers: list[Any] = []
        self._callbacks_registered = False
        self._plan_callback_registered = False
        self._pending_workbench_revision = -1
        self._automation_daemon = None
        self._startup_resume_mode = startup_resume_mode
        self._replaying_transcript = False
        self.command_registry = SlashCommandRegistry.default()
        for command in getattr(getattr(self.agent, "plugin_manager", None), "commands", []):
            self.command_registry.register_plugin_command(command)
        self._last_ctrl_c: float = 0.0
        # Guard against duplicate Submitted events from a single Enter press
        self._last_submitted_text: str = ""
        self._last_submitted_time: float = 0.0

    # ── lifecycle ────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-area"):
            with Container(id="messages-area"):
                yield _MessagesLog(
                    id="messages",
                    highlight=True,
                    markup=True,
                    wrap=True,
                    max_lines=10000,
                )
            yield _SelectableRichLog(id="tool-panel", highlight=True, markup=True, wrap=True)
        with Container(id="status-bar"):
            yield Label(id="status-text", markup=True)
        with Container(id="input-container"):
            yield Input(
                id="input",
                placeholder=_INPUT_PLACEHOLDER,
            )
            yield Label(id="suggestions", markup=True)

    def on_mount(self) -> None:
        self._set_tool_panel_visible(False)
        self._load_history()
        self._subscribe_runtime_state()
        self.call_after_refresh(self._after_mount)

    def on_unmount(self) -> None:
        with suppress(Exception):
            self.agent.flush_session()
        if callable(self._state_unsubscribe):
            self._state_unsubscribe()
        for unsubscribe in self._runtime_unsubscribers:
            if callable(unsubscribe):
                unsubscribe()
        self._runtime_unsubscribers.clear()

    def _subscribe_runtime_state(self) -> None:
        """Subscribe once to the workbench-relevant runtime state projection."""
        state_store = getattr(self.agent, "state_store", None)
        if state_store is None or callable(self._state_unsubscribe):
            return

        def selector(snapshot):
            return (
                snapshot.run.phase,
                snapshot.plan.revision,
                snapshot.todos,
            )

        def on_state_changed(selected, event) -> None:
            revision = int(event.revision)
            if revision <= self._pending_workbench_revision:
                return
            self._pending_workbench_revision = revision

            def refresh() -> None:
                if self._pending_workbench_revision != revision:
                    return
                self._refresh_workbench_panel()

            self.call_after_refresh(refresh)

        self._state_unsubscribe = state_store.subscribe(selector, on_state_changed)

    def _after_mount(self) -> None:
        if self._startup_resume_mode:
            asyncio.create_task(self._handle_startup_resume())
            return
        self._show_welcome()
        self._focus_input()

    async def _handle_startup_resume(self) -> None:
        if self._startup_resume_mode == "continue":
            sessions = self._get_resumable_sessions(exclude_current=False)
            if sessions and await self._resume_session_by_id(sessions[0].session_id):
                self._focus_input()
                return
            self._show_welcome()
            log = self.query_one("#messages")
            log.write("[yellow]No saved sessions available to continue.[/yellow]")
            self._focus_input()
            return

        resumed = await self._resume_via_picker(exclude_current=False)
        if not resumed:
            self._show_welcome()
        self._focus_input()

    def _focus_input(self) -> None:
        """Ensure input always has focus."""
        try:
            inp = self.query_one("#input", Input)
            inp.focus()
        except Exception:
            pass

    # ── welcome ──────────────────────────────────────────────────

    def _show_welcome(self) -> None:
        from opennova import __version__

        log = self.query_one("#messages")
        model_info = self.agent.get_model_info()
        provider = str(model_info.get("provider", "-"))
        model = str(model_info.get("model", "-"))
        session_id = str(getattr(getattr(self.agent, "session_manager", None), "session_id", ""))
        log.write(
            render_welcome_block(
                version=__version__,
                provider=provider,
                model=model,
                session_id=session_id,
            )
        )
        log.write("")

    def _record_transcript_event(self, kind: str, **payload: Any) -> None:
        if self._replaying_transcript:
            return
        with suppress(Exception):
            self.agent.record_session_transcript_event(kind, **payload)

    def _write_user_message(self, log: _MessagesLog, text: str, *, record: bool = True) -> None:
        log.write("")
        log.write(render_user_block(text))
        if record:
            self._record_transcript_event("user_message", text=text)

    def _write_assistant_message(
        self,
        log: _MessagesLog,
        text: str,
        *,
        record: bool = True,
    ) -> None:
        log.write(render_assistant_block(text))
        if record:
            self._record_transcript_event("assistant_markdown", content=text)

    def _write_tool_start(
        self,
        log: _MessagesLog,
        tool_name: str,
        detail: str,
        *,
        record: bool = True,
    ) -> None:
        log.write(render_tool_start_block(tool_name, detail))
        if record:
            self._record_transcript_event(
                "tool_start",
                tool_name=tool_name,
                detail=detail,
            )

    def _write_tool_result(
        self,
        log: _MessagesLog,
        *,
        tool_name: str,
        summary_markup: str,
        output: str = "",
        error: str = "",
        diff: str = "",
        diff_max_lines: int = _MAX_OUTPUT_LINES,
        record: bool = True,
    ) -> None:
        for renderable in render_tool_result_block(
            tool_name=tool_name,
            summary_markup=summary_markup,
            output=output,
            error=error,
            diff=diff,
            diff_max_lines=diff_max_lines,
            max_output_lines=_MAX_OUTPUT_LINES,
        ):
            log.write(renderable)
        if record:
            self._record_transcript_event(
                "tool_result",
                tool_name=tool_name,
                summary_markup=summary_markup,
                output=output,
                error=error,
                diff=diff,
                diff_max_lines=diff_max_lines,
            )

    def _write_turn_activity_summary(
        self,
        log: _MessagesLog,
        summary: TurnActivitySummary,
    ) -> None:
        if summary.has_activity:
            log.write(render_turn_activity_summary(summary))

    def _turn_activity_store(self) -> TurnActivityAccumulator:
        activity = getattr(self, "_turn_activity", None)
        if activity is None:
            activity = TurnActivityAccumulator()
            self._turn_activity = activity
        return activity

    def _flush_turn_activity(self, log: _MessagesLog) -> TurnActivitySummary:
        summary = OpenNovaTUI._turn_activity_store(self).consume()
        self._write_compression_notice(log)
        self._write_turn_activity_summary(log, summary)
        return summary

    def _write_compression_notice(self, log: _MessagesLog) -> None:
        context_manager = getattr(getattr(self, "agent", None), "context_manager", None)
        getter = getattr(context_manager, "get_presentation_snapshot", None)
        if not callable(getter):
            return
        snapshot = getter()
        count = int(getattr(snapshot, "compression_count", 0))
        if count <= self._last_compression_count:
            return
        self._last_compression_count = count
        log.write(
            Text(
                f"Earlier context summarized · {count} compression(s)",
                style=TUI_THEME.muted,
            )
        )

    def _set_tool_panel_visible(self, visible: bool) -> None:
        """Show or hide the session-scoped workbench side panel."""
        self._tool_panel_visible = visible
        self._workbench_visible = visible
        with suppress(Exception):
            panel = self.query_one("#tool-panel")
            panel.display = visible
        if not getattr(self, "_task_active", False):
            self._set_status("")

    def _refresh_workbench_panel(self) -> None:
        """Redraw the workbench side panel from current runtime state."""
        try:
            panel = self.query_one("#tool-panel", RichLog)
        except Exception:
            return
        state = build_workbench_panel_state(
            agent=self.agent,
            tool_cards=self._tool_cards,
            active_tab=self._workbench_tab,
            last_plan=getattr(self, "_last_plan_snapshot", None),
        )
        has_context = bool(
            state.context
            and (state.context.task or state.context.total_messages or state.context.active_files)
        )
        has_workbench_content = bool(
            self._tool_cards.cards or state.plan or state.todos or has_context
        )
        if not self._workbench_visible and not has_workbench_content:
            panel.clear()
            return
        self._set_tool_panel_visible(True)
        panel.clear()
        for renderable in render_workbench_panel(state):
            panel.write(renderable)
        panel.scroll_home(animate=False)

    def _refresh_tool_panel(self) -> None:
        """Compatibility alias for the upgraded workbench side panel."""
        self._refresh_workbench_panel()

    def _get_resumable_sessions(self, *, exclude_current: bool) -> list[SessionMeta]:
        sessions = self.agent.get_sessions()
        if exclude_current:
            current_id = self.agent.session_manager.session_id
            sessions = [session for session in sessions if session.session_id != current_id]
        return sessions

    async def _pick_session(self, sessions: list[SessionMeta]) -> str | None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str | None] = loop.create_future()

        def _on_choice(session_id: str | None) -> None:
            if not future.done():
                future.set_result(session_id)

        await self.push_screen(SessionPickerDialog(sessions), callback=_on_choice)
        return await future

    async def _resume_via_picker(self, *, exclude_current: bool) -> bool:
        sessions = self._get_resumable_sessions(exclude_current=exclude_current)
        log = self.query_one("#messages")
        if not sessions:
            log.write("[yellow]No saved sessions found for this project.[/yellow]")
            return False
        session_id = await self._pick_session(sessions)
        if not session_id:
            return False
        return await self._resume_session_by_id(session_id)

    async def _resume_session_by_id(self, session_id: str) -> bool:
        log = self.query_one("#messages")
        try:
            loaded = self.agent.resume_session(session_id)
        except Exception as exc:
            log.write(f"[red]Failed to resume session: {exc}[/red]")
            return False

        self._restore_loaded_session(log, loaded)
        for warning in getattr(loaded, "recovery_warnings", []):
            log.write(f"[yellow]Session recovery warning: {warning}[/yellow]")
        state = getattr(self.agent, "state", None)
        approval = getattr(getattr(state, "plan_approval_status", None), "value", "")
        if approval == "interrupted" and getattr(state, "current_plan", None):
            decision = await self._ask_plan_decision_dialog("Recovered interrupted plan")
            if decision == "execute":
                await self._execute_pending_plan()
            elif decision == "discard":
                self._discard_pending_plan()
            else:
                state.set_mode("plan")
                self._workbench_tab = "tasks"
                self._refresh_workbench_panel()
                log.write(
                    "[yellow]Interrupted plan kept for revision. "
                    "Send your requested changes next.[/yellow]"
                )
        elif getattr(getattr(state, "store", None), "get_state", None):
            if state.store.get_state().run.phase == "interrupted":
                log.write(
                    "[yellow]The previous task was interrupted and was not resumed "
                    "automatically.[/yellow]"
                )
        return True

    def _restore_loaded_session(self, log: _MessagesLog, loaded: LoadedSession) -> None:
        self._replaying_transcript = True
        try:
            log.clear_messages()
            if loaded.transcript_events:
                OpenNovaTUI._replay_transcript_events(
                    self,
                    log,
                    [event.payload for event in loaded.transcript_events],
                )
            else:
                for message in loaded.messages:
                    self._replay_legacy_message(log, message)
        finally:
            self._replaying_transcript = False
        context_manager = getattr(getattr(self, "agent", None), "context_manager", None)
        getter = getattr(context_manager, "get_presentation_snapshot", None)
        if callable(getter):
            self._last_compression_count = int(getter().compression_count)
        log.scroll_end(animate=False)

    def _replay_transcript_events(
        self,
        log: _MessagesLog,
        events: list[dict[str, Any]],
    ) -> None:
        activity = TurnActivityAccumulator()
        for event in events:
            kind = str(event.get("kind") or "")
            if kind in {"tool_start", "tool_result"}:
                activity.apply_transcript_event(event)
                continue
            summary = activity.consume()
            if summary.has_activity:
                OpenNovaTUI._write_turn_activity_summary(self, log, summary)
            OpenNovaTUI._replay_transcript_event(self, log, event)
        summary = activity.consume()
        if summary.has_activity:
            OpenNovaTUI._write_turn_activity_summary(self, log, summary)

    def _replay_transcript_event(self, log: _MessagesLog, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "user_message":
            self._write_user_message(log, str(event.get("text") or ""), record=False)
            return
        if kind == "assistant_markdown":
            self._write_assistant_message(log, str(event.get("content") or ""), record=False)
            return
        if kind == "tool_start":
            self._write_tool_start(
                log,
                str(event.get("tool_name") or "tool"),
                str(event.get("detail") or ""),
                record=False,
            )
            return
        if kind == "tool_result":
            self._write_tool_result(
                log,
                tool_name=str(event.get("tool_name") or "tool"),
                summary_markup=str(event.get("summary_markup") or ""),
                output=str(event.get("output") or ""),
                error=str(event.get("error") or ""),
                diff=str(event.get("diff") or ""),
                diff_max_lines=int(event.get("diff_max_lines") or _MAX_OUTPUT_LINES),
                record=False,
            )
            return
        if kind == "system_markup":
            log.write(str(event.get("content") or ""))
            return
        if kind == "plain_text":
            log.write(str(event.get("content") or ""))

    def _replay_legacy_message(self, log: _MessagesLog, message: Any) -> None:
        if message.role == "user":
            self._write_user_message(log, message.content, record=False)
        elif message.role == "assistant":
            self._write_assistant_message(log, message.content, record=False)
        elif message.role == "tool" and message.content:
            log.write(f"[dim]{message.content}[/dim]")

    # ── key bindings ─────────────────────────────────────────────

    def action_cancel(self) -> None:
        """Cancel the running agent task, or double-press to exit."""
        if self._is_agent_running():
            with suppress(Exception):
                self.agent.state.cancel_run(self.agent.state.run_id)
            self._agent_task.cancel()
            self._set_status("[yellow]Cancelling...[/yellow]")
            return

        with suppress(Exception):
            if self.screen.get_selected_text():
                self.action_copy_selection()
                return

        # When idle, double Ctrl+C exits
        now = time.monotonic()
        if now - self._last_ctrl_c < 1.0:
            self.exit()
        else:
            self._last_ctrl_c = now
            self._set_status("[dim]Press Ctrl+C again to exit[/dim]")
        self.call_after_refresh(self._focus_input)

    def action_quit_app(self) -> None:
        self.exit()

    def action_tool_next(self) -> None:
        if normalize_workbench_tab(getattr(self, "_workbench_tab", "activity")) != "activity":
            return
        self._tool_cards.select_next()
        self._refresh_tool_panel()

    def action_tool_previous(self) -> None:
        if normalize_workbench_tab(getattr(self, "_workbench_tab", "activity")) != "activity":
            return
        self._tool_cards.select_previous()
        self._refresh_tool_panel()

    def action_tool_toggle_expanded(self) -> None:
        if normalize_workbench_tab(getattr(self, "_workbench_tab", "activity")) != "activity":
            return
        self._tool_cards.toggle_expanded()
        self._refresh_tool_panel()

    def action_toggle_tool_panel(self) -> None:
        self._set_tool_panel_visible(not self._tool_panel_visible)
        if self._tool_panel_visible:
            with suppress(Exception):
                self._refresh_workbench_panel()

    def action_workbench_context(self) -> None:
        OpenNovaTUI._set_workbench_tab(self, "context")

    def action_workbench_tasks(self) -> None:
        OpenNovaTUI._set_workbench_tab(self, "tasks")

    def action_workbench_activity(self) -> None:
        OpenNovaTUI._set_workbench_tab(self, "activity")

    def action_workbench_tools(self) -> None:
        """Compatibility alias for the former Tools tab."""
        OpenNovaTUI.action_workbench_activity(self)

    def action_workbench_plan(self) -> None:
        """Compatibility alias for the former Plan tab."""
        OpenNovaTUI.action_workbench_tasks(self)

    def action_workbench_todos(self) -> None:
        """Compatibility alias for the former Todos tab."""
        OpenNovaTUI.action_workbench_tasks(self)

    def action_workbench_next_tab(self) -> None:
        OpenNovaTUI._set_workbench_tab(
            self,
            next_workbench_tab(getattr(self, "_workbench_tab", "context")),
        )

    def action_workbench_previous_tab(self) -> None:
        OpenNovaTUI._set_workbench_tab(
            self,
            previous_workbench_tab(getattr(self, "_workbench_tab", "context")),
        )

    def _set_workbench_tab(self, tab: WorkbenchTab | str) -> None:
        self._workbench_tab = normalize_workbench_tab(tab)
        if hasattr(self, "_set_tool_panel_visible"):
            self._set_tool_panel_visible(True)
        else:
            self._tool_panel_visible = True
            self._workbench_visible = True
        self._refresh_workbench_panel()

    # ── safe state reset ─────────────────────────────────────────

    def _reset_input_state(self) -> None:
        """Unconditionally reset running state and re-enable input.

        Called in every finally block and can also be called as an
        emergency recovery so the UI never gets permanently stuck.
        """
        self._task_active = False
        self._agent_task = None
        self._set_status("")
        with suppress(Exception):
            input_widget = self.query_one("#input", Input)
            input_widget.disabled = False
            input_widget.placeholder = _INPUT_PLACEHOLDER
        self.call_after_refresh(self._focus_input)

    def _clear_suggestions(self) -> None:
        """Clear the suggestions label and completion state."""
        with suppress(Exception):
            self.query_one("#suggestions", Label).update("")
        self._completion_state = {}

    # ── tab completion ────────────────────────────────────────────

    def action_complete(self) -> None:
        """Tab completion: cycle through matching slash commands or history entries."""
        try:
            input_widget = self.query_one("#input", Input)
        except Exception:
            return
        text = input_widget.value

        state = self._completion_state

        # If current text is one of our existing matches, keep cycling
        if state and text in state.get("matches", []):
            matches = state["matches"]
            idx = (matches.index(text) + 1) % len(matches)
            state["index"] = idx
            input_widget.value = matches[idx]
            input_widget.cursor_position = len(matches[idx])
            self._show_suggestions(matches, idx)
            return

        # If the original query hasn't changed, cycle to the next match
        if state and state.get("text") == text:
            matches = state["matches"]
            if matches:
                idx = (state["index"] + 1) % len(matches)
                state["index"] = idx
                input_widget.value = matches[idx]
                input_widget.cursor_position = len(matches[idx])
                self._show_suggestions(matches, idx)
                return

        # Find new completions
        matches = self._get_completions(text)
        if not matches:
            self._clear_suggestions()
            return

        state.clear()
        state["text"] = text
        state["matches"] = matches
        state["index"] = 0

        input_widget.value = matches[0]
        input_widget.cursor_position = len(matches[0])
        self._show_suggestions(matches, 0)

    def _get_completions(self, text: str) -> list[str]:
        """Return matching completions for the given input text."""
        stripped = text.lstrip()
        if stripped.startswith("/"):
            return self._slash_completions(stripped)
        if stripped:
            return self._history_completions(stripped)
        return []

    def _slash_completions(self, text: str) -> list[str]:
        """Complete slash command names and skill names after /skill."""
        if text.startswith("/permissions "):
            candidates = [
                "/permissions mode request",
                "/permissions mode auto",
                "/permissions mode full",
            ]
            return [candidate for candidate in candidates if candidate.startswith(text)]

        # If after "/skill ", complete skill names
        if text.startswith("/skill ") or text == "/skill":
            remainder = text[len("/skill") :].lstrip()
            skill_prefix = remainder
            skills = self.agent.get_skills()
            tokens = remainder.split(maxsplit=1)
            if text.endswith(" ") and tokens:
                hint = self.agent.get_skill_argument_hint(tokens[0], tokens[1] if len(tokens) > 1 else "")
                if hint:
                    return [f"{text}{hint}"]
            matches = [f"/skill {s}" for s in skills if s.startswith(skill_prefix)]
            if skill_prefix:
                matches = [f"/skill {s}" for s in skills if s.startswith(skill_prefix)]
            else:
                matches = [f"/skill {s}" for s in skills]
            matches.sort()
            return matches

        # Complete slash command name (first word)
        parts = text.split(maxsplit=1)
        cmd_prefix = parts[0].replace("_", "-")
        if len(parts) == 1 and not text.endswith(" "):
            # Still typing the command name
            all_cmds = self.command_registry.names()
            matches = [c for c in all_cmds if c.startswith(cmd_prefix)]
            matches.sort()
            return matches
        return []

    def _history_completions(self, text: str) -> list[str]:
        """Complete from command history — prefix match on full entries."""
        seen: set[str] = set()
        matches: list[str] = []
        for entry in self._history_entries:
            entry_stripped = entry.strip()
            if (
                entry_stripped.startswith(text)
                and entry_stripped != text
                and entry_stripped not in seen
            ):
                seen.add(entry_stripped)
                matches.append(entry_stripped)
        return matches

    def _show_suggestions(self, matches: list[str], current_idx: int) -> None:
        """Display completion matches in the suggestions label.

        current_idx < 0 means no highlight (real-time hint mode).
        """
        try:
            label = self.query_one("#suggestions", Label)
            display = matches[:6]
            if current_idx >= len(display):
                current_idx = 0
            parts: list[str] = []
            for i, m in enumerate(display):
                if i == current_idx:
                    parts.append(f"[reverse]{m}[/reverse]")
                else:
                    parts.append(f"[dim]{m}[/dim]")
            suffix = " …" if len(matches) > 6 else ""
            label.update("[dim]commands[/dim]  " + "  ".join(parts) + suffix)
        except Exception:
            pass

    def _is_agent_running(self) -> bool:
        """Return True when an agent task is running or being set up."""
        return self._agent_task is not None and not self._agent_task.done()

    # ── input dispatch ───────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Show completion hints in real-time as the user types."""
        text = event.value
        if self._handle_option_digit_shortcut(text):
            self._clear_suggestions()
            return
        if not text:
            self._clear_suggestions()
            return

        matches = self._get_completions(text)
        if matches:
            self._show_suggestions(matches, -1)
        else:
            self._clear_suggestions()

    def on_key(self, event: Any) -> None:
        """Handle macOS Option+digit shortcuts when no input widget owns the event."""
        if not OpenNovaTUI._handle_option_digit_key(
            self,
            getattr(event, "key", None),
            getattr(event, "character", None),
        ):
            return
        with suppress(Exception):
            event.prevent_default()
        with suppress(Exception):
            event.stop()

    def _handle_option_digit_key(self, key: str | None, character: str | None = None) -> bool:
        """Handle macOS Option+1/2/3 when Textual emits a key event instead of text."""
        pressed = character if character in _MAC_OPTION_DIGIT_TABS else key
        if pressed not in _MAC_OPTION_DIGIT_TABS:
            return False

        with suppress(Exception):
            input_widget = self.query_one("#input", Input)
            if pressed in input_widget.value and self._handle_option_digit_shortcut(input_widget.value):
                return True

        self._set_workbench_tab(_MAC_OPTION_DIGIT_TABS[pressed])
        return True

    def _handle_option_digit_shortcut(self, text: str) -> bool:
        """Handle macOS Option+1/2/3 when it arrives as text input."""
        pressed = [char for char in text if char in _MAC_OPTION_DIGIT_TABS]
        if not pressed:
            return False

        tab = _MAC_OPTION_DIGIT_TABS[pressed[-1]]
        cleaned = "".join(char for char in text if char not in _MAC_OPTION_DIGIT_TABS)
        with suppress(Exception):
            input_widget = self.query_one("#input", Input)
            input_widget.value = cleaned
            input_widget.cursor_position = min(getattr(input_widget, "cursor_position", len(cleaned)), len(cleaned))
        self._set_workbench_tab(tab)
        return True

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()

        text = event.value.strip()
        if not text:
            return

        # Text-based de-dup
        now = time.monotonic()
        if text == self._last_submitted_text and (now - self._last_submitted_time) < 0.3:
            return
        self._last_submitted_text = text
        self._last_submitted_time = now

        # Interaction mode: answer is routed to the pending future.
        # Must be checked BEFORE _is_agent_running() because during
        # ask_user_question the agent task is suspended awaiting this future.
        if self._interaction_mode:
            if self._interaction_future and not self._interaction_future.done():
                self._interaction_future.set_result(text)
            return

        # Don't process new tasks while agent is running.
        if self._is_agent_running():
            self._set_status("[yellow]Agent is busy, please wait...[/yellow]")
            return

        # Clear input and echo user message.
        input_widget = self.query_one("#input", Input)
        input_widget.value = ""
        self._clear_suggestions()

        self._add_to_history(text)

        log = self.query_one("#messages")
        self._write_user_message(log, text)

        # Fast commands: handle synchronously (they return quickly).
        if text.startswith("/"):
            cmd = text.split(maxsplit=1)[0].lower().replace("_", "-")
            if cmd in self._SYNC_COMMANDS:
                await self._handle_command(text)
                self._focus_input()
                return
            # Agent commands: launch in background so Textual can refresh UI.
            self._launch_agent_task(self._handle_command(text))
        else:
            self._launch_agent_task(self._execute_task(text))

    # NOTE: We intentionally do NOT define key_enter().
    # Textual's Input widget natively fires Input.Submitted on Enter.
    # A custom key_enter() would cause double-dispatch.

    # ── command dispatch ─────────────────────────────────────────

    # Commands that return quickly and can be awaited synchronously
    _SYNC_COMMANDS: set[str] = SlashCommandRegistry.default().sync_names()

    def _launch_agent_task(self, coro) -> None:
        """Launch a coroutine as a background task so Textual can refresh UI."""

        async def _runner() -> None:
            try:
                await coro
            except Exception:
                self._reset_input_state()

        asyncio.create_task(_runner())

    async def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().replace("_", "-")
        args = parts[1] if len(parts) > 1 else ""

        command = self.command_registry.get(cmd)
        if command and command.handler and hasattr(self, command.handler):
            handler = getattr(self, command.handler)
            await handler(args)
        else:
            log = self.query_one("#messages")
            log.write(f"[red]Unknown command: {cmd}[/red]")

    # ── slash commands ───────────────────────────────────────────

    async def _cmd_help(self, args: str) -> None:
        log = self.query_one("#messages")
        log.write(
            Markdown(
                """
## Commands

- `/plan <task>` - Plan mode: generate a plan before executing
- `/act <task>` - Act mode: execute directly (default)
- `/tools` - List available tools
- `/skills` - List loaded skills
- `/skill <name> [args]` - Invoke a skill directly
- `/reload-skills` - Reload skills from disk
- `/model` - Show current model info
- `/init [--force]` - Initialize project guide `OPENNOVA.md`
- `/config` - Show current configuration
- `/permissions` - Show the active approval mode and tool rules
- `/permissions mode request|auto|full` - Switch approval mode for this run
- `/permissions <tool> allow|deny|ask` - Update a persisted tool permission rule
- `/plugins [trust|untrust|test name|lock|drift|warnings|audit [--policy strict]]` - Manage and audit local plugins
- `/hooks` - Show loaded hook counts
- `/automations` - List local scheduled automations
- `/automations once <name> <run_at> <prompt>` - Schedule a one-shot local automation
- `/automations interval <name> <seconds> <prompt>` - Schedule an interval automation
- `/automations pause|resume|delete|run-now <id>` - Manage local automations
- `/automations daemon start|stop|status|tick|run` - Control the local automation daemon
- `/diagnostics [path]` - Run Python syntax diagnostics
- `/status` - Show runtime/session status
- `/todos` - Show current task summary
- `/checkpoint` - Show checkpoint/rollback status
- `/checkpoint list|diff|restore [--preview] <id>` - Manage checkpoint snapshots
- `/checkpoint diff --session <session> <id>` - Inspect checkpoint diff from exported session transcript
- `/checkpoint diff --from-transcript <path> <id>` - Inspect checkpoint diff from transcript
- `/export [dir]` - Export current transcript to Markdown
- `/history [n]` - Show recent conversation history
- `/clear` - Clear conversation (starts a new session)
- `/resume [id]` - Resume a past session (empty = pick from list)
- `/sessions` - List all saved sessions
- `/help` - Show this help
- `/exit` - Exit

## Tips

- Press `Tab` to complete slash commands and history entries
- Press `Up`/`Down` to navigate command history
- `Ctrl+C` cancels a running task; press twice on empty prompt to exit
"""
            )
        )

    async def _cmd_exit(self, args: str) -> None:
        self.exit()

    async def _cmd_act(self, args: str) -> None:
        if not args:
            log = self.query_one("#messages")
            log.write("[red]Usage: /act <task>[/red]")
            return
        await self._execute_task(args, route_workflow=False)

    async def _cmd_tools(self, args: str) -> None:
        log = self.query_one("#messages")
        table = Table(title="Available Tools")
        table.add_column("Tool Name", style="cyan")
        for tool in sorted(self.agent.get_tools()):
            table.add_row(tool)
        log.write(table)

    async def _cmd_skills(self, args: str) -> None:
        log = self.query_one("#messages")
        skill_registry = getattr(self.agent, "skill_registry", None)
        if not skill_registry:
            log.write("[yellow]No skills loaded.[/yellow]")
            return

        table = Table(title="Loaded Skills")
        table.add_column("Name", style="cyan")
        table.add_column("State")
        table.add_column("Description")
        names = skill_registry.list_skills()
        if not names:
            log.write("[yellow]No skills loaded.[/yellow]")
            return

        for name in sorted(names):
            info = skill_registry.get_skill_info(name) or {}
            table.add_row(name, str(info.get("activation_state", "static")), info.get("description", ""))
        log.write(table)

    async def _cmd_skill(self, args: str) -> None:
        if not args:
            log = self.query_one("#messages")
            log.write("[red]Usage: /skill <name> [args][/red]")
            return

        parts = args.split(maxsplit=1)
        skill_name = parts[0]
        skill_args = parts[1] if len(parts) > 1 else ""

        result = self.agent.invoke_skill(
            skill_name=skill_name, skill_args=skill_args, caller="user"
        )
        if not result.success:
            log = self.query_one("#messages")
            log.write(f"[red]{result.error or 'Failed to invoke skill'}[/red]")
            return

        skill_prompt = result.metadata.get("skill_prompt", "")
        if not skill_prompt:
            log = self.query_one("#messages")
            log.write("[red]Skill prompt is empty[/red]")
            return

        log = self.query_one("#messages")
        resolved_name = result.metadata.get("resolved_skill", skill_name)
        log.write(f"[green]Invoked skill: {resolved_name}[/green]")

        from opennova.providers.base import Message

        self.agent.context_manager.add_message(
            Message(
                role="user",
                content=f"Invoked skill '{resolved_name}':\n\n{skill_prompt}",
            )
        )

        task = f"/skill {resolved_name} {skill_args}".strip()
        await self._execute_task(task, preserve_context=True)

    async def _cmd_reload_skills(self, args: str) -> None:
        count = self.agent.reload_skills()
        log = self.query_one("#messages")
        log.write(f"[green]Reloaded {count} skills.[/green]")

    async def _cmd_model(self, args: str) -> None:
        log = self.query_one("#messages")
        info = self.agent.get_model_info()
        table = Table(title="Model Information")
        table.add_column("Property", style="cyan")
        table.add_column("Value")
        for key, value in info.items():
            table.add_row(key, str(value))
        log.write(table)

    async def _cmd_init(self, args: str) -> None:
        log = self.query_one("#messages")
        force = False
        tokens = [token for token in args.split() if token.strip()]
        if tokens:
            if len(tokens) == 1 and tokens[0] in {"--force", "-f"}:
                force = True
            else:
                log.write("[red]Usage: /init [--force][/red]")
                return

        result = await self._run_agent_task(self.agent.init_project_guide_async(force=force))
        if result is None:
            return
        if result.success:
            log.write(f"[green]{result.output}[/green]")
        else:
            log.write(f"[red]{result.error or 'Failed to initialize OPENNOVA.md'}[/red]")

    async def _cmd_config(self, args: str) -> None:
        import yaml

        log = self.query_one("#messages")
        if not self.config:
            log.write("[red]No configuration object available.[/red]")
            return
        if self.config.config_path:
            log.write(f"[cyan]Config path:[/cyan] {self.config.config_path}")
        log.write(
            Syntax(
                yaml.dump(self.config.data, default_flow_style=False, sort_keys=False),
                "yaml",
                theme="monokai",
            )
        )

    async def _cmd_clear(self, args: str) -> None:
        self.agent.clear_conversation()
        log = self.query_one("#messages")
        log.write("[green]Conversation cleared.[/green]")

    async def _cmd_history(self, args: str) -> None:
        log = self.query_one("#messages")
        context_manager = getattr(self.agent, "context_manager", None)
        if not context_manager:
            log.write("[yellow]No conversation history.[/yellow]")
            return

        history = context_manager.get_conversation_history()
        if args:
            try:
                limit = int(args)
                history = history[-limit:] if limit > 0 else history
            except ValueError:
                log.write("[red]Usage: /history [n][/red]")
                return

        if not history:
            log.write("[yellow]No conversation history.[/yellow]")
            return

        table = Table(title="Conversation History")
        table.add_column("Role", style="cyan")
        table.add_column("Content")
        for entry in history:
            table.add_row(
                entry.get("role", ""),
                (entry.get("content", "") or "")[:120],
            )
        log.write(table)

    async def _cmd_sessions(self, args: str) -> None:
        log = self.query_one("#messages")
        sessions = self.agent.get_sessions()
        current_id = self.agent.session_manager.session_id
        if not sessions:
            log.write("[yellow]No saved sessions found for this project.[/yellow]")
            return
        table = Table(title="Saved Sessions")
        table.add_column("ID", style="cyan")
        table.add_column("First Prompt")
        table.add_column("Messages", justify="right")
        table.add_column("Date")
        for s in sessions:
            sid = s.session_id[:8]
            if s.session_id == current_id:
                sid = f"[bold]{sid}[/bold]"
            prompt = format_session_title_snippet(s.first_prompt or "Untitled session", limit=20)
            from datetime import datetime

            date_str = datetime.fromtimestamp(s.modified).strftime("%m-%d %H:%M")
            table.add_row(sid, prompt, str(s.message_count), date_str)
        log.write(table)
        log.write("[dim]Use /resume <id> to restore a session.[/dim]")

    async def _cmd_resume(self, args: str) -> None:
        log = self.query_one("#messages")
        if args:
            session_id = args.strip()
            # Support partial ID matching
            sessions = self._get_resumable_sessions(exclude_current=True)
            matched = [s for s in sessions if s.session_id.startswith(session_id)]
            if not matched:
                log.write(f"[red]Session '{session_id}' not found.[/red]")
                return
            if len(matched) > 1:
                log.write("[yellow]Multiple matches, use a longer prefix:[/yellow]")
                for s in matched:
                    log.write(f"  [dim]{s.session_id[:16]}...[/dim] - {s.first_prompt[:60]}")
                return
            session_id = matched[0].session_id
            await self._resume_session_by_id(session_id)
            return

        await self._resume_via_picker(exclude_current=True)

    async def _cmd_permissions(self, args: str) -> None:
        from opennova.security.permissions import PermissionDecision, PermissionStore

        log = self.query_one("#messages")
        store = getattr(self.agent.guardrails, "permission_store", None)
        if store is None:
            store = PermissionStore(Path(".opennova") / "permissions.json")
            self.agent.guardrails.permission_store = store

        tokens = args.split()
        if tokens and tokens[0].lower() == "mode":
            if len(tokens) != 2 or tokens[1].lower() not in {"request", "auto", "full"}:
                log.write("[red]Usage: /permissions mode request|auto|full[/red]")
                return
            mode = self.agent.set_permission_mode(tokens[1].lower())
            descriptions = {
                "request": "every allowed tool call requires approval",
                "auto": "routine development calls run automatically; only high-risk calls require approval",
                "full": "allowed tool calls skip approval; hard safety blocks remain active",
            }
            log.write(
                f"[green]Permission mode: {mode.value}[/green]\n"
                f"[dim]{descriptions[mode.value]}[/dim]"
            )
            self._set_status(f"[green]permissions:{mode.value}[/green]")
            return

        if len(tokens) >= 2:
            aliases = {
                "allow": PermissionDecision.ALWAYS_ALLOW,
                "deny": PermissionDecision.ALWAYS_DENY,
                "ask": PermissionDecision.ALWAYS_ASK,
            }
            decision = aliases.get(tokens[1])
            if decision is None:
                log.write(
                    "[red]Usage: /permissions [mode request|auto|full|"
                    "<tool> allow|deny|ask][/red]"
                )
                return
            store.record(tokens[0], decision)
            security_config = getattr(self.agent, "security_config", {})
            self.agent.guardrails.always_allow_tools = set(
                security_config.get("always_allow_tools", [])
            ) | set(store.allowed_tools())
            self.agent.guardrails.always_deny_tools = set(
                security_config.get("always_deny_tools", [])
            ) | set(store.denied_tools())
            self.agent.guardrails.always_ask_tools = set(
                security_config.get("always_ask_tools", [])
            ) | set(store.ask_tools())
            log.write(f"[green]Permission rule saved: {tokens[0]} -> {decision.value}[/green]")
            return

        mode = self.agent.get_permission_mode().value
        log.write(f"[cyan]Permission mode:[/cyan] {mode}")
        if not store.rules:
            log.write("[yellow]No persisted permission rules.[/yellow]")
            return
        table = Table(title="Permission Rules")
        table.add_column("Tool", style="cyan")
        table.add_column("Decision")
        for tool_name, decision in sorted(store.rules.items()):
            table.add_row(tool_name, decision.value)
        log.write(table)

    async def _cmd_plugins(self, args: str) -> None:
        log = self.query_one("#messages")
        manager = getattr(self.agent, "plugin_manager", None)
        if not manager:
            log.write("[yellow]No plugin manager available.[/yellow]")
            return
        tokens = args.split()
        if tokens and tokens[0] in {"trust", "untrust", "test", "lock", "drift", "warnings", "audit"}:
            from opennova.cli.plugin_commands import handle_plugin_command

            manager.load_enabled_plugins(self.agent.config, hook_manager=self.agent.hook_manager)
            result = handle_plugin_command(manager, args)
            manager.load_enabled_plugins(self.agent.config, hook_manager=self.agent.hook_manager)
            if result.success:
                log.write(f"[green]{result.output}[/green]")
            else:
                log.write(f"[red]{result.error or 'Plugin command failed'}[/red]")
            return
        plugins = manager.load_enabled_plugins(
            self.agent.config, hook_manager=self.agent.hook_manager
        )
        if not plugins:
            log.write("[yellow]No project plugins discovered.[/yellow]")
            return
        table = Table(title="Project Plugins")
        table.add_column("Name", style="cyan")
        table.add_column("Trusted")
        table.add_column("Description")
        for plugin in plugins:
            table.add_row(
                plugin.name, "yes" if manager.is_trusted(plugin.name) else "no", plugin.description
            )
        log.write(table)

    async def _cmd_hooks(self, args: str) -> None:
        log = self.query_one("#messages")
        callbacks = getattr(getattr(self.agent, "hook_manager", None), "_callbacks", {})
        table = Table(title="Hooks")
        table.add_column("Event", style="cyan")
        table.add_column("Callbacks")
        for event_name, items in sorted(callbacks.items()):
            table.add_row(event_name, str(len(items)))
        log.write(table)

    async def _cmd_automations(self, args: str) -> None:
        from opennova.automation import AutomationArchive, LocalAutomationScheduler
        from opennova.cli.automation_commands import handle_automation_command

        log = self.query_one("#messages")
        scheduler = LocalAutomationScheduler(Path(".opennova") / "automations.json")
        if self._automation_daemon is None:
            from opennova.automation import LocalAutomationDaemon

            self._automation_daemon = LocalAutomationDaemon(scheduler)
        result = handle_automation_command(
            scheduler,
            args,
            runner=lambda task: f"Automation prompt ready for execution: {task.prompt}",
            daemon=self._automation_daemon,
            archive=AutomationArchive(Path(".opennova") / "automation-archive"),
        )
        if result.success:
            log.write(result.output)
        else:
            log.write(f"[red]{result.error or 'Automation command failed'}[/red]")

    async def _cmd_diagnostics(self, args: str) -> None:
        log = self.query_one("#messages")
        tool = self.agent.tool_registry.get("python_diagnostics")
        result = tool.execute(path=args.strip() or ".")
        if result.success:
            log.write(f"[green]{result.output}[/green]")
        else:
            log.write(f"[red]{result.error or result.output}[/red]")

    async def _cmd_status(self, args: str) -> None:
        log = self.query_one("#messages")
        info = self.agent.get_model_info()
        log.write(
            f"[cyan]Provider:[/cyan] {info.get('provider')}  "
            f"[cyan]Model:[/cyan] {info.get('model')}  "
            f"[cyan]Session:[/cyan] {self.agent.session_manager.session_id[:8]}"
        )
        log.write(
            f"[cyan]Tools:[/cyan] {len(self.agent.get_tools())}  "
            f"[cyan]Plugins:[/cyan] {len(getattr(self.agent.plugin_manager, 'plugins', []))}"
        )
        log.write(f"[cyan]Permission mode:[/cyan] {self.agent.get_permission_mode().value}")
        context_manager = getattr(self.agent, "context_manager", None)
        getter = getattr(context_manager, "get_presentation_snapshot", None)
        if callable(getter):
            context = getter()
            log.write(
                f"[cyan]Context:[/cyan] {context.total_tokens}/{context.context_window} "
                f"tokens ({context.utilization_percent:.1f}%)  "
                f"[cyan]Compressions:[/cyan] {context.compression_count}"
            )

    async def _cmd_todos(self, args: str) -> None:
        log = self.query_one("#messages")
        from opennova.tools.todo_tools import TodoWriteTool

        self._workbench_tab = "tasks"
        with suppress(Exception):
            self._refresh_workbench_panel()

        todos = TodoWriteTool.current_todos(getattr(self.agent, "state_store", None))
        if not todos:
            task = getattr(self.agent.state, "current_task", "") or "(none)"
            log.write(f"[cyan]Current task:[/cyan] {task}\n[yellow]No todos recorded.[/yellow]")
            return
        table = Table(title="Todos")
        table.add_column("ID", style="cyan")
        table.add_column("Status")
        table.add_column("Content")
        for todo in todos:
            table.add_row(todo["id"], todo["status"], todo["content"])
        log.write(table)

    async def _cmd_checkpoint(self, args: str) -> None:
        from opennova.cli.checkpoint_commands import handle_checkpoint_command

        log = self.query_one("#messages")
        result = handle_checkpoint_command(Path.cwd(), args)
        if result.success:
            log.write(result.output)
        else:
            log.write(f"[red]{result.error or 'Checkpoint command failed'}[/red]")

    async def _cmd_export(self, args: str) -> None:
        from opennova.transcript import TranscriptExporter

        log = self.query_one("#messages")
        output_dir = Path(args.strip()).expanduser() if args.strip() else Path(".opennova") / "exports"
        path = TranscriptExporter(output_dir).export_runtime(self.agent)
        log.write(f"[green]Transcript exported to {path}[/green]")

    async def _cmd_plan(self, args: str) -> None:
        if not args:
            log = self.query_one("#messages")
            log.write("[red]Usage: /plan <task>[/red]")
            return

        await OpenNovaTUI._run_plan_flow(self, args, user_message=f"/plan {args}")

    async def _run_plan_flow(self, task: str, *, user_message: str | None = None) -> None:
        """Generate a plan and ask the user how to proceed."""
        log = self.query_one("#messages")
        log.write(f"[yellow]Planning: {task}[/yellow]")
        self._register_plan_workbench_callback()

        # Phase 1: Generate the plan (not running state — user can still cancel)
        try:
            result = await self.agent.run(task, mode="plan")
            log.write(Markdown(result))
        except Exception as e:
            log.write(f"[red]Planning failed: {type(e).__name__}: {e}[/red]")
            return

        # Phase 2: Ask for plan approval via the same explicit decision dialog used in chat.
        decision = await self._ask_plan_decision_dialog(user_message or task)
        if decision == "discard":
            OpenNovaTUI._discard_pending_plan(self)
            return
        if decision == "revise":
            OpenNovaTUI._keep_plan_for_revision(self)
            log.write("[yellow]Plan kept for revision. Send your requested changes next.[/yellow]")
            return

        # Phase 3: Execute approved plan — fully guarded by try/finally
        log.write("[cyan]Executing approved plan...[/cyan]")
        await OpenNovaTUI._execute_pending_plan(self)

    def _register_plan_workbench_callback(self, *, write_chat: bool | None = True) -> None:
        """Register the plan callback that mirrors plan state into the workbench."""
        if getattr(self, "_plan_callback_registered", False):
            return
        self._plan_callback_registered = True

        def on_plan(plan, plan_file_path=None):
            try:
                self._workbench_tab = "tasks"
                state = getattr(self.agent, "state", None)
                approval = getattr(getattr(state, "plan_approval_status", None), "value", None)
                plan_path = plan_file_path or getattr(state, "plan_file_path", None)
                self._last_plan_snapshot = snapshot_plan(
                    plan,
                    plan_file_path=plan_path,
                    approval_status=approval,
                )
                should_write_chat = (
                    OpenNovaTUI._should_write_plan_to_chat(self)
                    if write_chat is None
                    else write_chat
                )
                signature = OpenNovaTUI._plan_chat_signature(self, plan)
                if should_write_chat and signature != getattr(self, "_last_plan_chat_signature", None):
                    _log = self.query_one("#messages")
                    table = Table(title=f"Plan: {plan.task}")
                    table.add_column("Step", style="cyan")
                    table.add_column("Description")
                    table.add_column("Status", justify="center")
                    status_icons = {
                        "pending": "⏳",
                        "running": "🔄",
                        "done": "✅",
                        "failed": "❌",
                        "skipped": "⏭️",
                        "interrupted": "⏸",
                    }
                    for step in plan.steps:
                        icon = status_icons.get(step.status.value, "❓")
                        table.add_row(step.id, step.description, icon)
                    _log.write(table)
                    if plan_path:
                        _log.write(f"[green]Plan saved to:[/green] {plan_path}")
                    self._last_plan_chat_signature = signature
                self._refresh_workbench_panel()
            except Exception:
                pass

        unsubscribe = self.agent.register_callback("plan", on_plan)
        if callable(unsubscribe):
            unsubscribers = getattr(self, "_runtime_unsubscribers", None)
            if unsubscribers is None:
                unsubscribers = []
                self._runtime_unsubscribers = unsubscribers
            unsubscribers.append(unsubscribe)

    def _should_write_plan_to_chat(self) -> bool:
        """Return whether a plan update is the initial reviewable planning output."""
        state = getattr(self.agent, "state", None)
        mode = getattr(getattr(state, "mode", None), "value", getattr(state, "mode", ""))
        approval = getattr(getattr(state, "plan_approval_status", None), "value", "")
        return mode == "plan" and approval not in {"approved", "executing"}

    def _plan_chat_signature(self, plan: Any) -> tuple[Any, ...]:
        """Stable signature used to avoid duplicating the same review table."""
        return (
            getattr(plan, "task", ""),
            tuple(
                (
                    getattr(step, "id", ""),
                    getattr(step, "description", ""),
                )
                for step in getattr(plan, "steps", []) or []
            ),
        )

    # ── interaction helper ───────────────────────────────────────

    async def _ask_user(self, placeholder: str = "Your answer: ") -> str:
        """Block until the user types a response in the input box.

        Used for plan approval and agent interaction prompts.
        Clears the input widget each time so multi-question dialogs
        start with a fresh input field.
        """
        self._interaction_mode = True
        input_widget = self.query_one("#input", Input)
        input_widget.value = ""
        input_widget.disabled = False
        input_widget.placeholder = placeholder
        input_widget.focus()

        loop = asyncio.get_running_loop()
        self._interaction_future = loop.create_future()
        try:
            answer = await self._interaction_future
        finally:
            self._interaction_future = None
            self._interaction_mode = False
        return answer

    # ── task execution ───────────────────────────────────────────

    async def _run_agent_task(self, coro) -> str | None:
        """Run an agent coroutine with spinner, state management, and error handling.

        Returns the result string or None.
        """
        self._task_active = True
        self._start_time = time.time()
        OpenNovaTUI._turn_activity_store(self).reset()

        try:
            input_widget = self.query_one("#input", Input)
            log = self.query_one("#messages")

            self._register_callbacks()
            self.agent.register_callback("interaction", self._handle_interaction)

            input_widget.disabled = True
            input_widget.placeholder = _WORKING_PLACEHOLDER
            await asyncio.sleep(0)  # yield a frame so UI updates

            self._agent_task = asyncio.create_task(coro)

            # Spinner loop
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            i = 0
            while not self._agent_task.done():
                frame = frames[i % len(frames)]
                self._set_status(self._tool_progress.status_text(frame=frame))
                i += 1
                await asyncio.sleep(0.1)

            result = self._agent_task.result()
            self._set_status("")
            if isinstance(result, str) and result:
                self._flush_turn_activity(log)
                self._write_assistant_message(log, result)
            else:
                self._flush_turn_activity(log)
            with suppress(Exception):
                self._refresh_workbench_panel()
            return result

        except asyncio.CancelledError:
            self._set_status("")
            self._flush_turn_activity(log)
            log.write("[yellow]Task cancelled[/yellow]")
            return None
        except Exception as e:
            self._set_status("")
            self._flush_turn_activity(log)
            log.write(f"[red]Error: {type(e).__name__}: {e}[/red]")
            return None
        finally:
            self._reset_input_state()

    async def _execute_task(
        self,
        task: str,
        preserve_context: bool = True,
        route_workflow: bool = True,
    ) -> None:
        """Execute a user task through the agent.

        By default preserves context so the conversation accumulates across
        turns within a session. The ReActLoop handles first-turn setup
        (system prompt injection) correctly even with preserve_context=True.
        """
        if _has_plan_revision_in_progress(getattr(self.agent, "state", None)):
            await OpenNovaTUI._continue_plan_conversation(
                self,
                task,
                preserve_context=preserve_context,
            )
            return

        if _has_pending_plan_decision(getattr(self.agent, "state", None)):
            decision = await self._ask_plan_decision_dialog(task)
            if decision == "execute":
                await OpenNovaTUI._execute_pending_plan(self)
                return
            if decision == "discard":
                OpenNovaTUI._discard_pending_plan(self)
                return
            await OpenNovaTUI._continue_plan_conversation(
                self,
                task,
                preserve_context=preserve_context,
            )
            return

        await self._run_agent_task(
            self.agent._run_act_mode(
                task=task,
                stream=True,
                preserve_context=preserve_context,
                route_workflow=route_workflow,
            )
        )

        if not _has_pending_plan_decision(getattr(self.agent, "state", None)):
            return

        decision = await self._ask_plan_decision_dialog(task)
        if decision == "execute":
            await OpenNovaTUI._execute_pending_plan(self)
        elif decision == "discard":
            OpenNovaTUI._discard_pending_plan(self)
        else:
            OpenNovaTUI._keep_plan_for_revision(self)
            log = self.query_one("#messages")
            log.write("[yellow]Plan kept for revision. Send your requested changes next.[/yellow]")

    async def _ask_plan_decision_dialog(self, user_message: str) -> PlanDecision:
        """Show the pending-plan decision modal and wait for the selected action."""
        state = getattr(self.agent, "state", None)
        plan = getattr(state, "current_plan", None)
        plan_title = str(getattr(plan, "task", "") or "")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PlanDecision] = loop.create_future()

        def _on_decision(decision: PlanDecision | None) -> None:
            if not future.done():
                future.set_result(decision or "revise")

        await self.push_screen(
            PlanDecisionDialog(plan_title=plan_title, user_message=user_message),
            callback=_on_decision,
        )
        return await future

    async def _execute_pending_plan(self) -> None:
        """Approve and execute the current pending plan."""
        self.agent.state.mark_plan_approved()
        self._workbench_tab = "tasks"
        with suppress(Exception):
            self._refresh_workbench_panel()
        await self._run_agent_task(self.agent.execute_approved_plan())
        with suppress(Exception):
            self._refresh_workbench_panel()

    def _discard_pending_plan(self) -> None:
        """Discard the current pending plan and mirrored todos."""
        from opennova.tools.todo_tools import TodoWriteTool

        self.agent.state.clear_plan_state()
        TodoWriteTool.replace_todos([], getattr(self.agent, "state_store", None))
        self._last_plan_snapshot = None
        self._last_plan_chat_signature = None
        self._workbench_tab = "tasks"
        with suppress(Exception):
            self._refresh_workbench_panel()
        with suppress(Exception):
            log = self.query_one("#messages")
            self._write_assistant_message(log, "Plan discarded. We can continue without it.")

    def _keep_plan_for_revision(self) -> None:
        """Move an approval-pending plan back to draft for the next user turn."""
        state = getattr(self.agent, "state", None)
        plan = getattr(state, "current_plan", None)
        if state is None or plan is None:
            return
        state.set_plan(plan)
        self._workbench_tab = "tasks"
        with suppress(Exception):
            self._refresh_workbench_panel()

    async def _continue_plan_conversation(self, task: str, preserve_context: bool = True) -> None:
        """Continue discussing or revising the pending plan without executing it."""
        self._workbench_tab = "tasks"
        with suppress(Exception):
            self.agent.state.set_mode("plan")
            self._refresh_workbench_panel()
        await self._run_agent_task(
            self.agent._run_act_mode(
                task=(
                    "Continue planning. The user wants to discuss or revise the pending plan; "
                    f"do not execute implementation steps yet.\n\nUser message: {task}"
                ),
                stream=True,
                preserve_context=preserve_context,
                preserve_plan_state=True,
            )
        )

    def _register_callbacks(self) -> None:
        if getattr(self, "_callbacks_registered", False):
            return
        self._callbacks_registered = True
        _current_tool: dict[str, str] = {"name": ""}
        _canonical_tools: dict[str, bool] = {"seen": False}
        OpenNovaTUI._register_plan_workbench_callback(self, write_chat=None)

        def on_thought(thought: str) -> None:
            return None

        def on_action(tool_name: str, args: dict) -> None:
            if _canonical_tools["seen"]:
                return
            _current_tool["name"] = tool_name
            self._tool_progress.start_tool(tool_name, args)

        def on_result(result: ToolResult) -> None:
            if _canonical_tools["seen"]:
                return
            event = self._tool_progress.finish_tool(result)
            tool_name = _current_tool["name"] or "tool"
            activity = OpenNovaTUI._turn_activity_store(self)
            fallback_id = f"fallback_{len(activity.snapshot().tool_names) + 1}"
            start_event = {
                "type": "tool_start",
                "tool_id": fallback_id,
                "tool_name": tool_name,
                "arguments": dict(getattr(self._tool_progress, "current_args", {}) or {}),
            }
            result_event = {
                "type": "tool_result" if result.success else "tool_error",
                "tool_id": fallback_id,
                "tool_name": tool_name,
                "success": result.success,
                "duration_ms": event.get("duration_ms", 0),
            }
            activity.apply_event(start_event)
            activity.apply_event(result_event)
            with suppress(Exception):
                self._record_transcript_event(
                    "tool_start",
                    tool_name=tool_name,
                    detail=f"[dim]{fallback_id}[/dim]",
                )
            color = "green" if result.success else "red"
            with suppress(Exception):
                self._record_transcript_event(
                    "tool_result",
                    tool_name=tool_name,
                    summary_markup=(
                        f"[{color}]Result:[/{color}] "
                        f"[dim]{tool_name} in {event.get('duration_ms', 0)}ms[/dim]"
                    ),
                    output=str(event.get("output_preview") or ""),
                    error=result.error or "",
                    diff=str(result.metadata.get("diff") or ""),
                    diff_max_lines=_MAX_DIFF_LINES.get(tool_name, _MAX_OUTPUT_LINES),
                )

        def on_tool_event(event: Any) -> None:
            _canonical_tools["seen"] = True
            if hasattr(event, "type"):
                self._tool_cards.apply_event(event)
                with suppress(Exception):
                    self._refresh_tool_panel()
            data = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            event_type = data.get("type")
            tool_name = data.get("tool_name", "tool")
            OpenNovaTUI._turn_activity_store(self).apply_event(data)
            if event_type == "tool_start":
                self._tool_progress.current_tool_id = str(data.get("tool_id", ""))
                self._tool_progress.current_tool_name = tool_name
                self._tool_progress.current_args = dict(data.get("arguments") or {})
                self._tool_progress.started_at = float(data.get("started_at") or time.time())
                with suppress(Exception):
                    self._record_transcript_event(
                        "tool_start",
                        tool_name=tool_name,
                        detail=f"[dim]{data.get('tool_id')}[/dim]",
                    )
            elif event_type == "permission_request":
                self._tool_progress.waiting_for_interaction = True
                self._tool_progress.interaction_label = "Confirm"
            elif event_type in {"tool_result", "tool_error", "tool_cancelled"}:
                success = data.get("success") is True
                color = "green" if success else "red"
                duration = data.get("duration_ms")
                summary_markup = (
                    f"[{color}]Result:[/{color}] [dim]{tool_name} in {duration or 0}ms[/dim]"
                )
                output = ""
                if tool_name not in _SUPPRESSED_RESULT_OUTPUT:
                    output = _truncate_tool_output(tool_name, str(data.get("output") or ""))
                with suppress(Exception):
                    self._record_transcript_event(
                        "tool_result",
                        tool_name=tool_name,
                        summary_markup=summary_markup,
                        output=output,
                        error=str(data.get("error") or ""),
                        diff=str(data.get("diff") or ""),
                        diff_max_lines=_MAX_DIFF_LINES.get(tool_name, _MAX_OUTPUT_LINES),
                    )
                self._tool_progress.clear_interaction()
                self._tool_progress.current_tool_name = ""

        def on_stream(chunk: StreamChunk) -> None:
            return None

        for event_name, callback in (
            ("thought", on_thought),
            ("action", on_action),
            ("result", on_result),
            ("stream", on_stream),
            ("tool_event", on_tool_event),
        ):
            unsubscribe = self.agent.register_callback(event_name, callback)
            if callable(unsubscribe):
                unsubscribers = getattr(self, "_runtime_unsubscribers", None)
                if unsubscribers is None:
                    unsubscribers = []
                    self._runtime_unsubscribers = unsubscribers
                unsubscribers.append(unsubscribe)

    # ── interaction ──────────────────────────────────────────────

    async def _handle_interaction(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Handle ask_user_question interaction with multi-question support.

        Renders each question as a dialog panel and collects answers one at a time.
        All answers are batched and returned together, matching Claude Code's behavior.
        """
        self._tool_progress.start_interaction(metadata)
        try:
            questions = metadata.get("questions", [])
            if not questions:
                # Fallback to prompt_payload for backward compat
                payload = metadata.get("prompt_payload", {})
                questions = [payload] if payload.get("question") else []

            if not questions:
                return {
                    "skipped": True,
                    "answers": {},
                    "all_answers": [],
                    "display": "(no questions)",
                }

            all_answers: list[dict[str, Any]] = []

            for qi, q in enumerate(questions):
                question = q.get("question", "")
                options = q.get("options", [])
                free_text = q.get("free_text", False)
                header = q.get("header")
                multi_select = q.get("multiSelect", False)
                total = len(questions)
                progress_label = f"Question {qi + 1}/{total}" if total > 1 else None

                answer_payload = await self._ask_question_dialog(
                    question=question,
                    header=header,
                    options=options,
                    free_text=free_text,
                    multi_select=multi_select,
                    progress_label=progress_label,
                )
                all_answers.append(answer_payload)

            all_skipped = all(a.get("skipped") for a in all_answers)
            answers_map = {a["question"]: a.get("answer") for a in all_answers}
            display_parts = []
            for a in all_answers:
                q_text = a["question"]
                ans = a.get("answer")
                if a.get("skipped"):
                    display_parts.append(f"Q: {q_text} → (skipped)")
                else:
                    display_parts.append(f"Q: {q_text} → {ans}")

            return {
                "skipped": all_skipped,
                "answers": answers_map,
                "all_answers": all_answers,
                "display": "\n".join(display_parts),
            }
        finally:
            self._tool_progress.clear_interaction()

    async def _ask_question_dialog(
        self,
        *,
        question: str,
        header: str | None,
        options: list[dict[str, Any]],
        free_text: bool,
        multi_select: bool,
        progress_label: str | None,
    ) -> dict[str, Any]:
        """Show the ask_user_question modal and wait for its result."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        def _on_answer(answer: dict[str, Any] | None) -> None:
            if not future.done():
                future.set_result(answer or {})

        await self.push_screen(
            AskQuestionDialog(
                question=question,
                header=header,
                options=options,
                free_text=free_text,
                multi_select=multi_select,
                progress_label=progress_label,
            ),
            callback=_on_answer,
        )
        return await future

    # ── diff display ─────────────────────────────────────────────

    def _write_diff(self, log: _MessagesLog, diff_text: str, max_lines: int = _MAX_OUTPUT_LINES) -> None:
        lines = diff_text.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            truncated = True
        else:
            truncated = False

        log.write("")
        for line in lines:
            log.write(line)

        if truncated:
            log.write(
                f"[dim]... (diff truncated, {max_lines}/{len(diff_text.splitlines())} lines)[/dim]"
            )
        log.write("")

    # ── status bar ───────────────────────────────────────────────

    def _build_status_text(self, message: str = "") -> str:
        model_info = self.agent.get_model_info() if hasattr(self.agent, "get_model_info") else {}
        session_id = str(getattr(getattr(self.agent, "session_manager", None), "session_id", ""))
        model = str(model_info.get("model") or "unknown")
        mode_getter = getattr(self.agent, "get_permission_mode", None)
        permission_mode = mode_getter().value if callable(mode_getter) else "auto"
        context_utilization = OpenNovaTUI._cached_context_utilization(self)
        phase = "running" if getattr(self, "_task_active", False) else "idle"
        state_store = getattr(self.agent, "state_store", None)
        with suppress(Exception):
            run_phase = state_store.get_state().run.phase
            phase = str(getattr(run_phase, "value", run_phase))
        current_step = ""
        plan = getattr(getattr(self.agent, "state", None), "current_plan", None)
        for step in getattr(plan, "steps", []) or []:
            status = str(getattr(getattr(step, "status", None), "value", step.status))
            if status in {"running", "in_progress", "executing", "interrupted"}:
                current_step = str(getattr(step, "id", ""))
                break
        elapsed = 0.0
        if getattr(self, "_task_active", False) and getattr(self, "_start_time", 0.0):
            elapsed = max(0.0, time.time() - self._start_time)
        return render_status_bar(
            session_id=session_id,
            model=model,
            message=message,
            tool_panel_visible=bool(getattr(self, "_tool_panel_visible", False)),
            permission_mode=permission_mode,
            phase=phase,
            current_step=current_step,
            context_utilization=context_utilization,
            elapsed_seconds=elapsed,
        )

    def _cached_context_utilization(self) -> float:
        now = time.monotonic()
        cached_at, cached_value = getattr(self, "_context_status_cache", (0.0, 0.0))
        if now - cached_at < 0.5:
            return cached_value
        context_manager = getattr(self.agent, "context_manager", None)
        context_getter = getattr(context_manager, "get_presentation_snapshot", None)
        utilization = (
            float(context_getter().utilization_percent) if callable(context_getter) else 0.0
        )
        self._context_status_cache = (now, utilization)
        return utilization

    def _set_status(self, text: str) -> None:
        try:
            status = self.query_one("#status-text", Label)
            status.update(self._build_status_text(text))
        except Exception:
            pass

    # ── history ──────────────────────────────────────────────────

    def _load_history(self) -> None:
        try:
            if self._history_path.exists():
                self._history_entries = [
                    line.rstrip("\n")
                    for line in self._history_path.read_text("utf-8").splitlines()
                    if line.strip()
                ]
        except Exception:
            self._history_entries = []

    def _add_to_history(self, entry: str) -> None:
        entry = entry.strip()
        if not entry:
            return
        self._history_entries.append(entry)
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._history_path, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            pass
        self._history_index = -1
        self._saved_input = ""

    def action_history_prev(self) -> None:
        if not self._history_entries:
            return
        try:
            input_widget = self.query_one("#input", Input)
        except Exception:
            return
        if self._history_index < 0:
            self._saved_input = input_widget.value
            self._history_index = len(self._history_entries) - 1
        else:
            self._history_index = max(0, self._history_index - 1)
        input_widget.value = self._history_entries[self._history_index]
        input_widget.cursor_position = len(input_widget.value)

    def action_history_next(self) -> None:
        if self._history_index < 0:
            return
        try:
            input_widget = self.query_one("#input", Input)
        except Exception:
            return
        self._history_index += 1
        if self._history_index >= len(self._history_entries):
            self._history_index = -1
            input_widget.value = self._saved_input
            self._saved_input = ""
        else:
            input_widget.value = self._history_entries[self._history_index]
        input_widget.cursor_position = len(input_widget.value)

    def action_focus_input(self) -> None:
        self._focus_input()

    def action_copy_selection(self) -> None:
        """Copy the current in-place TUI text selection."""
        selected = ""
        with suppress(Exception):
            selected = self.screen.get_selected_text() or ""

        if not selected:
            self._set_status(
                "[yellow]Select text in the messages area, then press Ctrl+C or Cmd+C[/yellow]"
            )
            return

        textual_clipboard_ok = False
        with suppress(Exception):
            self.copy_to_clipboard(selected)
            textual_clipboard_ok = True
        system_clipboard_ok = _copy_to_system_clipboard(selected)

        if textual_clipboard_ok or system_clipboard_ok:
            with suppress(Exception):
                self.screen.clear_selection()
            self._set_status("[green]Copied selection[/green]")
            return

        self._set_status("[yellow]Could not copy selection to clipboard[/yellow]")


async def run_tui(config: Config, startup_resume_mode: str | None = None) -> None:
    """Launch the Textual TUI."""
    agent = AgentRuntime(config)
    app = OpenNovaTUI(agent, config, startup_resume_mode=startup_resume_mode)
    await app.run_async()
