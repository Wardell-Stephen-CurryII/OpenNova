"""Trusted local plugin command-backed tools."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from opennova.tools.base import BaseTool, ToolResult
from opennova.utils.encoding import utf8_environment


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

    def execute(self) -> ToolResult:
        try:
            cwd = Path(self.config.get("working_dir", ".")).resolve()
            result = subprocess.run(
                [self.command, *self.args],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                env=utf8_environment(),
            )
            output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
            return ToolResult(
                success=result.returncode == 0,
                output=output or "(no output)",
                error=None if result.returncode == 0 else f"Plugin tool exited {result.returncode}",
                metadata={
                    "plugin_tool": True,
                    "returncode": result.returncode,
                    "permission": self.permission,
                },
            )
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc), metadata={"plugin_tool": True})

    def is_read_only(self, **kwargs: Any) -> bool:
        return self._read_only

    def requires_permission(self, **kwargs: Any) -> bool:
        return not self._read_only
