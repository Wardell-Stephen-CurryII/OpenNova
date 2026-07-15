"""Trusted local plugin command-backed tools."""

from __future__ import annotations

import shlex
from typing import Any

from opennova.tools.base import BaseTool, ToolResult
from opennova.tools.shell_tools import ExecuteCommandTool


class PluginCommandTool(BaseTool):
    """A trusted plugin tool backed by a local command."""

    def __init__(
        self,
        name: str,
        description: str,
        command: str,
        args: list[str] | None = None,
        config: dict[str, Any] | None = None,
        read_only: bool = False,
        permission: str = "command",
    ):
        super().__init__(config)
        self.name = name
        self.description = description
        self.command = command
        self.args = args or []
        self.permission = permission
        self._read_only = read_only or permission == "read"
        self._command_tool = ExecuteCommandTool(self.config)

    @property
    def command_line(self) -> str:
        return shlex.join([self.command, *self.args])

    def execute(self) -> ToolResult:
        return self._decorate_result(self._command_tool.execute(self.command_line))

    async def async_execute(self) -> ToolResult:
        """Execute through the shared cancellable process-sandbox path."""
        result = await self._command_tool.async_execute(self.command_line)
        return self._decorate_result(result)

    def _decorate_result(self, result: ToolResult) -> ToolResult:
        result.metadata.update(
            {
                "plugin_tool": True,
                "returncode": result.metadata.get("exit_code"),
                "permission": self.permission,
            }
        )
        return result

    def is_read_only(self, **kwargs: Any) -> bool:
        return self._read_only

    def requires_permission(self, **kwargs: Any) -> bool:
        return not self._read_only
