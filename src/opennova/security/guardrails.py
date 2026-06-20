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
from enum import StrEnum
from pathlib import Path
from typing import Any


class RiskLevel(StrEnum):
    """Risk level of an action."""

    SAFE = "safe"
    WARN = "warn"
    DANGER = "danger"
    BLOCK = "block"


class PermissionMode(StrEnum):
    """High-level permission mode for tool execution."""

    DEFAULT = "default"
    ASK = "ask"
    ALLOW_EDITS = "allowEdits"
    READ_ONLY = "readOnly"
    BYPASS = "bypass"


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

NETWORK_COMMAND_PATTERNS = [
    (r"\bcurl\b", "Network command: curl"),
    (r"\bwget\b", "Network command: wget"),
    (r"\bpip(?:3)?\s+(install|download)\b", "Python package download/install"),
    (r"\buv\s+(sync|add|pip|tool\s+install)\b", "uv dependency/network operation"),
    (r"\bnpm\s+(install|i|update|add)\b", "npm package install/update"),
    (r"\byarn\s+(add|install)\b", "yarn package install"),
    (r"\bpnpm\s+(add|install)\b", "pnpm package install"),
    (r"\bgit\s+(clone|fetch|pull)\b", "git network operation"),
]

SHELL_FEATURE_PATTERNS = [
    r"\|",
    r">>",
    r"(?<!\d)>",
    r"<",
    r"\$\(",
    r"`[^`]+`",
    r"\*",
    r"\?",
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
        allow_network: bool = True,
        permission_mode: str | PermissionMode = PermissionMode.DEFAULT,
        always_allow_tools: list[str] | None = None,
        always_deny_tools: list[str] | None = None,
        always_ask_tools: list[str] | None = None,
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
        self.allow_network = allow_network
        self.permission_mode = PermissionMode(permission_mode)
        self.always_allow_tools = set(always_allow_tools or [])
        self.always_deny_tools = set(always_deny_tools or [])
        self.always_ask_tools = set(always_ask_tools or [])

    @staticmethod
    def _tool_category(tool_name: str) -> str:
        if tool_name in {
            "read_file",
            "list_directory",
            "glob_files",
            "grep_code",
            "web_search",
            "web_fetch",
            "list_mcp_resources",
            "read_mcp_resource",
            "python_diagnostics",
            "python_symbols",
            "python_definition",
            "python_references",
        }:
            return "read"
        if tool_name in {
            "write_file",
            "create_file",
            "edit_file",
            "multi_edit_file",
            "delete_file",
            "enter_worktree",
            "exit_worktree",
        }:
            return "edit"
        if tool_name == "execute_command":
            return "command"
        return "other"

    def _check_permission_mode(self, tool_name: str) -> GuardResult | None:
        if tool_name in self.always_deny_tools:
            return GuardResult(False, RiskLevel.BLOCK, f"Tool is denied by policy: {tool_name}")
        if self.permission_mode == PermissionMode.BYPASS:
            return GuardResult(True, RiskLevel.SAFE, f"Tool is allowed by policy: {tool_name}")

        category = self._tool_category(tool_name)
        if self.permission_mode == PermissionMode.READ_ONLY and category != "read":
            return GuardResult(False, RiskLevel.BLOCK, f"Read-only mode blocks tool: {tool_name}")
        if tool_name in self.always_ask_tools:
            return GuardResult(True, RiskLevel.WARN, f"Tool requires confirmation by policy: {tool_name}", True)
        if self.permission_mode == PermissionMode.ASK and category != "read":
            return GuardResult(True, RiskLevel.WARN, f"Permission required for tool: {tool_name}", True)
        if self.permission_mode == PermissionMode.ALLOW_EDITS and category == "command":
            return GuardResult(True, RiskLevel.WARN, f"Command requires confirmation by policy: {tool_name}", True)
        return None

    def _apply_permission_overlay(
        self,
        tool_name: str,
        safety_result: GuardResult,
        permission_result: GuardResult | None,
    ) -> GuardResult:
        """Apply permission policy without letting it bypass hard safety blocks."""
        if not safety_result.allowed:
            return safety_result
        if tool_name in self.always_allow_tools:
            safety_result.requires_confirmation = False
            if safety_result.risk_level == RiskLevel.WARN:
                safety_result.risk_level = RiskLevel.SAFE
            return safety_result
        if permission_result and permission_result.requires_confirmation:
            return permission_result
        return safety_result

    @staticmethod
    def command_uses_shell_features(command: str) -> bool:
        """Return whether command uses shell-specific syntax."""
        stripped = command.strip()
        if not stripped:
            return False
        return any(re.search(pattern, stripped) for pattern in SHELL_FEATURE_PATTERNS)

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

        if not self.allow_network:
            for pattern, description in NETWORK_COMMAND_PATTERNS:
                if re.search(pattern, command_stripped, re.IGNORECASE):
                    return GuardResult(
                        allowed=False,
                        risk_level=RiskLevel.BLOCK,
                        reason=f"Network access is disabled by policy: {description}",
                        requires_confirmation=False,
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

        if self.command_uses_shell_features(command_stripped):
            return GuardResult(
                allowed=True,
                risk_level=RiskLevel.WARN,
                reason="Command uses shell syntax and requires shell fallback parsing",
                requires_confirmation=True,
                suggestions=[
                    "Prefer argument-based commands without shell operators",
                    "Confirm shell fallback execution if this is intentional",
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

        if parsed.scheme not in ("http", "https"):
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason=f"Unsupported URL scheme: {parsed.scheme}",
            )

        if not self.allow_network:
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason="Network access is disabled by policy",
                requires_confirmation=False,
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
        permission_result = self._check_permission_mode(tool_name)
        if permission_result is not None and (
            not permission_result.allowed or self.permission_mode == PermissionMode.BYPASS
        ):
            return permission_result

        if tool_name == "execute_command":
            command = arguments.get("command", "")
            result = self.check_command(command)

        elif tool_name == "read_file":
            file_path = arguments.get("file_path", "")
            result = self.check_file_path(file_path, "read", working_dir)

        elif tool_name in ("write_file", "create_file", "edit_file", "multi_edit_file"):
            file_path = arguments.get("file_path", "")
            result = self.check_file_path(file_path, "write", working_dir)

        elif tool_name == "delete_file":
            file_path = arguments.get("file_path", "")
            result = self.check_file_path(file_path, "delete", working_dir)
        elif tool_name in ("list_directory", "glob_files", "grep_code"):
            directory = arguments.get("directory", ".")
            result = self.check_file_path(directory, "read", working_dir)

        elif tool_name in ("enter_worktree", "exit_worktree"):
            result = GuardResult(
                allowed=True,
                risk_level=RiskLevel.WARN,
                reason="Git worktree operation changes repository checkout state",
                requires_confirmation=True,
                suggestions=[
                    "Confirm the target path and branch name before proceeding",
                    "Review uncommitted changes before removing a worktree",
                ],
            )

        elif tool_name == "http_request":
            url = arguments.get("url", "")
            method = arguments.get("method", "GET")
            result = self.check_http_request(url, method)
        elif tool_name == "web_fetch":
            url = arguments.get("url", "")
            result = self.check_http_request(url, "GET")

        else:
            result = GuardResult(
                allowed=True,
                risk_level=RiskLevel.SAFE,
                reason="Tool not in high-risk category",
                requires_confirmation=False,
            )

        return self._apply_permission_overlay(tool_name, result, permission_result)
