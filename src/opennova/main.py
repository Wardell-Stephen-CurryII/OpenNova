"""
OpenNova terminal application entry point.

Launches the Textual TUI and exposes setup and one-shot task commands.
"""

import asyncio
import sys
from pathlib import Path

import click

from opennova import __version__
from opennova.config import (
    Config,
    create_default_config,
    load_config,
    validate_config,
)


def print_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Print version and exit."""
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"OpenNova v{__version__}")
    ctx.exit()


@click.group(invoke_without_command=True)
@click.option(
    "--version",
    "-v",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=print_version,
    help="Show version and exit.",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=False),
    help="Path to configuration file.",
)
@click.option(
    "--resume",
    "resume_mode",
    is_flag=True,
    help="Open the TUI and choose a saved session to resume.",
)
@click.option(
    "--continue",
    "continue_mode",
    is_flag=True,
    help="Open the TUI and continue the most recent saved session.",
)
@click.option(
    "--permission-mode",
    type=click.Choice(["request", "auto", "full"], case_sensitive=False),
    help="Approval mode for this run: request, auto, or full.",
)
@click.pass_context
def main(
    ctx: click.Context,
    config_path: str | None,
    resume_mode: bool,
    continue_mode: bool,
    permission_mode: str | None,
) -> None:
    """
    OpenNova - A terminal AI Coding Agent with a Textual TUI.

    Run without arguments to start the Textual TUI.
    """
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["resume_mode"] = resume_mode
    ctx.obj["continue_mode"] = continue_mode
    ctx.obj["permission_mode"] = permission_mode

    if ctx.invoked_subcommand is None:
        ctx.invoke(run, task=None)


@main.command()
@click.argument("task", required=False)
@click.option("--plan", "-p", is_flag=True, help="Run in plan mode.")
@click.option("--model", "-m", "model", help="Override model to use.")
@click.option("--provider", help="Override provider to use.")
@click.option("--no-stream", is_flag=True, help="Disable streaming output.")
@click.option(
    "--tui",
    "force_tui",
    is_flag=True,
    help="Force the Textual TUI, including on Windows terminals.",
)
@click.pass_context
def run(
    ctx: click.Context,
    task: str | None,
    plan: bool,
    model: str | None,
    provider: str | None,
    no_stream: bool,
    force_tui: bool,
) -> None:
    """
    Run OpenNova agent on a task.

    If no task is provided, starts interactive TUI mode.

    Examples:

        opennova run "Read the README.md file"

        opennova run --plan "Refactor the authentication module"

        opennova run -m deepseek-v4-pro "Create a new Python module"
    """
    config = _load_and_validate_config(
        ctx.obj.get("config_path"),
        provider,
        model,
        ctx.obj.get("permission_mode"),
    )
    resume_mode = bool(ctx.obj.get("resume_mode"))
    continue_mode = bool(ctx.obj.get("continue_mode"))

    if resume_mode and continue_mode:
        raise click.UsageError("Use only one of --resume or --continue.")
    if (resume_mode or continue_mode) and task:
        raise click.UsageError("--resume/--continue cannot be used with a direct task.")

    if task:
        asyncio.run(_run_single_task(config, task, plan, not no_stream))
    elif _use_tui_for_interactive(force_tui=force_tui):
        from opennova.cli.tui import run_tui

        startup_resume_mode = None
        if resume_mode:
            startup_resume_mode = "resume"
        elif continue_mode:
            startup_resume_mode = "continue"
        asyncio.run(run_tui(config, startup_resume_mode=startup_resume_mode))


@main.command()
@click.argument("task")
@click.option("--edit", is_flag=True, help="Open plan in editor before execution.")
@click.pass_context
def plan(ctx: click.Context, task: str, edit: bool) -> None:
    """
    Create and execute a plan for a task.

    Generates a structured plan before execution, allowing review.

    Example:

        opennova plan "Add unit tests for the authentication module"
    """
    config = _load_and_validate_config(
        ctx.obj.get("config_path"),
        permission_mode=ctx.obj.get("permission_mode"),
    )
    asyncio.run(_run_single_task(config, task, plan_mode=True, stream=True))


@main.command("list-tools")
@click.pass_context
def list_tools(ctx: click.Context) -> None:
    """
    List all available tools.
    """
    del ctx
    from opennova.runtime.bootstrap import inspect_runtime

    snapshot = inspect_runtime()

    click.echo("Available tools:\n")
    for tool_name in snapshot.tool_names:
        click.echo(f"  • {tool_name}")

    click.echo(f"\nTotal: {len(snapshot.tool_names)} tools")


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Inspect local runtime readiness without creating a provider or session."""
    from opennova.runtime.bootstrap import inspect_runtime

    config = load_config(ctx.obj.get("config_path"))
    snapshot = inspect_runtime()
    project = Path.cwd()
    hooks = list((project / ".opennova" / "hooks").glob("*.py"))
    plugins = list((project / ".opennova" / "plugins").glob("*/plugin.yaml"))
    mcp_servers = config.get_mcp_servers()
    process_sandbox = config.get("security.process_sandbox", {})
    click.echo("OpenNova doctor (side-effect-free)\n")
    click.echo(f"Version: {__version__}")
    click.echo(f"Bootstrap profile: {snapshot.profile.value}")
    click.echo(f"Python encoding: {sys.getfilesystemencoding()}")
    click.echo(f"Built-in tools: {len(snapshot.tool_names)}")
    click.echo(f"Project hooks declared: {len(hooks)} (not imported)")
    click.echo(f"Project plugins declared: {len(plugins)} (not loaded)")
    click.echo(f"MCP servers configured: {len(mcp_servers)} (not connected)")
    click.echo(
        "Process sandbox: "
        f"enabled={bool(process_sandbox.get('enabled', True))} "
        f"backend={process_sandbox.get('backend', 'auto')}"
    )


@main.command()
@click.pass_context
def config_cmd(ctx: click.Context) -> None:
    """
    Show current configuration.

    Displays the merged configuration from all sources.
    """
    config = load_config(ctx.obj.get("config_path"))

    import yaml

    click.echo("Current configuration:\n")
    click.echo(yaml.dump(config.redacted_data(), default_flow_style=False, sort_keys=False))


@main.command()
def init() -> None:
    """
    Initialize OpenNova configuration.

    Creates a default configuration file at ~/.opennova/config.yaml
    """
    config_path = create_default_config()
    click.echo(f"Created configuration file: {config_path}")
    click.echo("\nPlease edit the configuration file and add your API keys.")
    click.echo("\nYou can also set environment variables:")
    click.echo("  - OPENAI_API_KEY")
    click.echo("  - ANTHROPIC_API_KEY")
    click.echo("  - DEEPSEEK_API_KEY")


def _use_tui_for_interactive(*, force_tui: bool, platform: str | None = None) -> bool:
    """Return whether the interactive command should launch the Textual TUI."""
    return True


async def _run_single_task(
    config: Config,
    task: str,
    plan_mode: bool = False,
    stream: bool = True,
) -> None:
    """Run a single task and exit."""
    from rich.console import Console

    from opennova.providers.base import StreamChunk
    from opennova.runtime.agent import AgentRuntime
    from opennova.runtime.state import Plan
    from opennova.tools.base import ToolResult

    console = Console(
        force_terminal=True,
        soft_wrap=False,  # Disable soft wrap to allow terminal scrolling
        markup=True,
        highlight=True,
    )

    from opennova.runtime.bootstrap import RuntimeBootstrapProfile

    agent = AgentRuntime(config, bootstrap_profile=RuntimeBootstrapProfile.HEADLESS)

    if plan_mode:
        console.print(f"[yellow]Planning: {task}[/yellow]\n")
    else:
        console.print(f"[cyan]Task: {task}[/cyan]\n")

    def on_thought(thought: str) -> None:
        console.print(f"[dim]💭 {thought}[/dim]\n")

    def on_action(tool_name: str, args: dict) -> None:
        args_str = ", ".join(f"{k}={repr(v)[:50]}" for k, v in args.items())
        console.print(f"[blue]⚙️  {tool_name}({args_str})[/blue]")

    def on_result(result: ToolResult) -> None:
        if result.success:
            console.print("[green]✅ Done[/green]\n")
        else:
            console.print(f"[red]❌ Error: {result.error}[/red]\n")

    def on_stream(chunk: StreamChunk) -> None:
        if chunk.content:
            print(chunk.content, end="", flush=True)

    def on_plan(plan: Plan, plan_file_path: str | None = None) -> None:
        step_count = len(plan.steps)
        console.print(f"[cyan]Generated plan with {step_count} steps.[/cyan]")
        if plan_file_path:
            console.print(f"[green]Plan saved to:[/green] {plan_file_path}\n")

    agent.register_callback("thought", on_thought)
    agent.register_callback("action", on_action)
    agent.register_callback("result", on_result)
    agent.register_callback("stream", on_stream)
    if plan_mode:
        agent.register_callback("plan", on_plan)

    try:
        result = await agent.run(
            task,
            mode="plan" if plan_mode else "act",
            stream=stream,
        )

        console.print()
        console.print("[bold]Result:[/bold]")
        console.print(result)

        if plan_mode:
            if click.confirm("Execute this saved plan now?", default=False):
                agent.state.mark_plan_approved()
                execution_result = await agent.execute_approved_plan(stream=stream)
                console.print()
                console.print("[bold]Execution Result:[/bold]")
                console.print(execution_result)
            else:
                console.print("[yellow]Plan kept for later execution.[/yellow]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Task interrupted.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        sys.exit(1)
    finally:
        await agent.aclose()


def _load_and_validate_config(
    config_path: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
) -> Config:
    """Load and validate configuration."""
    config = load_config(config_path)

    if provider:
        config.set("default_provider", provider)

    if model:
        current_provider = config.get("default_provider")
        providers = config.get("providers", {})
        if current_provider in providers:
            providers[current_provider]["default_model"] = model
            config.data["providers"] = providers

    if permission_mode:
        config.set("security.permission_mode", permission_mode.lower())

    errors = validate_config(config)
    if errors:
        click.echo("Configuration errors:\n", err=True)
        for error in errors:
            click.echo(f"  • {error}", err=True)
        click.echo(
            "\nRun 'opennova init' to create a configuration file, "
            "or set the appropriate API key environment variable.",
            err=True,
        )
        sys.exit(1)

    return config


if __name__ == "__main__":
    main()
