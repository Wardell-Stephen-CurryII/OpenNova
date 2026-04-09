"""
REPL (Read-Eval-Print Loop) for OpenNova CLI.

Provides an interactive command-line interface with:
- Multi-line input support
- Command history
- Built-in commands (/plan, /act, /tools, etc.)
- Rich output rendering
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from opennova.config import Config
from opennova.providers.base import StreamChunk
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.state import Plan, PlanStep
from opennova.tools.base import ToolResult


class Renderer:
    """Rich-based renderer for CLI output."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console(
            force_terminal=True,
            soft_wrap=False,  # Disable soft wrap to allow terminal scrolling
            markup=True,
            highlight=True,
        )

    def print(self, message: str | Any, **kwargs) -> None:
        """Print message to console."""
        self.console.print(message, **kwargs)

    def print_thinking(self, thought: str) -> None:
        """Display thinking process."""
        if thought is None:
            thought = "(thinking...)"
        self.console.print(Panel(thought, title="💭 Thinking", border_style="yellow"))

    def print_action(self, tool_name: str, args: dict[str, Any]) -> None:
        """Display tool action."""
        args_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
        self.console.print(f"[cyan]⚙️  Executing:[/cyan] {tool_name}({args_str})")

    def print_result(self, result: ToolResult) -> None:
        """Display tool result."""
        if result.success:
            style = "green"
            icon = "✅"
        else:
            style = "red"
            icon = "❌"

        output = result.output or ""
        if len(output) > 500:
            output = output[:500] + "\n... [truncated]"

        self.console.print(f"[{style}]{icon} Result:[/{style}]\n{output}")

        if result.error:
            self.console.print(f"[red]Error: {result.error}[/red]")

    def print_stream(self, chunk: StreamChunk) -> None:
        """Display streaming chunk."""
        if chunk.content:
            self.console.print(chunk.content, end="", markup=False)
        if chunk.finish_reason:
            self.console.print()

    def print_plan(self, plan: Plan) -> None:
        """Display a plan."""
        table = Table(title=f"📋 Plan: {plan.task}")
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

        self.console.print(table)

    def print_welcome(self) -> None:
        """Display welcome message."""
        self.console.print(
            Panel.fit(
                "[bold cyan]OpenNova[/bold cyan] - AI Coding Agent\n"
                "Type /help for commands, Ctrl+D to exit",
                border_style="blue",
            )
        )

    def print_help(self) -> None:
        """Display help message."""
        help_text = """
## Commands

- `/plan <task>` - Plan mode: generate a plan before executing
- `/act <task>` - Act mode: execute directly (default)
- `/tools` - List available tools
- `/model` - Show current model info
- `/history` - Show conversation history
- `/clear` - Clear conversation
- `/help` - Show this help
- `/exit` - Exit the REPL
"""
        self.console.print(Markdown(help_text))

    def print_tools(self, tools: list[str]) -> None:
        """Display available tools."""
        table = Table(title="🛠️ Available Tools")
        table.add_column("Tool Name", style="cyan")

        for tool in tools:
            table.add_row(tool)

        self.console.print(table)

    def print_error(self, message: str) -> None:
        """Display error message."""
        self.console.print(f"[red]Error: {message}[/red]")

    def print_success(self, message: str) -> None:
        """Display success message."""
        self.console.print(f"[green]{message}[/green]")

    def print_markdown(self, text: str) -> None:
        """Render markdown text."""
        self.console.print(Markdown(text))

    def print_code(self, code: str, language: str = "python") -> None:
        """Display code with syntax highlighting."""
        self.console.print(Syntax(code, language, theme="monokai"))


class REPL:
    """
    Read-Eval-Print Loop for interactive OpenNova usage.

    Features:
    - Multi-line input with Ctrl+Enter
    - Command history
    - Built-in slash commands
    - Streaming output support
    """

    def __init__(
        self,
        agent: AgentRuntime,
        config: Config,
        history_file: str | None = None,
    ):
        """
        Initialize REPL.

        Args:
            agent: Agent runtime instance
            config: Configuration
            history_file: Path to command history file
        """
        self.agent = agent
        self.config = config
        self.renderer = Renderer()

        history_path = Path(history_file) if history_file else Path.home() / ".opennova" / "history"
        history_path.parent.mkdir(parents=True, exist_ok=True)

        self.session: PromptSession | None = None
        self.history_path = history_path

        self.running = True
        self.current_task: str = ""

    def _setup_key_bindings(self) -> KeyBindings:
        """Set up key bindings."""
        kb = KeyBindings()

        @kb.add("c-d")
        def _(event):
            self.running = False
            event.app.exit()

        @kb.add("c-c")
        def _(event):
            event.app.current_buffer.reset()

        return kb

    def _get_prompt_style(self) -> Style:
        """Get prompt style."""
        return Style.from_dict(
            {
                "prompt": "bold cyan",
            }
        )

    async def start(self) -> None:
        """Start the REPL."""
        import traceback as tb

        self.session = PromptSession(
            history=FileHistory(str(self.history_path)),
            auto_suggest=AutoSuggestFromHistory(),
            multiline=False,
            mouse_support=False,  # Disable mouse support to allow terminal scrolling
            key_bindings=self._setup_key_bindings(),
        )

        self.renderer.print_welcome()

        while self.running:
            try:
                user_input = await self.session.prompt_async(
                    "opennova> ",
                    style=self._get_prompt_style(),
                )

                if user_input is None:
                    continue

                user_input = user_input.strip()

                if not user_input:
                    continue

                await self._handle_input(user_input)

            except KeyboardInterrupt:
                print()
                continue
            except EOFError:
                self.running = False
                break
            except Exception as e:
                print(f"\n[ERROR] {type(e).__name__}: {e}")
                print("Traceback:")
                tb.print_exc()

        self.renderer.print_success("Goodbye!")

    async def _handle_input(self, user_input: str) -> None:
        """Handle user input."""
        if user_input.startswith("/"):
            await self._handle_command(user_input)
        else:
            await self._execute_task(user_input)

    async def _handle_command(self, command: str) -> None:
        """Handle slash command."""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        commands = {
            "/help": self._cmd_help,
            "/plan": self._cmd_plan,
            "/act": self._cmd_act,
            "/tools": self._cmd_tools,
            "/model": self._cmd_model,
            "/clear": self._cmd_clear,
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
            "/history": self._cmd_history,
        }

        handler = commands.get(cmd)
        if handler:
            await handler(args)
        else:
            self.renderer.print_error(f"Unknown command: {cmd}")

    async def _cmd_help(self, args: str) -> None:
        """Show help."""
        self.renderer.print_help()

    async def _cmd_plan(self, args: str) -> None:
        """Execute in plan mode."""
        if not args:
            self.renderer.print_error("Usage: /plan <task>")
            return

        self.renderer.print(f"[yellow]Planning: {args}[/yellow]")

        def on_plan(plan: Plan, plan_file_path: Any = None) -> None:
            self.renderer.print_plan(plan)
            if plan_file_path:
                self.renderer.print(f"[green]Plan saved to:[/green] {plan_file_path}")

        self.agent.register_callback("plan", on_plan)

        result = await self.agent.run(args, mode="plan")
        self.renderer.print_markdown(result)

    async def _cmd_act(self, args: str) -> None:
        """Execute in act mode."""
        if not args:
            self.renderer.print_error("Usage: /act <task>")
            return

        await self._execute_task(args)

    async def _cmd_tools(self, args: str) -> None:
        """List available tools."""
        tools = self.agent.get_tools()
        self.renderer.print_tools(tools)

    async def _cmd_model(self, args: str) -> None:
        """Show model info."""
        info = self.agent.get_model_info()
        table = Table(title="🤖 Model Information")
        table.add_column("Property", style="cyan")
        table.add_column("Value")

        for key, value in info.items():
            table.add_row(key, str(value))

        self.renderer.print(table)

    async def _cmd_clear(self, args: str) -> None:
        """Clear conversation."""
        self.renderer.print_success("Conversation cleared.")

    async def _cmd_history(self, args: str) -> None:
        """Show conversation history."""
        self.renderer.print("Conversation history not yet implemented.")

    async def _cmd_exit(self, args: str) -> None:
        """Exit the REPL."""
        self.running = False

    async def _execute_task(self, task: str) -> None:
        """Execute a task with streaming output."""
        import traceback

        def on_thought(thought: str) -> None:
            self.renderer.print_thinking(thought)

        def on_action(tool_name: str, args: dict) -> None:
            self.renderer.print_action(tool_name, args)

        def on_result(result: ToolResult) -> None:
            self.renderer.print_result(result)

        def on_stream(chunk: StreamChunk) -> None:
            self.renderer.print_stream(chunk)

        self.agent.register_callback("thought", on_thought)
        self.agent.register_callback("action", on_action)
        self.agent.register_callback("result", on_result)
        self.agent.register_callback("stream", on_stream)

        print()
        try:
            result = await self.agent.run(task)
            print()
            if result is None:
                self.renderer.print_error("Task returned None - check logs for details")
            else:
                self.renderer.print_markdown(result)
        except Exception as e:
            print()
            error_msg = f"Exception during task execution: {type(e).__name__}: {e}"
            self.renderer.print_error(error_msg)
            print("\nFull traceback:")
            traceback.print_exc()


async def run_repl(config: Config) -> None:
    """Run the REPL with given configuration."""
    agent = AgentRuntime(config)
    repl = REPL(agent, config)
    await repl.start()
