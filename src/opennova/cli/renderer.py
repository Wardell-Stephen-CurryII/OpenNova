"""
Rich Renderer - Enhanced terminal output rendering.

Provides beautiful terminal output with:
- Syntax highlighting for code
- Diff previews with colors
- Progress bars
- Tables and panels
- Markdown rendering
"""

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from opennova.diff.parser import ChangeType
from opennova.providers.base import StreamChunk
from opennova.runtime.state import Plan, PlanStep
from opennova.tools.base import ToolResult


class Renderer:
    """
    Rich-based renderer for CLI output.

    Provides beautiful terminal output with syntax highlighting,
    diff previews, progress bars, and more.
    """

    def __init__(self, console: Console | None = None):
        """Initialize renderer with optional console."""
        self.console = console or Console(
            force_terminal=True,
            soft_wrap=False,  # Disable soft wrap to allow terminal scrolling
            markup=True,
            highlight=True,
        )

    def print(self, message: Any = "", **kwargs) -> None:
        """Print message to console."""
        self.console.print(message, **kwargs)

    def print_welcome(self) -> None:
        """Display welcome message."""
        self.console.print(
            Panel.fit(
                "[bold cyan]OpenNova[/bold cyan] - AI Coding Agent\n\n"
                "[dim]A lightweight CLI AI Coding Agent built from scratch[/dim]\n\n"
                "Type [bold]/help[/bold] for commands, [bold]Ctrl+D[/bold] to exit",
                title="🌟 Welcome",
                border_style="blue",
            )
        )

    def print_help(self) -> None:
        """Display help message."""
        help_text = """
## Commands

| Command | Description |
|---------|-------------|
| `/plan <task>` | Generate a plan before executing |
| `/act <task>` | Execute directly (default mode) |
| `/tools` | List available tools |
| `/model` | Show current model info |
| `/config` | Show current configuration |
| `/history` | Show conversation history |
| `/clear` | Clear conversation |
| `/help` | Show this help message |
| `/exit` / `/quit` | Exit the REPL |

## Tips

- Use `Tab` for auto-suggestions
- Use `↑/↓` for history navigation
- Multi-line input is supported
"""
        self.console.print(Markdown(help_text))

    def print_thinking(self, thought: str, collapsed: bool = False) -> None:
        """Display thinking process."""
        if collapsed and len(thought) > 200:
            preview = thought[:200] + "..."
        else:
            preview = thought

        self.console.print(
            Panel(
                preview,
                title="💭 Thinking",
                border_style="yellow",
                expand=False,
            )
        )

    def print_action(self, tool_name: str, args: dict[str, Any]) -> None:
        """Display tool action."""
        args_preview = []
        for k, v in args.items():
            v_str = str(v)
            if len(v_str) > 50:
                v_str = v_str[:47] + "..."
            args_preview.append(f"[dim]{k}[/dim]={repr(v_str)}")

        args_str = ", ".join(args_preview)

        self.console.print(
            Panel(
                f"[bold cyan]{tool_name}[/bold cyan]({args_str})",
                title="⚙️ Tool Call",
                border_style="blue",
                expand=False,
            )
        )

    def print_result(self, result: ToolResult, max_lines: int = 20) -> None:
        """Display tool result."""
        if result.success:
            style = "green"
            icon = "✅"
            title = "Success"
        else:
            style = "red"
            icon = "❌"
            title = "Error"

        output = result.output or ""

        lines = output.split("\n")
        if len(lines) > max_lines:
            output = "\n".join(lines[:max_lines]) + f"\n\n... [truncated, {len(lines) - max_lines} more lines]"

        self.console.print(
            Panel(
                output,
                title=f"{icon} {title}",
                border_style=style,
                expand=False,
            )
        )

        if result.error:
            self.console.print(f"[red bold]Error:[/red bold] {result.error}")

    def print_stream(self, chunk: StreamChunk) -> None:
        """Display streaming chunk."""
        if chunk.content:
            self.console.print(chunk.content, end="", markup=False)
        if chunk.finish_reason:
            self.console.print()

    def print_plan(self, plan: Plan, show_progress: bool = True) -> None:
        """Display a plan with status."""
        tree = Tree(f"📋 [bold]{plan.task}[/bold]")

        status_icons = {
            "pending": ("⏳", "yellow"),
            "running": ("🔄", "blue"),
            "done": ("✅", "green"),
            "failed": ("❌", "red"),
            "skipped": ("⏭️", "dim"),
        }

        for step in plan.steps:
            icon, color = status_icons.get(step.status.value, ("❓", "white"))
            step_text = f"[{color}]{icon}[/{color}] [{step.id}] {step.description}"

            if step.tool_hint:
                step_text += f" [dim](tool: {step.tool_hint})[/dim]"

            tree.add(step_text)

        self.console.print(tree)

        if show_progress:
            done = sum(1 for s in plan.steps if s.status.value == "done")
            total = len(plan.steps)
            self.console.print(f"\n[dim]Progress: {done}/{total} steps completed[/dim]")

    def print_tools(self, tools: list[str], descriptions: dict[str, str] | None = None) -> None:
        """Display available tools."""
        table = Table(title="🛠️ Available Tools", show_header=True)
        table.add_column("Tool", style="cyan")
        table.add_column("Description")

        for tool in sorted(tools):
            desc = descriptions.get(tool, "") if descriptions else ""
            table.add_row(tool, desc)

        self.console.print(table)

    def print_error(self, message: str, title: str = "Error") -> None:
        """Display error message."""
        self.console.print(
            Panel(
                message,
                title=f"❌ {title}",
                border_style="red",
            )
        )

    def print_success(self, message: str, title: str = "Success") -> None:
        """Display success message."""
        self.console.print(
            Panel(
                message,
                title=f"✅ {title}",
                border_style="green",
            )
        )

    def print_warning(self, message: str, title: str = "Warning") -> None:
        """Display warning message."""
        self.console.print(
            Panel(
                message,
                title=f"⚠️ {title}",
                border_style="yellow",
            )
        )

    def print_info(self, message: str, title: str = "Info") -> None:
        """Display info message."""
        self.console.print(
            Panel(
                message,
                title=f"ℹ️ {title}",
                border_style="blue",
            )
        )

    def print_markdown(self, text: str) -> None:
        """Render markdown text."""
        self.console.print(Markdown(text))

    def print_code(
        self,
        code: str,
        language: str = "python",
        line_numbers: bool = True,
        title: str | None = None,
    ) -> None:
        """Display code with syntax highlighting."""
        syntax = Syntax(
            code,
            language,
            theme="monokai",
            line_numbers=line_numbers,
        )

        if title:
            self.console.print(Panel(syntax, title=title, border_style="dim"))
        else:
            self.console.print(syntax)

    def print_diff(self, diff_text: str, title: str = "Diff") -> None:
        """Display a colored diff preview."""
        lines = []

        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                lines.append(f"[bold cyan]{line}[/bold cyan]")
            elif line.startswith("@@"):
                lines.append(f"[bold blue]{line}[/bold blue]")
            elif line.startswith("+"):
                lines.append(f"[green]{line}[/green]")
            elif line.startswith("-"):
                lines.append(f"[red]{line}[/red]")
            else:
                lines.append(f"[dim]{line}[/dim]")

        content = "\n".join(lines)

        self.console.print(
            Panel(
                content,
                title=f"📝 {title}",
                border_style="magenta",
                expand=False,
            )
        )

    def print_file_tree(
        self,
        root_path: str,
        max_depth: int = 3,
        show_files: bool = True,
    ) -> None:
        """Display a file tree."""
        root = Path(root_path)
        tree = Tree(f"📁 [bold]{root.name}/[/bold]")

        def add_items(parent: Tree, path: Path, depth: int) -> None:
            if depth > max_depth:
                return

            try:
                items = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            except PermissionError:
                return

            for item in items:
                if item.name.startswith(".") and item.name not in (".gitignore", ".env.example"):
                    continue

                if item.is_dir():
                    branch = parent.add(f"📁 [cyan]{item.name}/[/cyan]")
                    add_items(branch, item, depth + 1)
                elif show_files:
                    ext = item.suffix.lower()
                    icon = self._get_file_icon(ext)
                    parent.add(f"{icon} {item.name}")

        add_items(tree, root, 1)
        self.console.print(tree)

    def _get_file_icon(self, ext: str) -> str:
        """Get icon for file extension."""
        icons = {
            ".py": "🐍",
            ".js": "📜",
            ".ts": "📘",
            ".jsx": "⚛️",
            ".tsx": "⚛️",
            ".json": "📋",
            ".yaml": "⚙️",
            ".yml": "⚙️",
            ".md": "📝",
            ".txt": "📄",
            ".html": "🌐",
            ".css": "🎨",
            ".sql": "🗃️",
            ".sh": "🖥️",
            ".toml": "⚙️",
            ".ini": "⚙️",
            ".cfg": "⚙️",
        }
        return icons.get(ext, "📄")

    def print_table(
        self,
        title: str,
        headers: list[str],
        rows: list[list[Any]],
    ) -> None:
        """Display a formatted table."""
        table = Table(title=title)

        for header in headers:
            table.add_column(header)

        for row in rows:
            table.add_row(*[str(cell) for cell in row])

        self.console.print(table)

    def print_progress(
        self,
        description: str = "Processing...",
        total: int | None = None,
    ) -> Progress:
        """Create and return a progress bar."""
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
        )
        return progress

    def print_file_change(
        self,
        file_path: str,
        change_type: ChangeType,
        diff: str | None = None,
    ) -> None:
        """Display a file change preview."""
        type_colors = {
            ChangeType.CREATE: "green",
            ChangeType.MODIFY: "yellow",
            ChangeType.DELETE: "red",
        }

        type_icons = {
            ChangeType.CREATE: "✨",
            ChangeType.MODIFY: "📝",
            ChangeType.DELETE: "🗑️",
        }

        color = type_colors.get(change_type, "white")
        icon = type_icons.get(change_type, "📄")

        self.console.print(
            Panel(
                f"[{color}]{change_type.value.upper()}[/{color}]\n{file_path}",
                title=f"{icon} File Change",
                border_style=color,
            )
        )

        if diff:
            self.print_diff(diff, title=f"Changes: {Path(file_path).name}")

    def print_statistics(
        self,
        stats: dict[str, Any],
        title: str = "Statistics",
    ) -> None:
        """Display statistics in a nice format."""
        table = Table(title=f"📊 {title}")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")

        for key, value in stats.items():
            if isinstance(value, float):
                value = f"{value:.2f}"
            table.add_row(key.replace("_", " ").title(), str(value))

        self.console.print(table)

    def print_divider(self, title: str | None = None) -> None:
        """Print a divider line."""
        if title:
            self.console.print(f"\n[bold dim]── {title} ──[/bold dim]\n")
        else:
            self.console.print("\n[dim]" + "─" * 50 + "[/dim]\n")

    def clear(self) -> None:
        """Clear the console."""
        self.console.clear()
