"""Shared plugin slash-command handling."""

from __future__ import annotations

from opennova.plugins import PluginManager
from opennova.tools.base import ToolResult


def handle_plugin_command(manager: PluginManager, args: str) -> ToolResult:
    """Handle `/plugins` subcommands."""
    tokens = (args or "list").split()
    subcommand = tokens[0] if tokens else "list"

    try:
        if subcommand == "list":
            plugins = manager.plugins
            output = "\n".join(
                f"{plugin.name} trusted={manager.is_trusted(plugin.name)} enabled={plugin.enabled}"
                for plugin in plugins
            ) or "No local plugins discovered."
            return ToolResult(success=True, output=output, metadata={"plugins": plugins})

        if subcommand == "test" and len(tokens) == 2:
            name = tokens[1]
            report = manager.test_plugin(name)
            if report.success:
                return ToolResult(
                    success=True,
                    output=f"Plugin {name} passed validation",
                    metadata={"report": report},
                )
            return ToolResult(
                success=False,
                output="",
                error="\n".join(report.errors),
                metadata={"report": report},
            )
    except Exception as exc:
        return ToolResult(success=False, output="", error=str(exc))

    return ToolResult(
        success=False,
        output="",
        error="Usage: /plugins [list|test <name>]",
    )
