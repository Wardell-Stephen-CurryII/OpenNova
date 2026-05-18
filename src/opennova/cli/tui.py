
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
import time
from pathlib import Path
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, Input, Label, RichLog, TextArea

from opennova.config import Config
from opennova.providers.base import StreamChunk
from opennova.runtime.agent import AgentRuntime
from opennova.tools.base import ToolResult

# Tool names whose result outputs are not displayed (verbose file ops).
_SUPPRESSED_RESULT_TOOLS = {"list_directory", "read_file"}

# Tool names where the "Result:" label is shown but raw stdout is hidden.
_SUPPRESSED_RESULT_OUTPUT = {"execute_command"}


class _MessagesLog(RichLog):
    """RichLog that shows a selectable TextArea overlay when clicked."""

    can_focus = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._plain_lines: list[str] = []

    def write(self, text: Any, *args: Any, **kwargs: Any) -> None:
        super().write(text, *args, **kwargs)
        self._plain_lines.append(_to_plain(text))

    def clear_messages(self) -> None:
        self.clear()
        self._plain_lines.clear()

    def get_plain_text(self) -> str:
        return "\n".join(self._plain_lines)

    def on_click(self) -> None:
        """Show the selectable text overlay when user clicks the message area."""
        try:
            self.app._show_copy_overlay()
        except Exception:
            pass


class _CopyOverlay(TextArea):
    """Selectable text overlay for copying message content."""

    can_focus = True

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            try:
                self.app.action_focus_input()
            except Exception:
                pass
            return
        if event.key == "ctrl+c":
            # Let TextArea copy, then auto-dismiss
            await super()._on_key(event)
            try:
                self.app.action_focus_input()
            except Exception:
                pass
            return
        # Forward all other keys to Input
        await super()._on_key(event)
        try:
            self.app.query_one("#input", Input).focus()
        except Exception:
            pass


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
            return re.sub(r'\x1b\[[0-9;]*m', '', capture.get()).rstrip("\n")
        if isinstance(text, str):
            return Text.from_markup(text).plain
        return str(text)
    except Exception:
        return str(text)


class OpenNovaTUI(App):
    """Textual TUI application for OpenNova with split-pane layout."""

    CSS = """
    #messages-area {
        height: 1fr;
    }

    #messages {
        height: 1fr;
        overflow-y: auto;
    }

    #copy-overlay {
        display: none;
        height: 1fr;
        border: thick $accent;
    }

    #copy-overlay.visible {
        display: block;
    }

    #input-container {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
    }

    #input {
        width: 100%;
    }

    #suggestions {
        width: 100%;
        height: 1;
        color: $text-disabled;
    }

    #status-bar {
        height: 1;
        background: $surface;
    }

    #status-text {
        width: 100%;
    }

    RichLog {
        scrollbar-size: 1 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel", show=True),
        Binding("ctrl+d", "quit_app", "Quit", show=True),
        Binding("up", "history_prev", "Previous", show=False),
        Binding("down", "history_next", "Next", show=False),
        Binding("tab", "complete", "Complete", show=False, priority=True),
        Binding("escape", "focus_input", "", show=False),
    ]

    def __init__(
        self,
        agent: AgentRuntime,
        config: Config | None = None,
        history_file: str | None = None,
    ):
        super().__init__()
        self.agent = agent
        self.config = config
        history_path = (
            Path(history_file) if history_file else Path.home() / ".opennova" / "history"
        )
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
        self._last_ctrl_c: float = 0.0
        # Guard against duplicate Submitted events from a single Enter press
        self._last_submitted_text: str = ""
        self._last_submitted_time: float = 0.0

    # ── lifecycle ────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="messages-area"):
            yield _MessagesLog(
                id="messages",
                highlight=True,
                markup=True,
                wrap=True,
                max_lines=10000,
            )
            yield _CopyOverlay(
                id="copy-overlay",
                read_only=True,
                show_line_numbers=False,
                soft_wrap=True,
            )
        with Container(id="status-bar"):
            yield Label(id="status-text", markup=False)
        with Container(id="input-container"):
            yield Input(
                id="input",
                placeholder="Type a message or /command...",
            )
            yield Label(id="suggestions", markup=True)

    def on_mount(self) -> None:
        self._load_history()
        self._show_welcome()
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        """Ensure input always has focus."""
        try:
            inp = self.query_one("#input", Input)
            inp.focus()
        except Exception:
            pass

    # ── welcome ──────────────────────────────────────────────────

    _BANNER = (
        " ██████╗ ██████╗ ███████╗███╗   ██╗███╗   ██╗ ██████╗ ██╗   ██╗ █████╗ \n"
        "██╔═══██╗██╔══██╗██╔════╝████╗  ██║████╗  ██║██╔═══██╗██║   ██║██╔══██╗\n"
        "██║   ██║██████╔╝█████╗  ██╔██╗ ██║██╔██╗ ██║██║   ██║██║   ██║███████║\n"
        "██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║\n"
        "╚██████╔╝██║     ███████╗██║ ╚████║██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║\n"
        " ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝"
    )

    def _show_welcome(self) -> None:
        from opennova import __version__

        log = self.query_one("#messages")
        for line in self._BANNER.split("\n"):
            log.write(f"[bold cyan]{line}[/bold cyan]")
        log.write("")
        model_info = self.agent.get_model_info()
        provider = model_info.get("provider", "—")
        model = model_info.get("model", "—")
        log.write(
            Panel.fit(
                f"[bold]AI Coding Agent[/bold] [dim]v{__version__}[/dim]\n\n"
                f"[dim]Provider:[/dim] [green]{provider}[/green]  ·  "
                f"[dim]Model:[/dim] [yellow]{model}[/yellow]\n\n"
                f"[dim]Type [bold]/help[/bold] for commands  ·  [bold]Ctrl+C[/bold] to cancel[/dim]",
                border_style="bright_blue",
                padding=(1, 3),
            )
        )
        log.write("")

    # ── key bindings ─────────────────────────────────────────────

    def action_cancel(self) -> None:
        """Cancel the running agent task, or double-press to exit."""
        if self._is_agent_running():
            self._agent_task.cancel()
            self._set_status("[yellow]Cancelling...[/yellow]")
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

    # ── safe state reset ─────────────────────────────────────────

    def _reset_input_state(self) -> None:
        """Unconditionally reset running state and re-enable input.

        Called in every finally block and can also be called as an
        emergency recovery so the UI never gets permanently stuck.
        """
        self._task_active = False
        self._agent_task = None
        self._set_status("")
        try:
            input_widget = self.query_one("#input", Input)
            input_widget.disabled = False
            input_widget.placeholder = "Type a message or /command..."
        except Exception:
            pass
        self.call_after_refresh(self._focus_input)

    def _clear_suggestions(self) -> None:
        """Clear the suggestions label and completion state."""
        try:
            self.query_one("#suggestions", Label).update("")
        except Exception:
            pass
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
        # If after "/skill ", complete skill names
        if text.startswith("/skill ") or text == "/skill":
            skill_prefix = text[len("/skill"):].lstrip()
            skills = self.agent.get_skills()
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
            all_cmds = list(self._COMMAND_MAP.keys())
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
            if entry_stripped.startswith(text) and entry_stripped != text:
                if entry_stripped not in seen:
                    seen.add(entry_stripped)
                    matches.append(entry_stripped)
        return matches

    def _show_suggestions(self, matches: list[str], current_idx: int) -> None:
        """Display completion matches in the suggestions label.

        current_idx < 0 means no highlight (real-time hint mode).
        """
        try:
            label = self.query_one("#suggestions", Label)
            display = matches[:8]
            if current_idx >= len(display):
                current_idx = 0
            parts: list[str] = []
            for i, m in enumerate(display):
                if i == current_idx:
                    parts.append(f"[reverse]{m}[/reverse]")
                else:
                    parts.append(f"[dim]{m}[/dim]")
            suffix = " …" if len(matches) > 8 else ""
            label.update("  ".join(parts) + suffix)
        except Exception:
            pass

    def _is_agent_running(self) -> bool:
        """Return True when an agent task is running or being set up."""
        return self._agent_task is not None and not self._agent_task.done()
    
    # ── input dispatch ───────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Show completion hints in real-time as the user types."""
        text = event.value
        if not text:
            self._clear_suggestions()
            return

        matches = self._get_completions(text)
        if matches:
            self._show_suggestions(matches, -1)
        else:
            self._clear_suggestions()

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

        # Don't process new tasks while agent is running.
        if self._is_agent_running():
            self._set_status("[yellow]Agent is busy, please wait...[/yellow]")
            return

        # Clear input and echo user message.
        input_widget = self.query_one("#input", Input)
        input_widget.value = ""
        self._clear_suggestions()

        # Interaction mode: answer is routed to the pending future.
        if self._interaction_mode:
            if self._interaction_future and not self._interaction_future.done():
                self._interaction_future.set_result(text)
            return

        self._add_to_history(text)

        log = self.query_one("#messages")
        log.write(f"[bold]You:[/bold] {text}")
        log.scroll_end(animate=False)

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

    _COMMAND_MAP: dict[str, str] = {
        "/help": "_cmd_help",
        "/plan": "_cmd_plan",
        "/act": "_cmd_act",
        "/tools": "_cmd_tools",
        "/skills": "_cmd_skills",
        "/skill": "_cmd_skill",
        "/reload-skills": "_cmd_reload_skills",
        "/model": "_cmd_model",
        "/config": "_cmd_config",
        "/clear": "_cmd_clear",
        "/exit": "_cmd_exit",
        "/quit": "_cmd_exit",
        "/history": "_cmd_history",
        "/resume": "_cmd_resume",
        "/sessions": "_cmd_sessions",
    }

    # Commands that return quickly and can be awaited synchronously
    _SYNC_COMMANDS: set[str] = {
        "/help", "/tools", "/skills", "/model", "/config",
        "/clear", "/exit", "/quit", "/history", "/reload-skills",
        "/resume", "/sessions",
    }

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

        method_name = self._COMMAND_MAP.get(cmd)
        if method_name:
            handler = getattr(self, method_name)
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
- `/config` - Show current configuration
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
        await self._execute_task(args)

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
        table.add_column("Description")
        names = skill_registry.list_skills()
        if not names:
            log.write("[yellow]No skills loaded.[/yellow]")
            return

        for name in sorted(names):
            info = skill_registry.get_skill_info(name) or {}
            table.add_row(name, info.get("description", ""))
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
        log.write(f"[green]Invoked skill: {skill_name}[/green]")

        from opennova.providers.base import Message

        self.agent.context_manager.add_message(
            Message(
                role="user",
                content=f"Invoked skill '{skill_name}':\n\n{skill_prompt}",
            )
        )

        task = f"/skill {skill_name} {skill_args}".strip()
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
            prompt = (s.first_prompt or "—")[:80]
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
            sessions = self.agent.get_sessions()
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
        else:
            # No args: show picker (list recent sessions)
            sessions = self.agent.get_sessions()
            current_id = self.agent.session_manager.session_id
            # Filter out current session
            sessions = [s for s in sessions if s.session_id != current_id]
            if not sessions:
                log.write("[yellow]No past sessions to resume.[/yellow]")
                return
            # Auto-pick the most recent
            session_id = sessions[0].session_id

        try:
            messages = self.agent.resume_session(session_id)
            log.write(
                f"[green]Resumed session [bold]{session_id[:8]}[/bold] "
                f"({len(messages)} messages restored).[/green]"
            )
        except Exception as e:
            log.write(f"[red]Failed to resume session: {e}[/red]")

    async def _cmd_plan(self, args: str) -> None:
        log = self.query_one("#messages")
        if not args:
            log.write("[red]Usage: /plan <task>[/red]")
            return

        log.write(f"[yellow]Planning: {args}[/yellow]")

        def on_plan(plan, plan_file_path=None):
            try:
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
                }
                for step in plan.steps:
                    icon = status_icons.get(step.status.value, "❓")
                    table.add_row(step.id, step.description, icon)
                _log.write(table)
                if plan_file_path:
                    _log.write(f"[green]Plan saved to:[/green] {plan_file_path}")
            except Exception:
                pass

        self.agent.register_callback("plan", on_plan)

        # Phase 1: Generate the plan (not running state — user can still cancel)
        try:
            result = await self.agent.run(args, mode="plan")
            log.write(Markdown(result))
        except Exception as e:
            log.write(f"[red]Planning failed: {type(e).__name__}: {e}[/red]")
            return

        # Phase 2: Ask for plan approval via interaction
        log.write("[cyan]Execute this plan now? [y/N][/cyan]")
        answer = await self._ask_user(placeholder="Execute this plan now? [y/N]: ")

        if answer.strip().lower() not in {"y", "yes"}:
            log.write("[yellow]Plan kept for later execution.[/yellow]")
            return

        # Phase 3: Execute approved plan — fully guarded by try/finally
        self.agent.state.mark_plan_approved()
        log.write("[cyan]Executing approved plan...[/cyan]")
        await self._run_agent_task(self.agent.execute_approved_plan())

    # ── interaction helper ───────────────────────────────────────

    async def _ask_user(self, placeholder: str = "Your answer: ") -> str:
        """Block until the user types a response in the input box.

        Used for plan approval and agent interaction prompts.
        """
        self._interaction_mode = True
        input_widget = self.query_one("#input", Input)
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

        try:
            input_widget = self.query_one("#input", Input)
            log = self.query_one("#messages")

            self._register_callbacks()
            self.agent.register_callback("interaction", self._handle_interaction)

            input_widget.disabled = True
            input_widget.placeholder = "Working..."
            await asyncio.sleep(0)  # yield a frame so UI updates

            self._agent_task = asyncio.create_task(coro)

            # Spinner loop
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            i = 0
            while not self._agent_task.done():
                elapsed = time.time() - self._start_time
                frame = frames[i % len(frames)]
                self._set_status(f"  {frame} Working... ({elapsed:.0f}s)")
                i += 1
                await asyncio.sleep(0.1)

            result = self._agent_task.result()
            self._set_status("")
            if result:
                log.write(Markdown(result))
                log.scroll_end(animate=False)
            return result

        except asyncio.CancelledError:
            self._set_status("")
            log.write("[yellow]Task cancelled[/yellow]")
            return None
        except Exception as e:
            self._set_status("")
            log.write(f"[red]Error: {type(e).__name__}: {e}[/red]")
            return None
        finally:
            self._reset_input_state()

    async def _execute_task(self, task: str, preserve_context: bool = True) -> None:
        """Execute a user task through the agent.

        By default preserves context so the conversation accumulates across
        turns within a session. The ReActLoop handles first-turn setup
        (system prompt injection) correctly even with preserve_context=True.
        """
        await self._run_agent_task(
            self.agent._run_act_mode(
                task=task, stream=True,
                preserve_context=preserve_context,
            )
        )

    def _register_callbacks(self) -> None:
        _current_tool: dict[str, str] = {"name": ""}

        _stream_buffer: list[str] = [""]  # mutable so closure can reassign

        def on_thought(thought: str) -> None:
            try:
                log = self.query_one("#messages")
                log.write(Panel(thought, title="Thinking", border_style="yellow"))
            except Exception:
                pass

        def on_action(tool_name: str, args: dict) -> None:
            _current_tool["name"] = tool_name
            try:
                log = self.query_one("#messages")
                args_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
                log.write(f"[cyan]Executing:[/cyan] {tool_name}({args_str})")
            except Exception:
                pass

        def on_result(result: ToolResult) -> None:
            if _current_tool["name"] in _SUPPRESSED_RESULT_TOOLS:
                return
            try:
                log = self.query_one("#messages")
                if result.success:
                    log.write("[green]Result:[/green]")
                else:
                    log.write("[red]Result:[/red]")
                if _current_tool["name"] not in _SUPPRESSED_RESULT_OUTPUT:
                    output = (result.output or "")[:500]
                    if output:
                        log.write(output)
                if result.error:
                    log.write(f"[red]Error: {result.error}[/red]")
                diff = result.metadata.get("diff") if result.success else None
                if diff:
                    self._write_diff(log, diff)
            except Exception:
                pass

        def on_stream(chunk: StreamChunk) -> None:
            try:
                log = self.query_one("#messages")
                if chunk.content:
                    content = chunk.content
                    # Buffer content and write only on natural line breaks
                    # to avoid each tiny chunk becoming its own RichLog line.
                    combined = _stream_buffer[0] + content
                    lines = combined.split("\n")
                    # Write all complete lines; keep the last (possibly partial) line in buffer
                    for line in lines[:-1]:
                        if line:
                            log.write(line)
                    _stream_buffer[0] = lines[-1]
                if chunk.finish_reason:
                    # Flush any remaining buffered content
                    if _stream_buffer[0]:
                        log.write(_stream_buffer[0])
                        _stream_buffer[0] = ""
                    log.write("")
            except Exception:
                pass

        self.agent.register_callback("thought", on_thought)
        self.agent.register_callback("action", on_action)
        self.agent.register_callback("result", on_result)
        self.agent.register_callback("stream", on_stream)

    # ── interaction ──────────────────────────────────────────────

    async def _handle_interaction(self, metadata: dict[str, Any]) -> dict[str, Any]:
        payload = metadata.get("prompt_payload", {})
        question = payload.get("question", "")
        options = payload.get("options", [])
        free_text = payload.get("free_text", False)
        header = payload.get("header")

        log = self.query_one("#messages")
        dialog_lines = [f"[bold]{question}[/bold]"]
        if header:
            dialog_lines.insert(0, f"[cyan][{header}][/cyan]")
        if free_text:
            dialog_lines.append("[dim](Press Enter to skip — model will decide)[/dim]")
        else:
            for opt in options:
                idx = opt.get("index", "?")
                label = opt.get("label", "")
                desc = opt.get("description", "")
                line = f"  [[{idx}]] [yellow]{label}[/yellow]"
                if desc:
                    line += f"\n      [dim]{desc}[/dim]"
                dialog_lines.append(line)

        log.write(
            Panel("\n".join(dialog_lines), border_style="cyan", padding=(1, 2))
        )

        placeholder = (
            "Your answer (Enter to skip): "
            if free_text
            else f"Select option{'s' if payload.get('multi_select') else ''}: "
        )
        answer = await self._ask_user(placeholder=placeholder)

        answer = answer.strip()
        if free_text:
            if not answer:
                return {
                    "answer": None,
                    "skipped": True,
                    "answers": {question: None},
                    "display": "(skipped — model will decide)",
                }
            return {
                "answer": answer,
                "skipped": False,
                "answers": {question: answer},
                "display": answer,
            }

        # Choice mode
        chosen = [opt for opt in options if opt.get("label", "") == answer]
        if not chosen:
            # Try numeric index
            try:
                idx = int(answer)
                chosen = [opt for opt in options if opt.get("index") == idx]
            except ValueError:
                pass
        if not chosen:
            return {
                "answer": answer,
                "skipped": True,
                "answers": {question: None},
                "display": answer,
            }

        opt = chosen[0]
        return {
            "answer": opt.get("label", answer),
            "skipped": False,
            "answers": {question: opt.get("label", answer)},
            "selected_options": [opt],
            "display": opt.get("label", answer),
        }

    # ── diff display ─────────────────────────────────────────────

    def _write_diff(self, log: _MessagesLog, diff_text: str) -> None:
        lines = diff_text.splitlines()
        if len(lines) > 120:
            lines = lines[:120]
            truncated = True
        else:
            truncated = False

        log.write("")
        for line in lines:
            if line.startswith("+") or line.startswith("-") or line.startswith("@@"):
                log.write(line)
            else:
                log.write(line)

        if truncated:
            log.write("[dim]... (diff truncated)[/dim]")
        log.write("")

    # ── status bar ───────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        try:
            status = self.query_one("#status-text", Label)
            status.update(text)
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
        self._hide_copy_overlay()

    def _show_copy_overlay(self) -> None:
        """Populate the copy overlay with plain text and show it."""
        try:
            log = self.query_one("#messages", RichLog)
            overlay = self.query_one("#copy-overlay", TextArea)
            overlay.load_text(log.get_plain_text())
            overlay.add_class("visible")
            overlay.focus()
            # Scroll to bottom so latest content is visible
            overlay.move_cursor((0, 0))
        except Exception:
            pass

    def _hide_copy_overlay(self) -> None:
        """Hide the copy overlay and return focus to Input."""
        try:
            overlay = self.query_one("#copy-overlay", TextArea)
            overlay.remove_class("visible")
        except Exception:
            pass
        self._focus_input()

async def run_tui(config: Config) -> None:
    """Launch the Textual TUI."""
    agent = AgentRuntime(config)
    app = OpenNovaTUI(agent, config)
    await app.run_async()