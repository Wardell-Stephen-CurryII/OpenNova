"""
Guardrails - Safety checks for agent actions.

Provides:
- Command safety validation
- File path safety checks
- HTTP request validation
- Risk level assessment
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RiskLevel(str, Enum):
    """Risk level of an action."""

    SAFE = "safe"
    WARN = "warn"
    DANGER = "danger"
    BLOCK = "block"


@dataclass
class GuardResult:
    """Result of a guardrails check."""

    allowed: bool
    risk_level: RiskLevel
    reason: str
    requires_confirmation: bool = False
    suggestions: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.allowed


DANGEROUS_COMMAND_PATTERNS = [
    (r"rm\s+-rf\s+/", "Delete root directory"),
    (r"rm\s+-rf\s+~", "Delete home directory"),
    (r"rm\s+-rf\s+\*", "Delete all files in current directory"),
    (r"chmod\s+(-R\s+)?777", "Set unsafe permissions"),
    (r"chown\s+.*:\s*/", "Change root ownership"),
    (r">\s*/etc/", "Write to system configuration"),
    (r">\s*/dev/", "Write to device files"),
    (r"dd\s+if=.*of=", "Disk write operation"),
    (r"mkfs", "Format filesystem"),
    (r"fdisk", "Disk partitioning"),
    (r":\(\)\s*\{\s*:\|\:&\s*\}\s*;:", "Fork bomb"),
    (r"curl\s+.*\|\s*(sh|bash|zsh)", "Execute remote script"),
    (r"wget\s+.*\|\s*(sh|bash|zsh)", "Execute remote script"),
    (r"eval\s+", "Eval command execution"),
    (r"exec\s+", "Exec command"),
    (r"sudo\s+", "Elevated privileges"),
    (r"su\s+", "Switch user"),
    (r"shutdown", "System shutdown"),
    (r"reboot", "System reboot"),
    (r"init\s+[06]", "System shutdown/reboot"),
]

PROTECTED_PATHS = [
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/root",
    "~/.ssh",
    "~/.gnupg",
    "~/.config/git",
]

SENSITIVE_FILE_PATTERNS = [
    r"\.env$",
    r"\.pem$",
    r"\.key$",
    r"\.p12$",
    r"\.pfx$",
    r"id_rsa",
    r"id_ed25519",
    r"\.gitconfig$",
    r"\.netrc$",
    r"\.pgpass$",
    r"credentials\.json$",
    r"secrets\.json$",
    r"secrets\.yaml$",
    r"\.htpasswd$",
]


class Guardrails:
    """
    Safety checker for agent actions.

    Checks:
    - Shell commands for dangerous patterns
    - File paths for protected locations
    - HTTP requests for suspicious URLs
    """

    def __init__(
        self,
        sandbox_mode: bool = True,
        allowed_paths: list[str] | None = None,
        blocked_commands: list[str] | None = None,
        auto_confirm_safe: bool = True,
    ):
        """
        Initialize guardrails.

        Args:
            sandbox_mode: Enable path sandboxing
            allowed_paths: Whitelist of allowed paths
            blocked_commands: Additional blocked commands
            auto_confirm_safe: Auto-confirm safe operations
        """
        self.sandbox_mode = sandbox_mode
        self.allowed_paths = allowed_paths or []
        self.blocked_commands = blocked_commands or []
        self.auto_confirm_safe = auto_confirm_safe

    def check_command(self, command: str) -> GuardResult:
        """
        Check if a command is safe to execute.

        Args:
            command: Shell command to check

        Returns:
            GuardResult with safety assessment
        """
        command_stripped = command.strip()

        for blocked in self.blocked_commands:
            if blocked.lower() in command_stripped.lower():
                return GuardResult(
                    allowed=False,
                    risk_level=RiskLevel.BLOCK,
                    reason=f"Command is in blocked list: {blocked}",
                    requires_confirmation=False,
                )

        for pattern, description in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, command_stripped, re.IGNORECASE):
                return GuardResult(
                    allowed=False,
                    risk_level=RiskLevel.BLOCK,
                    reason=f"Potentially dangerous command detected: {description}",
                    requires_confirmation=True,
                    suggestions=[
                        "Review the command carefully before executing",
                        "Consider using a safer alternative",
                    ],
                )

        destructive_patterns = [
            (r"rm\s+", "File deletion"),
            (r"rmdir", "Directory removal"),
            (r"git\s+push\s+--force", "Force push to git"),
            (r"git\s+reset\s+--hard", "Hard reset git"),
            (r"drop\s+(table|database|schema)", "Database drop"),
            (r"delete\s+from", "Database deletion"),
            (r"truncate\s+", "Table truncation"),
        ]

        for pattern, description in destructive_patterns:
            if re.search(pattern, command_stripped, re.IGNORECASE):
                return GuardResult(
                    allowed=True,
                    risk_level=RiskLevel.WARN,
                    reason=f"Destructive operation detected: {description}",
                    requires_confirmation=True,
                    suggestions=[
                        "Ensure you have backups",
                        "Double-check the target",
                    ],
                )

        return GuardResult(
            allowed=True,
            risk_level=RiskLevel.SAFE,
            reason="Command appears safe",
            requires_confirmation=not self.auto_confirm_safe,
        )

    def check_file_path(
        self,
        file_path: str,
        operation: str = "read",
        working_dir: str | None = None,
    ) -> GuardResult:
        """
        Check if a file path is safe to access.

        Args:
            file_path: Path to check
            operation: Operation type (read/write/delete)
            working_dir: Working directory for sandbox

        Returns:
            GuardResult with path safety assessment
        """
        try:
            path = Path(file_path).expanduser().resolve()
        except Exception as e:
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason=f"Invalid path: {e}",
            )

        for protected in PROTECTED_PATHS:
            protected_path = Path(protected).expanduser().resolve()
            try:
                path.relative_to(protected_path)
                return GuardResult(
                    allowed=False,
                    risk_level=RiskLevel.BLOCK,
                    reason=f"Access to protected system path: {protected}",
                    requires_confirmation=True,
                )
            except ValueError:
                pass

        for pattern in SENSITIVE_FILE_PATTERNS:
            if re.search(pattern, file_path, re.IGNORECASE):
                return GuardResult(
                    allowed=True,
                    risk_level=RiskLevel.WARN,
                    reason="Accessing potentially sensitive file",
                    requires_confirmation=True,
                    suggestions=["Verify this is intentional"],
                )

        if self.sandbox_mode and working_dir:
            work_path = Path(working_dir).resolve()
            try:
                path.relative_to(work_path)
            except ValueError:
                if not any(
                    path.is_relative_to(Path(p).resolve()) for p in self.allowed_paths
                ):
                    return GuardResult(
                        allowed=False,
                        risk_level=RiskLevel.DANGER,
                        reason=f"Path outside working directory: {file_path}",
                        requires_confirmation=True,
                        suggestions=[
                            f"Move to working directory: {working_dir}",
                            "Add path to allowed_paths in configuration",
                        ],
                    )

        return GuardResult(
            allowed=True,
            risk_level=RiskLevel.SAFE,
            reason="Path is safe to access",
            requires_confirmation=operation in ("write", "delete"),
        )

    def check_http_request(self, url: str, method: str = "GET") -> GuardResult:
        """
        Check if an HTTP request is safe.

        Args:
            url: URL to check
            method: HTTP method

        Returns:
            GuardResult with request safety assessment
        """
        import re
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except Exception as e:
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason=f"Invalid URL: {e}",
            )

        if not parsed.scheme in ("http", "https"):
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason=f"Unsupported URL scheme: {parsed.scheme}",
            )

        internal_hosts = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "192.168.", "10.", "172.")
        is_internal = any(
            parsed.hostname and parsed.hostname.startswith(h)
            for h in internal_hosts
        ) or parsed.hostname in internal_hosts

        if is_internal:
            return GuardResult(
                allowed=True,
                risk_level=RiskLevel.WARN,
                reason="Request to internal/local address",
                requires_confirmation=True,
            )

        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            return GuardResult(
                allowed=True,
                risk_level=RiskLevel.WARN,
                reason=f"Mutating HTTP request: {method}",
                requires_confirmation=True,
            )

        suspicious_patterns = [
            r"login",
            r"signin",
            r"auth",
            r"password",
            r"secret",
            r"api[_-]?key",
            r"token",
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return GuardResult(
                    allowed=True,
                    risk_level=RiskLevel.WARN,
                    reason="URL contains potentially sensitive keywords",
                    requires_confirmation=True,
                )

        return GuardResult(
            allowed=True,
            risk_level=RiskLevel.SAFE,
            reason="Request appears safe",
            requires_confirmation=False,
        )

    def check_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        working_dir: str | None = None,
    ) -> GuardResult:
        """
        Check if a tool call is safe.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments
            working_dir: Working directory for sandbox

        Returns:
            GuardResult with tool call safety assessment
        """
        if tool_name == "execute_command":
            command = arguments.get("command", "")
            return self.check_command(command)

        elif tool_name == "read_file":
            file_path = arguments.get("file_path", "")
            return self.check_file_path(file_path, "read", working_dir)

        elif tool_name in ("write_file", "create_file"):
            file_path = arguments.get("file_path", "")
            return self.check_file_path(file_path, "write", working_dir)

        elif tool_name == "delete_file":
            file_path = arguments.get("file_path", "")
            return self.check_file_path(file_path, "delete", working_dir)

        elif tool_name == "http_request":
            url = arguments.get("url", "")
            method = arguments.get("method", "GET")
            return self.check_http_request(url, method)

        return GuardResult(
            allowed=True,
            risk_level=RiskLevel.SAFE,
            reason="Tool not in high-risk category",
            requires_confirmation=False,
        )
