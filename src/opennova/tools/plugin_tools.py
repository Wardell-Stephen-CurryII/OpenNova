"""Trusted local plugin command-backed tools."""

from __future__ import annotations

import os
import shlex
import subprocess
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
    def command_argv(self) -> list[str]:
        return [self.command, *self.args]

    @property
    def command_line(self) -> str:
        """Display form of the command, quoted for the current platform."""
        if os.name == "nt":
            return subprocess.list2cmdline(self.command_argv)
        return shlex.join(self.command_argv)

    def execute(self) -> ToolResult:
        # Pass argv explicitly: joining to a string and re-splitting is lossy on
        # Windows (POSIX quoting is not understood by non-POSIX shlex.split).
        return self._decorate_result(
            self._command_tool.execute(self.command_line, argv=self.command_argv)
        )

    async def async_execute(self) -> ToolResult:
        """Execute through the shared cancellable process-sandbox path."""
        result = await self._command_tool.async_execute(self.command_line, argv=self.command_argv)
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
