"""
Shell command execution tool.

Implements safe command execution with:
- Timeout control
- Output size limits
- Working directory restriction
- Basic security checks
"""

import asyncio
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opennova.security.guardrails import Guardrails, GuardResult
from opennova.tools.base import BaseTool, ToolResult
from opennova.utils.encoding import utf8_environment

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_SIZE = 100 * 1024


@dataclass
class PreparedCommand:
    command: str
    timeout: int | float
    working_dir: str
    run_with_shell: bool
    argv: list[str] | None
    guard_result: GuardResult


class ExecuteCommandTool(BaseTool):
    """Execute shell commands with safety controls."""

    name = "execute_command"
    search_hint = "Run tests, scripts, git commands, package managers, and shell commands"
    description = (
        "Execute a shell command in a subprocess. "
        "Commands have timeout limits and are monitored for dangerous patterns. "
        "Use this for running tests, installing packages, git operations, etc."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.default_timeout = self.config.get("command_timeout", DEFAULT_TIMEOUT)
        self.working_dir = self.config.get("working_dir", os.getcwd())
        self.strict_shell_parsing = bool(self.config.get("strict_shell_parsing", False))
        self.guardrails = Guardrails(
            sandbox_mode=bool(self.config.get("sandbox_mode", True)),
            allowed_paths=self.config.get("allowed_paths", []),
            blocked_commands=self.config.get("blocked_commands", []),
            auto_confirm_safe=bool(self.config.get("auto_confirm_safe", True)),
            allow_network=bool(self.config.get("allow_network", True)),
            permission_mode=self.config.get("permission_mode", "default"),
            always_allow_tools=self.config.get("always_allow_tools", []),
            always_deny_tools=self.config.get("always_deny_tools", []),
            always_ask_tools=self.config.get("always_ask_tools", []),
        )

    def _prepare_command_execution(
        self,
        command: str,
        timeout: int | float | None,
        working_dir: str | None,
    ) -> PreparedCommand | ToolResult:
        """Validate guardrails, cwd, timeout, and shell mode before execution."""
        resolved_timeout = timeout if timeout is not None else self.default_timeout
        if not isinstance(resolved_timeout, (int, float)) or isinstance(resolved_timeout, bool):
            return ToolResult(
                success=False,
                output="",
                error="timeout must be a number of seconds",
                metadata={"guard_blocked": True, "risk_level": "block"},
            )

        work_dir = working_dir or self.working_dir

        guard_result = self.guardrails.check_command(command)
        if not guard_result.allowed:
            return ToolResult(
                success=False,
                output="",
                error=guard_result.reason,
                metadata={
                    "requires_confirmation": guard_result.requires_confirmation,
                    "risk_level": guard_result.risk_level.value,
                    "suggestions": guard_result.suggestions,
                    "guard_blocked": True,
                },
            )

        uses_shell_features = Guardrails.command_uses_shell_features(command)
        if uses_shell_features and self.strict_shell_parsing:
            return ToolResult(
                success=False,
                output="",
                error=(
                    "Command uses shell syntax, but strict shell parsing is enabled. "
                    "Run a plain argv-style command or disable strict_shell_parsing."
                ),
                metadata={
                    "guard_blocked": True,
                    "requires_confirmation": True,
                    "risk_level": "block",
                },
            )

        try:
            work_path = Path(work_dir).resolve()
            if not work_path.exists():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Working directory does not exist: {work_dir}",
                )
            if not work_path.is_dir():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Working directory is not a directory: {work_dir}",
                )
            work_dir = str(work_path)
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Invalid working directory: {e}",
            )

        run_with_shell = uses_shell_features and not self.strict_shell_parsing
        argv: list[str] | None = None
        if not run_with_shell:
            try:
                argv = shlex.split(command, posix=(os.name != "nt"))
            except ValueError as e:
                return ToolResult(success=False, output="", error=f"Invalid command syntax: {e}")
            if not argv:
                return ToolResult(success=False, output="", error="Empty command")

        return PreparedCommand(
            command=command,
            timeout=resolved_timeout,
            working_dir=work_dir,
            run_with_shell=run_with_shell,
            argv=argv,
            guard_result=guard_result,
        )

    def execute(
        self,
        command: str,
        timeout: int | None = None,
        working_dir: str | None = None,
        capture_stderr: bool = True,
    ) -> ToolResult:
        """
        Execute a shell command.

        Args:
            command: Shell command to execute
            timeout: Timeout in seconds (default: 30)
            working_dir: Working directory for command execution
            capture_stderr: Whether to capture stderr output

        Returns:
            ToolResult with command output
        """
        prepared = self._prepare_command_execution(command, timeout, working_dir)
        if isinstance(prepared, ToolResult):
            return prepared

        try:
            result = subprocess.run(
                prepared.command if prepared.run_with_shell else prepared.argv,
                shell=prepared.run_with_shell,
                cwd=prepared.working_dir,
                capture_output=True,
                text=True,
                timeout=prepared.timeout,
                env=utf8_environment(),
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""

            output_parts = []
            if stdout:
                output_parts.append(f"stdout:\n{stdout}")
            if capture_stderr and stderr:
                output_parts.append(f"stderr:\n{stderr}")

            combined_output = "\n\n".join(output_parts) if output_parts else "(no output)"

            truncated_output = self._truncate_output(combined_output)

            success = result.returncode == 0

            if not success:
                output = f"Exit code: {result.returncode}\n\n{truncated_output}"
            else:
                output = truncated_output

            return ToolResult(
                success=success,
                output=output,
                error=None if success else f"Command failed with exit code {result.returncode}",
                metadata={
                    "command": command,
                    "exit_code": result.returncode,
                    "timeout": prepared.timeout,
                    "working_dir": prepared.working_dir,
                    "shell_fallback": prepared.run_with_shell,
                    "requires_confirmation": prepared.guard_result.requires_confirmation,
                    "risk_level": prepared.guard_result.risk_level.value,
                },
            )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output="",
                error=f"Command timed out after {prepared.timeout} seconds",
                metadata={
                    "command": command,
                    "timeout": prepared.timeout,
                },
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to execute command: {e}",
            )

    async def execute_async(
        self,
        command: str,
        timeout: int | None = None,
        working_dir: str | None = None,
    ) -> ToolResult:
        """
        Execute a shell command asynchronously.

        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            working_dir: Working directory

        Returns:
            ToolResult with command output
        """
        prepared = self._prepare_command_execution(command, timeout, working_dir)
        if isinstance(prepared, ToolResult):
            return prepared

        try:
            if prepared.run_with_shell:
                process = await asyncio.create_subprocess_shell(
                    prepared.command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=prepared.working_dir,
                    env=utf8_environment(),
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *(prepared.argv or []),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=prepared.working_dir,
                    env=utf8_environment(),
                )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=prepared.timeout,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Command timed out after {prepared.timeout} seconds",
                )

            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

            output_parts = []
            if stdout_str:
                output_parts.append(f"stdout:\n{stdout_str}")
            if stderr_str:
                output_parts.append(f"stderr:\n{stderr_str}")

            combined_output = "\n\n".join(output_parts) if output_parts else "(no output)"
            truncated_output = self._truncate_output(combined_output)

            success = process.returncode == 0

            if not success:
                output = f"Exit code: {process.returncode}\n\n{truncated_output}"
            else:
                output = truncated_output

            return ToolResult(
                success=success,
                output=output,
                error=None if success else f"Command failed with exit code {process.returncode}",
                metadata={
                    "command": command,
                    "exit_code": process.returncode,
                    "timeout": prepared.timeout,
                    "working_dir": prepared.working_dir,
                    "shell_fallback": prepared.run_with_shell,
                    "requires_confirmation": prepared.guard_result.requires_confirmation,
                    "risk_level": prepared.guard_result.risk_level.value,
                },
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to execute command: {e}",
            )

    def _truncate_output(self, output: str) -> str:
        """Truncate output if too large."""
        if len(output) > MAX_OUTPUT_SIZE:
            return (
                output[: MAX_OUTPUT_SIZE // 2]
                + f"\n\n... [truncated {len(output) - MAX_OUTPUT_SIZE} bytes] ...\n\n"
                + output[-MAX_OUTPUT_SIZE // 2 :]
            )
        return output

    def is_destructive(self, **kwargs: Any) -> bool:
        """Shell commands can mutate state and should generally be confirmed by policy."""
        return True

    def requires_permission(self, **kwargs: Any) -> bool:
        return True
