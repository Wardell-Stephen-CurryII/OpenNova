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
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, Input, Label, RichLog

from opennova.config import Config
from opennova.providers.base import StreamChunk
from opennova.runtime.agent import AgentRuntime
from opennova.tools.base import ToolResult

# Tool names whose results are not displayed (verbose file ops).
_SUPPRESSED_RESULT_TOOLS = {"list_directory", "read_file"}


class OpenNovaTUI(App):
    """Textual TUI application for OpenNova with split-pane layout."""

    CSS = """
    #messages-container {
        height: 1fr;
        overflow-y: auto;
        border-bottom: solid $primary;
    }

    #messages {
        height: 100%;
    }

    #input-container {
        dock: bottom;
        height: auto;
        padding: 0 1;
    }

    #input {
        width: 100%;
    }

    #status-bar {
        dock: bottom;
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
        Binding("tab", "complete", "Complete", show=False),
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
        history_path = Path(history_file) if history_file else Path.home() / ".opennova" / "history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_path = history_path
        self._history_entries: list[str] = []
        self._history_index: int = -1
        self._saved_input: str = ""

        self._running: bool = False
        self._agent_task: asyncio.Task | None = None
        self._interaction_future: asyncio.Future | None = None
        self._interaction_mode: bool = False
        self._completion_state: dict[str, Any] = {}
        self._start_time: float = 0.0

    # ── lifecycle ────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="messages-container"):
            yield RichLog(id="messages", highlight=True, markup=True, wrap=True, max_lines=10000)
        with Container(id="status-bar"):
            yield Label(id="status-text", markup=False)
        with Container(id="input-container"):
            yield Input(
                id="input",
                placeholder="Type a message or /command...",
            )
        yield Footer()

    def on_mount(self) -> None:
        self._load_history()
        self._show_welcome()
        self.query_one("#input", Input).focus()

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

        log = self.query_one("#messages", RichLog)
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
                f"[dim]Type [bold]/help[/bold] for commands  ·  [bold]Ctrl+C[/bold] to cancel  ·  [bold]Ctrl+D[/bold] to exit[/dim]",
                border_style="bright_blue",
                padding=(1, 3),
            )
        )
        log.write("")

    # ── input dispatch ───────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        # Interaction mode: answer is routed to the pending future.
        if self._interaction_mode:
            if self._interaction_future and not self._interaction_future.done():
                self._interaction_future.set_result(text)
            return

        # Don't process new tasks while agent is running.
        if self._running:
            return

        input_widget = self.query_one("#input", Input)
        input_widget.value = ""

        self._add_to_history(text)

        if text.startswith("/"):
            await self._handle_command(text)
        else:
            await self._execute_task(text)

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
    }

    async def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().replace("_", "-")
        args = parts[1] if len(parts) > 1 else ""

        method_name = self._COMMAND_MAP.get(cmd)
        if method_name:
            handler = getattr(self, method_name)
            await handler(args)
        else:
            log = self.query_one("#messages", RichLog)
            log.write(f"[red]Unknown command: {cmd}[/red]")

    # ── slash commands ───────────────────────────────────────────

    async def _cmd_help(self, args: str) -> None:
        log = self.query_one("#messages", RichLog)
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
- `/clear` - Clear conversation state
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
            log = self.query_one("#messages", RichLog)
            log.write("[red]Usage: /act <task>[/red]")
            return
        await self._execute_task(args)

    # ── task execution ───────────────────────────────────────────

    async def _execute_task(self, task: str) -> None:
        self._running = True
        self._start_time = time.time()
        self._register_callbacks()
        self.agent.register_callback("interaction", self._handle_interaction)

        input_widget = self.query_one("#input", Input)
        input_widget.disabled = True
        input_widget.placeholder = "Working..."

        self._agent_task = asyncio.create_task(self.agent.run(task))

        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        i = 0
        try:
            while not self._agent_task.done():
                elapsed = time.time() - self._start_time
                frame = frames[i % len(frames)]
                self._set_status(f"  {frame} Working... ({elapsed:.0f}s)")
                i += 1
                await asyncio.sleep(0.1)

            result = self._agent_task.result()
            self._set_status("")
            if result:
                log = self.query_one("#messages", RichLog)
                log.write(Markdown(result))
        except asyncio.CancelledError:
            self._set_status("")
            log = self.query_one("#messages", RichLog)
            log.write("[yellow]Task cancelled[/yellow]")
        except Exception as e:
            self._set_status("")
            log = self.query_one("#messages", RichLog)
            log.write(f"[red]Error: {type(e).__name__}: {e}[/red]")
        finally:
            self._running = False
            self._agent_task = None
            input_widget.disabled = False
            input_widget.placeholder = "Type a message or /command..."
            input_widget.focus()

    def _register_callbacks(self) -> None:
        _current_tool: dict[str, str] = {"name": ""}

        def on_thought(thought: str) -> None:
            log = self.query_one("#messages", RichLog)
            log.write(Panel(thought, title="Thinking", border_style="yellow"))

        def on_action(tool_name: str, args: dict) -> None:
            _current_tool["name"] = tool_name
            log = self.query_one("#messages", RichLog)
            args_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
            log.write(f"[cyan]Executing:[/cyan] {tool_name}({args_str})")

        def on_result(result: ToolResult) -> None:
            if _current_tool["name"] in _SUPPRESSED_RESULT_TOOLS:
                return
            log = self.query_one("#messages", RichLog)
            if result.success:
                log.write("[green]Result:[/green]")
            else:
                log.write("[red]Result:[/red]")
            output = (result.output or "")[:500]
            if output:
                log.write(output)
            if result.error:
                log.write(f"[red]Error: {result.error}[/red]")
            diff = result.metadata.get("diff") if result.success else None
            if diff:
                self._write_diff(log, diff)

        def on_stream(chunk: StreamChunk) -> None:
            log = self.query_one("#messages", RichLog)
            if chunk.content:
                log.write(chunk.content, markup=False)
            if chunk.finish_reason:
                log.write("")

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

        log = self.query_one("#messages", RichLog)
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

        log.write(Panel("\n".join(dialog_lines), border_style="cyan", padding=(1, 2)))

        self._interaction_mode = True
        input_widget = self.query_one("#input", Input)
        input_widget.disabled = False
        input_widget.placeholder = (
            "Your answer (Enter to skip): "
            if free_text
            else f"Select option{'s' if payload.get('multi_select') else ''}: "
        )
        input_widget.focus()

        loop = asyncio.get_running_loop()
        self._interaction_future = loop.create_future()
        try:
            answer = await self._interaction_future
        finally:
            self._interaction_future = None
            self._interaction_mode = False
            input_widget.disabled = True
            input_widget.placeholder = "Working..."

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

    def _write_diff(self, log: RichLog, diff_text: str) -> None:
        lines = diff_text.splitlines()
        if len(lines) > 120:
            lines = lines[:120]
            truncated = True
        else:
            truncated = False

        log.write("")
        for line in lines:
            if line.startswith("---") or line.startswith("+++"):
                log.write(Text(line, style="bold cyan"))
            elif line.startswith("@@"):
                log.write(Text(line, style="bold blue"))
            elif line.startswith("+"):
                log.write(Text(line, style="on green"))
            elif line.startswith("-"):
                log.write(Text(line, style="on red"))
            else:
                log.write(Text(line, style="dim"))

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
        if self._history_path.exists():
            self._history_entries = [
                line.rstrip("\n")
                for line in self._history_path.read_text("utf-8").splitlines()
                if line.strip()
            ]

    def _add_to_history(self, entry: str) -> None:
        entry = entry.strip()
        if not entry:
            return
        self._history_entries.append(entry)
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._history_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        self._history_index = -1
        self._saved_input = ""

    def action_history_prev(self) -> None:
        if not self._history_entries:
            return
        input_widget = self.query_one("#input", Input)
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
        input_widget = self.query_one("#input", Input)
        self._history_index += 1
        if self._history_index >= len(self._history_entries):
            self._history_index = -1
            input_widget.value = self._saved_input
            self._saved_input = ""
        else:
            input_widget.value = self._history_entries[self._history_index]
        input_widget.cursor_position = len(input_widget.value)

    # ── tab completion ───────────────────────────────────────────

    def action_complete(self) -> None:
        input_widget = self.query_one("#input", Input)
        text = input_widget.value
        cursor_pos = input_widget.cursor_position
        if not text:
            return

        matches = self._get_completions(text, cursor_pos)
        if not matches:
            return

        state = self._completion_state
        if state.get("prefix") != text:
            state["prefix"] = text
            state["index"] = 0
            state["matches"] = matches
        else:
            state["index"] = (state["index"] + 1) % len(matches)

        completion = state["matches"][state["index"]]
        input_widget.value = completion
        input_widget.cursor_position = len(completion)

    def _get_completions(self, text: str, cursor_pos: int) -> list[str]:
        if text.startswith("/"):
            return self._slash_completions(text)

        # History-based completion
        seen: set[str] = set()
        results: list[str] = []
        current_word = text.split()[-1] if text else text

        for entry in self._history_entries:
            entry_stripped = entry.strip()
            if not entry_stripped:
                continue
            if entry_stripped.startswith(text) and entry_stripped != text:
                if entry_stripped not in seen:
                    seen.add(entry_stripped)
                    results.append(entry_stripped)
            for word in entry_stripped.split():
                if len(word) > 1 and word.startswith(current_word) and word != current_word:
                    if word not in seen:
                        seen.add(word)
                        results.append(word)

        return results

    def _slash_completions(self, text: str) -> list[str]:
        parts = text.split()
        ends_with_space = text.endswith(" ")

        if len(parts) <= 1 and not ends_with_space:
            token = parts[0] if parts else ""
            normalized = token.replace("_", "-")
            return [cmd for cmd in self._COMMAND_MAP if cmd.startswith(normalized)]

        if parts and parts[0].lower().replace("_", "-") == "/skill":
            skills = self.agent.get_skills() if hasattr(self.agent, "get_skills") else []
            skill_token = parts[1] if len(parts) >= 2 else ""
            return [s for s in skills if s.startswith(skill_token)]

        return []

    # ── key bindings ─────────────────────────────────────────────

    def action_cancel(self) -> None:
        if self._agent_task is not None and not self._agent_task.done():
            self._agent_task.cancel()
            return
        # No running task — quit the app (double-Ctrl+C equivalent).
        self.exit()

    def action_quit_app(self) -> None:
        self.exit()

    def action_focus_input(self) -> None:
        self.query_one("#input", Input).focus()


async def run_tui(config: Config) -> None:
    """Launch the Textual TUI."""
    agent = AgentRuntime(config)
    app = OpenNovaTUI(agent, config)
    await app.run_async()
