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
import subprocess
from pathlib import Path
from typing import Any

from opennova.tools.base import BaseTool, ToolResult

DEFAULT_TIMEOUT = 30
MAX_OUTPUT_SIZE = 100 * 1024

DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "chmod 777",
    "chmod -R 777",
    "> /etc/",
    ">> /etc/",
    "dd if=",
    "mkfs",
    "fdisk",
    ":(){ :|:& };:",
    "curl | sh",
    "curl | bash",
    "wget | sh",
    "wget | bash",
]


def _check_dangerous_command(command: str) -> tuple[bool, str]:
    """
    Check if command contains dangerous patterns.

    Args:
        command: Command string to check

    Returns:
        Tuple of (is_dangerous, reason)
    """
    command_lower = command.lower()

    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in command_lower:
            return True, f"Command matches dangerous pattern: '{pattern}'"

    return False, ""


class ExecuteCommandTool(BaseTool):
    """Execute shell commands with safety controls."""

    name = "execute_command"
    description = (
        "Execute a shell command in a subprocess. "
        "Commands have timeout limits and are monitored for dangerous patterns. "
        "Use this for running tests, installing packages, git operations, etc."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.default_timeout = self.config.get("command_timeout", DEFAULT_TIMEOUT)
        self.working_dir = self.config.get("working_dir", os.getcwd())

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
        timeout = timeout or self.default_timeout
        work_dir = working_dir or self.working_dir

        is_dangerous, reason = _check_dangerous_command(command)
        if is_dangerous:
            return ToolResult(
                success=False,
                output="",
                error=f"Potentially dangerous command blocked: {reason}",
                metadata={"requires_confirmation": True, "danger_reason": reason},
            )

        try:
            if working_dir:
                work_path = Path(work_dir).resolve()
                if not work_path.exists():
                    return ToolResult(
                        success=False,
                        output="",
                        error=f"Working directory does not exist: {work_dir}",
                    )
                work_dir = str(work_path)
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Invalid working directory: {e}",
            )

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
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
                    "timeout": timeout,
                    "working_dir": work_dir,
                },
            )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output="",
                error=f"Command timed out after {timeout} seconds",
                metadata={
                    "command": command,
                    "timeout": timeout,
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
        timeout = timeout or self.default_timeout
        work_dir = working_dir or self.working_dir

        is_dangerous, reason = _check_dangerous_command(command)
        if is_dangerous:
            return ToolResult(
                success=False,
                output="",
                error=f"Potentially dangerous command blocked: {reason}",
                metadata={"requires_confirmation": True},
            )

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Command timed out after {timeout} seconds",
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
                    "timeout": timeout,
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
