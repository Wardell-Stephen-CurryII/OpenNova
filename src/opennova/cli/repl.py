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

import yaml
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
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


class SlashCommandCompleter(Completer):
    """Tab completion for REPL slash commands, skill names, and history."""

    def __init__(self, repl: "REPL"):
        self.repl = repl

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        # Slash command completion
        if text.startswith("/"):
            yield from self._slash_completions(text)
            return

        # History-based completion for regular text
        if not text.strip():
            return

        yield from self._history_completions(text)

    def _slash_completions(self, text: str):
        parts = text.split()
        ends_with_space = text.endswith(" ")

        if len(parts) <= 1 and not ends_with_space:
            token = parts[0] if parts else ""
            normalized = token.replace("_", "-")
            for command in self.repl._get_slash_commands():
                if command.startswith(normalized):
                    yield Completion(command, start_position=-len(token))
            return

        if not parts:
            return

        command = parts[0].lower().replace("_", "-")
        if command != "/skill":
            return

        skills = self.repl.agent.get_skills() if hasattr(self.repl.agent, "get_skills") else []
        skill_token = ""
        if len(parts) >= 2:
            skill_token = parts[1]
        elif not ends_with_space:
            return

        for skill in skills:
            if skill.startswith(skill_token):
                yield Completion(skill, start_position=-len(skill_token))

    def _history_completions(self, text: str):
        """Yield completions from command history.

        Matches history entries that start with the typed text, and also
        suggests individual words from history that match the current word.
        """
        try:
            history = self.repl.session.history
        except Exception:
            return

        seen = set()
        current_word = text.split()[-1] if text else text

        for item in history.get_strings():
            item_stripped = item.strip()
            if not item_stripped:
                continue

            # Complete whole history entry if it starts with typed text
            if item_stripped.startswith(text) and item_stripped != text:
                if item_stripped not in seen:
                    seen.add(item_stripped)
                    yield Completion(
                        item_stripped,
                        start_position=-len(text),
                        display=item_stripped[:80],
                    )

            # Complete individual word from history matching the last word
            for word in item_stripped.split():
                if (
                    len(word) > 1
                    and word.startswith(current_word)
                    and word != current_word
                ):
                    if word not in seen:
                        seen.add(word)
                        yield Completion(
                            word,
                            start_position=-len(current_word),
                        )


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

        diff = result.metadata.get("diff") if result.success else None
        if diff:
            self.print_diff(diff)

    def print_diff(self, diff_text: str) -> None:
        """Display a unified diff with colored backgrounds.

        Deletions are shown on a red background, additions on green.
        """
        from rich.text import Text

        lines = diff_text.splitlines()
        # Limit diff display to avoid flooding the terminal.
        if len(lines) > 120:
            lines = lines[:120]
            truncated = True
        else:
            truncated = False

        self.console.print()
        for line in lines:
            if line.startswith("---") or line.startswith("+++"):
                self.console.print(f"[bold cyan]{line}[/bold cyan]")
            elif line.startswith("@@"):
                self.console.print(f"[bold blue]{line}[/bold blue]")
            elif line.startswith("+"):
                self.console.print(Text(line, style="on green"))
            elif line.startswith("-"):
                self.console.print(Text(line, style="on red"))
            else:
                self.console.print(f"[dim]{line}[/dim]")

        if truncated:
            self.console.print("[dim]... (diff truncated)[/dim]")
        self.console.print()

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

    _BANNER = (
        " ██████╗ ██████╗ ███████╗███╗   ██╗███╗   ██╗ ██████╗ ██╗   ██╗ █████╗ \n"
        "██╔═══██╗██╔══██╗██╔════╝████╗  ██║████╗  ██║██╔═══██╗██║   ██║██╔══██╗\n"
        "██║   ██║██████╔╝█████╗  ██╔██╗ ██║██╔██╗ ██║██║   ██║██║   ██║███████║\n"
        "██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║\n"
        "╚██████╔╝██║     ███████╗██║ ╚████║██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║\n"
        " ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝"
    )

    def print_welcome(self, model_info: dict[str, Any] | None = None) -> None:
        """Display welcome message with model info."""
        from opennova import __version__

        provider = model_info.get("provider", "—") if model_info else "—"
        model = model_info.get("model", "—") if model_info else "—"

        self.console.print()
        for line in self._BANNER.split("\n"):
            self.console.print(f"[bold cyan]{line}[/bold cyan]")
        self.console.print()
        self.console.print(
            Panel.fit(
                f"[bold]AI Coding Agent[/bold] [dim]v{__version__}[/dim]\n\n"
                f"[dim]Provider:[/dim] [green]{provider}[/green]  ·  "
                f"[dim]Model:[/dim] [yellow]{model}[/yellow]\n\n"
                f"[dim]Type [bold]/help[/bold] for commands  ·  [bold]Ctrl+C×2[/bold] to exit[/dim]",
                border_style="bright_blue",
                padding=(1, 3),
            )
        )

    def print_help(self) -> None:
        """Display help message."""
        help_text = """
## Commands

- `/plan <task>` - Plan mode: generate a plan before executing
- `/act <task>` - Act mode: execute directly (default)
- `/tools` - List available tools
- `/skills` - List loaded skills and invocation status
- `/skill <name> [args]` - Invoke a loaded skill directly
- `/reload-skills` - Reload skills from disk
- `/model` - Show current model info
- `/config` - Show current configuration
- `/history [n]` - Show recent conversation history
- `/clear` - Clear conversation state
- `/help` - Show this help
- `/exit` - Exit the REPL

## Tips

- Press `Tab` to complete slash commands, skill names, and history entries
- Start typing a command you've used before — Tab will match it from history
- Ghost text suggestions are also shown from history
"""
        self.console.print(Markdown(help_text))

    def print_tools(self, tools: list[str]) -> None:
        """Display available tools."""
        table = Table(title="🛠️ Available Tools")
        table.add_column("Tool Name", style="cyan")

        for tool in tools:
            table.add_row(tool)

        self.console.print(table)

    def print_skills(self, skills: list[dict[str, Any]]) -> None:
        """Display loaded skills."""
        table = Table(title="🧩 Loaded Skills")
        table.add_column("Name", style="cyan")
        table.add_column("Enabled", justify="center")
        table.add_column("Source")
        table.add_column("Model", justify="center")
        table.add_column("User", justify="center")
        table.add_column("Description")

        for skill in skills:
            table.add_row(
                skill.get("name", ""),
                "Yes" if skill.get("enabled", True) else "No",
                str(skill.get("source_type", "")),
                "Yes" if skill.get("model_invocable", False) else "No",
                "Yes" if skill.get("user_invocable", False) else "No",
                str(skill.get("description", "")),
            )

        self.console.print(table)

    def print_history(self, history: list[dict[str, str]]) -> None:
        """Display conversation history."""
        table = Table(title="🕘 Conversation History")
        table.add_column("Role", style="cyan")
        table.add_column("Content")
        table.add_column("Timestamp")

        for entry in history:
            table.add_row(
                entry.get("role", ""),
                entry.get("content", "")[:120],
                entry.get("timestamp", ""),
            )

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
        self._last_ctrl_c: float = 0.0

    def _get_command_handlers(self) -> dict[str, Callable[[str], Any]]:
        """Return canonical slash command handlers."""
        return {
            "/help": self._cmd_help,
            "/plan": self._cmd_plan,
            "/act": self._cmd_act,
            "/tools": self._cmd_tools,
            "/skills": self._cmd_skills,
            "/skill": self._cmd_skill,
            "/reload-skills": self._cmd_reload_skills,
            "/model": self._cmd_model,
            "/config": self._cmd_config,
            "/clear": self._cmd_clear,
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
            "/history": self._cmd_history,
        }

    def _get_slash_commands(self) -> list[str]:
        """Return slash commands available for completion."""
        return list(self._get_command_handlers().keys())

    def _get_completer(self) -> SlashCommandCompleter:
        """Return the prompt completer for slash commands."""
        return SlashCommandCompleter(self)

    def _setup_key_bindings(self) -> KeyBindings:
        """Set up key bindings."""
        kb = KeyBindings()

        @kb.add("c-d")
        def _(event):
            self.running = False
            event.app.exit()

        @kb.add("c-c")
        def _(event):
            import time
            now = time.time()
            buffer = event.app.current_buffer

            # If buffer has text, first Ctrl+C just clears it
            if buffer.text:
                buffer.reset()
                self._last_ctrl_c = 0.0
                return

            # Double Ctrl+C within 2 seconds on empty buffer → exit
            if self._last_ctrl_c > 0 and (now - self._last_ctrl_c) < 2.0:
                self.running = False
                event.app.exit()
                return

            self._last_ctrl_c = now
            self.renderer.print("[yellow]Press Ctrl+C again to exit[/yellow]")

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
            completer=self._get_completer(),
            multiline=False,
            mouse_support=False,  # Disable mouse support to allow terminal scrolling
            key_bindings=self._setup_key_bindings(),
        )

        self.renderer.print_welcome(self.agent.get_model_info())

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

                self._last_ctrl_c = 0.0
                await self._handle_input(user_input)

            except KeyboardInterrupt:
                print()
                import time
                now = time.time()
                if self._last_ctrl_c > 0 and (now - self._last_ctrl_c) < 2.0:
                    self.running = False
                    break
                self._last_ctrl_c = now
                self.renderer.print("[yellow]Press Ctrl+C again to exit[/yellow]")
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
        cmd = parts[0].lower().replace("_", "-")
        args = parts[1] if len(parts) > 1 else ""

        commands = self._get_command_handlers()

        handler = commands.get(cmd)
        if handler:
            await handler(args)
        else:
            self.renderer.print_error(f"Unknown command: {cmd}")

    async def _cmd_help(self, args: str) -> None:
        """Show help."""
        self.renderer.print_help()

    # Tool names whose results are not displayed to the user (verbose file ops).
    _SUPPRESSED_RESULT_TOOLS: set[str] = {"list_directory", "read_file"}

    def _register_act_callbacks(self) -> None:
        """Register the same visible callbacks used by normal act mode."""
        _current_tool: dict[str, str] = {"name": ""}

        def on_thought(thought: str) -> None:
            self.renderer.print_thinking(thought)

        def on_action(tool_name: str, args: dict) -> None:
            _current_tool["name"] = tool_name
            self.renderer.print_action(tool_name, args)

        def on_result(result: ToolResult) -> None:
            if _current_tool["name"] in self._SUPPRESSED_RESULT_TOOLS:
                return
            self.renderer.print_result(result)

        def on_stream(chunk: StreamChunk) -> None:
            self.renderer.print_stream(chunk)

        self.agent.register_callback("thought", on_thought)
        self.agent.register_callback("action", on_action)
        self.agent.register_callback("result", on_result)
        self.agent.register_callback("stream", on_stream)

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

        approved = await self._prompt_plan_execution()
        if not approved:
            self.renderer.print("[yellow]Plan kept for later execution.[/yellow]")
            return

        self.agent.state.mark_plan_approved()
        self._register_act_callbacks()
        self.agent.register_callback("interaction", self._handle_interaction)
        self.renderer.print("[cyan]Executing approved plan...[/cyan]")
        execution_result = await self._run_with_spinner(self.agent.execute_approved_plan())
        self.renderer.print_markdown(execution_result)

    async def _prompt_plan_execution(self) -> bool:
        """Prompt the user to approve execution of the current saved plan."""
        response = await self.session.prompt_async(
            "Execute this plan now? [y/N]: "
        )
        return response.strip().lower() in {"y", "yes"}

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

    async def _cmd_skills(self, args: str) -> None:
        """List loaded skills."""
        skills = []
        skill_registry = getattr(self.agent, "skill_registry", None)
        if skill_registry:
            for name in skill_registry.list_skills():
                info = skill_registry.get_skill_info(name) or {}
                source_path = info.get("source")
                source_type = info.get("source_type", "")
                source_label = source_type
                if source_path:
                    source_label = f"{source_type}: {source_path}"
                skills.append(
                    {
                        "name": name,
                        "enabled": info.get("enabled", True),
                        "source_type": source_label,
                        "model_invocable": info.get("model_invocable", False),
                        "user_invocable": info.get("user_invocable", False),
                        "description": info.get("description") or info.get("tool_description", ""),
                    }
                )

        if not skills:
            self.renderer.print("No skills loaded.")
            return

        self.renderer.print_skills(skills)

    async def _cmd_skill(self, args: str) -> None:
        """Invoke a loaded skill and start agent execution with its instructions."""
        if not args:
            self.renderer.print_error("Usage: /skill <name> [args]")
            return

        parts = args.split(maxsplit=1)
        skill_name = parts[0]
        skill_args = parts[1] if len(parts) > 1 else ""

        # Step 1: Validate and materialize the skill
        result = self.agent.invoke_skill(
            skill_name=skill_name, skill_args=skill_args, caller="user"
        )

        if not result.success:
            self.renderer.print_error(result.error or "Failed to invoke skill")
            return

        skill_prompt = result.metadata.get("skill_prompt", "")
        if not skill_prompt:
            self.renderer.print_error("Skill prompt is empty")
            return

        self.renderer.print_success(f"Invoked skill: {skill_name}")

        # Step 2: Add the skill prompt to context
        from opennova.providers.base import Message

        self.agent.context_manager.add_message(
            Message(
                role="user",
                content=f"Invoked skill '{skill_name}':\n\n{skill_prompt}",
            )
        )

        # Step 3: Run the agent with preserved context so it processes the skill
        task = f"/skill {skill_name} {skill_args}".strip()
        self._register_act_callbacks()
        self.agent.register_callback("interaction", self._handle_interaction)

        try:
            result_text = await self._run_with_spinner(
                self.agent._run_act_mode(
                    task=task,
                    stream=True,
                    preserve_context=True,
                )
            )
            if result_text:
                self.renderer.print_markdown(result_text)
        except KeyboardInterrupt:
            print()
            self.renderer.print("[yellow]Skill execution cancelled[/yellow]")
            raise
        except Exception as e:
            self.renderer.print_error(f"Skill execution failed: {type(e).__name__}: {e}")

    async def _cmd_reload_skills(self, args: str) -> None:
        """Reload skills from disk."""
        count = self.agent.reload_skills()
        self.renderer.print_success(f"Reloaded {count} skills.")

    async def _cmd_model(self, args: str) -> None:
        """Show model info."""
        info = self.agent.get_model_info()
        table = Table(title="🤖 Model Information")
        table.add_column("Property", style="cyan")
        table.add_column("Value")

        for key, value in info.items():
            table.add_row(key, str(value))

        self.renderer.print(table)

    async def _cmd_config(self, args: str) -> None:
        """Show current configuration."""
        if not self.config:
            self.renderer.print_error("No configuration object available.")
            return

        if self.config.config_path:
            self.renderer.print(f"[cyan]Config path:[/cyan] {self.config.config_path}")
        self.renderer.print_code(
            yaml.dump(self.config.data, default_flow_style=False, sort_keys=False),
            language="yaml",
        )

    async def _cmd_clear(self, args: str) -> None:
        """Clear conversation."""
        self.agent.clear_conversation()
        self.renderer.print_success("Conversation cleared.")

    async def _cmd_history(self, args: str) -> None:
        """Show conversation history."""
        history = []
        context_manager = getattr(self.agent, "context_manager", None)
        if context_manager:
            history = context_manager.get_conversation_history()

        if args:
            try:
                limit = int(args)
                history = history[-limit:] if limit > 0 else history
            except ValueError:
                self.renderer.print_error("Usage: /history [n]")
                return

        if not history:
            self.renderer.print("No conversation history.")
            return

        self.renderer.print_history(history)


    async def _cmd_exit(self, args: str) -> None:
        """Exit the REPL."""
        self.running = False

    async def _handle_interaction(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """Handle interactive tool prompts during REPL runs.

        Two modes:
        - Choice mode (2+ options): numbered selection dialog
        - Free-text mode (0-1 options): text input, empty = skipped
        """
        payload = metadata.get("prompt_payload", {})
        question = payload.get("question", "")
        options = payload.get("options", [])
        multi_select = payload.get("multi_select", False)
        free_text = payload.get("free_text", False)
        header = payload.get("header")

        # Dialog box with Rich Panel
        dialog_lines = [f"[bold]{question}[/bold]"]
        if header:
            dialog_lines.insert(0, f"[cyan][{header}][/cyan]")
        if free_text:
            dialog_lines.append("[dim](Press Enter to skip — model will decide)[/dim]")
        else:
            for option in options:
                idx = option["index"]
                label = option["label"]
                desc = option.get("description", "")
                line = f"  [[{idx}]] [yellow]{label}[/yellow]"
                if desc:
                    line += f"\n      [dim]{desc}[/dim]"
                dialog_lines.append(line)

        self.renderer.print("")
        self.renderer.print(Panel("\n".join(dialog_lines), border_style="cyan", padding=(1, 2)))

        # Free-text mode
        if free_text:
            response = await self.session.prompt_async("Your answer: ")
            answer = response.strip()
            if not answer:
                # User skipped — let the model decide
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
        prompt = "Select option(s): " if multi_select else "Select option: "
        while True:
            response = await self.session.prompt_async(prompt)
            selected = [part.strip() for part in response.split(",") if part.strip()]
            if not selected:
                self.renderer.print("[yellow]Please choose at least one option.[/yellow]")
                continue
            if not multi_select and len(selected) != 1:
                self.renderer.print("[yellow]Please choose exactly one option.[/yellow]")
                continue

            try:
                indexes = [int(value) for value in selected]
            except ValueError:
                self.renderer.print("[yellow]Please enter numeric option values.[/yellow]")
                continue

            option_map = {option["index"]: option for option in options}
            if any(index not in option_map for index in indexes):
                self.renderer.print("[yellow]Selection out of range.[/yellow]")
                continue

            chosen = [option_map[index] for index in indexes]
            labels = [item["label"] for item in chosen]
            return {
                "answer": labels if multi_select else labels[0],
                "skipped": False,
                "answers": {question: labels if multi_select else labels[0]},
                "selected_options": chosen,
                "display": ", ".join(labels),
            }

    async def _run_with_spinner(self, coro):
        """Run a coroutine while showing a spinner with elapsed time."""
        import time

        start = time.time()
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        async def spin():
            i = 0
            while True:
                elapsed = time.time() - start
                frame = frames[i % len(frames)]
                sys.stderr.write(f"\r  {frame} Working... ({elapsed:.0f}s)")
                sys.stderr.flush()
                i += 1
                await asyncio.sleep(0.1)

        spinner_task = asyncio.create_task(spin())
        try:
            result = await coro
            return result
        finally:
            spinner_task.cancel()
            try:
                await spinner_task
            except asyncio.CancelledError:
                pass
            sys.stderr.write("\r" + " " * 40 + "\r")
            sys.stderr.flush()

    async def _execute_task(self, task: str) -> None:
        """Execute a task with streaming output."""
        import traceback

        self._register_act_callbacks()
        self.agent.register_callback("interaction", self._handle_interaction)

        print()
        try:
            result = await self._run_with_spinner(self.agent.run(task))
            print()
            if result is None:
                self.renderer.print_error("Task returned None - check logs for details")
            else:
                self.renderer.print_markdown(result)
        except KeyboardInterrupt:
            print()
            self.renderer.print("[yellow]Task cancelled[/yellow]")
            raise
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
