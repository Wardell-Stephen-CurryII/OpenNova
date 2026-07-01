"""Shared slash command registry for the TUI and plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SlashCommand:
    """Declarative slash command metadata."""

    name: str
    handler: str = ""
    description: str = ""
    usage: str = ""
    plugin: str | None = None
    sync: bool = True


class SlashCommandRegistry:
    """Small command registry shared by interactive surfaces."""

    def __init__(self):
        self._commands: dict[str, SlashCommand] = {}

    @classmethod
    def default(cls) -> SlashCommandRegistry:
        registry = cls()
        for command in [
            SlashCommand("/help", "_cmd_help", "Show help"),
            SlashCommand("/plan", "_cmd_plan", "Plan before executing", "/plan <task>", sync=False),
            SlashCommand("/act", "_cmd_act", "Execute directly", "/act <task>", sync=False),
            SlashCommand("/tools", "_cmd_tools", "List tools"),
            SlashCommand("/skills", "_cmd_skills", "List skills"),
            SlashCommand("/skill", "_cmd_skill", "Invoke a skill", "/skill <name> [args]", sync=False),
            SlashCommand("/reload-skills", "_cmd_reload_skills", "Reload skills"),
            SlashCommand("/model", "_cmd_model", "Show model information"),
            SlashCommand("/init", "_cmd_init", "Initialize OPENNOVA.md", "/init [--force]", sync=False),
            SlashCommand("/config", "_cmd_config", "Show configuration"),
            SlashCommand("/clear", "_cmd_clear", "Clear conversation"),
            SlashCommand("/exit", "_cmd_exit", "Exit"),
            SlashCommand("/quit", "_cmd_exit", "Exit"),
            SlashCommand("/history", "_cmd_history", "Show history", "/history [n]"),
            SlashCommand("/resume", "_cmd_resume", "Resume a session", "/resume [id]", sync=False),
            SlashCommand("/sessions", "_cmd_sessions", "List sessions"),
            SlashCommand("/permissions", "_cmd_permissions", "Show or update permission rules"),
            SlashCommand("/plugins", "_cmd_plugins", "List or trust local plugins"),
            SlashCommand("/hooks", "_cmd_hooks", "Show loaded hooks"),
            SlashCommand("/automations", "_cmd_automations", "List local automations"),
            SlashCommand("/diagnostics", "_cmd_diagnostics", "Run Python diagnostics", sync=False),
            SlashCommand("/status", "_cmd_status", "Show runtime status"),
            SlashCommand("/todos", "_cmd_todos", "Show task/todo summary"),
            SlashCommand("/checkpoint", "_cmd_checkpoint", "Show checkpoint guidance"),
            SlashCommand("/export", "_cmd_export", "Export current transcript"),
        ]:
            registry.register(command)
        return registry

    def register(self, command: SlashCommand) -> None:
        self._commands[command.name.lower()] = command

    def register_plugin_command(self, data: dict[str, Any]) -> None:
        name = str(data.get("name", "")).strip()
        if not name:
            return
        if not name.startswith("/"):
            name = "/" + name
        self.register(
            SlashCommand(
                name=name.lower(),
                handler=str(data.get("handler") or ""),
                description=str(data.get("description") or ""),
                usage=str(data.get("usage") or ""),
                plugin=str(data.get("plugin") or "") or None,
                sync=bool(data.get("sync", True)),
            )
        )

    def get(self, name: str) -> SlashCommand | None:
        return self._commands.get(name.lower().replace("_", "-"))

    def names(self) -> list[str]:
        return sorted(self._commands)

    def sync_names(self) -> set[str]:
        return {name for name, command in self._commands.items() if command.sync}
