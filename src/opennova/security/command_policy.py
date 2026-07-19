"""Structured shell command analysis for guardrails."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

from opennova.security.network_policy import NetworkPolicy

SHELL_FEATURE_PATTERNS = [
    r"\|",
    r"&&",
    r"\|\|",
    r";",
    r">>",
    r"(?<!\d)>",
    r"<",
    r"\$\(",
    r"`[^`]+`",
    r"\*",
    r"\?",
]


@dataclass
class CommandAnalysis:
    """Structured interpretation of a shell command."""

    command: str
    argv: list[str] = field(default_factory=list)
    executable: str = ""
    family: str = "unknown"
    operation: str = "unknown"
    uses_shell_features: bool = False
    risk_level: str = "safe"
    reason: str = "Command appears safe"
    network_analysis: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "command": self.command,
            "argv": list(self.argv),
            "executable": self.executable,
            "family": self.family,
            "operation": self.operation,
            "uses_shell_features": self.uses_shell_features,
            "risk_level": self.risk_level,
            "reason": self.reason,
        }
        if self.network_analysis:
            data["network_analysis"] = self.network_analysis
        return data


class CommandPolicy:
    """Classify command intent before regex fallback checks run."""

    NETWORK_EXECUTABLES = {"curl", "wget", "ssh", "scp", "rsync"}
    DEV_CHECK_EXECUTABLES = {"pytest", "ruff", "mypy"}
    PACKAGE_EXECUTABLES = {"uv", "pip", "pip3", "npm", "yarn", "pnpm"}

    def __init__(
        self,
        allow_network: bool = True,
        strict_shell_parsing: bool = False,
        network_policy: NetworkPolicy | None = None,
    ):
        self.allow_network = allow_network
        self.strict_shell_parsing = strict_shell_parsing
        self.network_policy = network_policy or NetworkPolicy()

    @staticmethod
    def command_uses_shell_features(command: str) -> bool:
        stripped = command.strip()
        if not stripped:
            return False
        return any(re.search(pattern, stripped) for pattern in SHELL_FEATURE_PATTERNS)

    def analyze(self, command: str) -> CommandAnalysis:
        command_stripped = command.strip()
        uses_shell_features = self.command_uses_shell_features(command_stripped)
        try:
            argv = shlex.split(command_stripped, posix=(os.name != "nt"))
        except ValueError:
            argv = []

        executable = PathLike.executable_name(argv[0]) if argv else ""
        analysis = CommandAnalysis(
            command=command_stripped,
            argv=argv,
            executable=executable,
            uses_shell_features=uses_shell_features,
        )

        if not command_stripped:
            analysis.risk_level = "block"
            analysis.reason = "Empty command"
            return analysis

        if uses_shell_features:
            analysis.risk_level = "block" if self.strict_shell_parsing else "warn"
            analysis.reason = (
                "Command uses shell syntax, but strict shell parsing is enabled"
                if self.strict_shell_parsing
                else "Command uses shell syntax and requires shell fallback parsing"
            )

        if not executable:
            return analysis

        if executable == "git":
            self._classify_git(analysis)
        elif executable in self.PACKAGE_EXECUTABLES:
            self._classify_package_manager(analysis)
        elif executable in self.DEV_CHECK_EXECUTABLES:
            self._set_if_lower_risk(analysis, "dev-check", executable, "safe", "Development check command")
        elif executable in {"python", "python3", "node"}:
            self._classify_runtime(analysis)
        elif executable in {"rm", "rmdir", "mv"}:
            self._classify_file_mutation(analysis)
        elif executable in self.NETWORK_EXECUTABLES:
            self._classify_network(analysis)

        return analysis

    def _set_if_lower_risk(
        self,
        analysis: CommandAnalysis,
        family: str,
        operation: str,
        risk_level: str,
        reason: str,
    ) -> None:
        analysis.family = family
        analysis.operation = operation
        if self._risk_rank(risk_level) > self._risk_rank(analysis.risk_level):
            analysis.risk_level = risk_level
            analysis.reason = reason
        elif analysis.risk_level == "safe":
            analysis.reason = reason

    @staticmethod
    def _risk_rank(risk_level: str) -> int:
        return {"safe": 0, "warn": 1, "danger": 2, "block": 3}.get(risk_level, 0)

    def _classify_git(self, analysis: CommandAnalysis) -> None:
        args = analysis.argv[1:]
        subcommand = args[0] if args else ""
        analysis.family = "git"
        analysis.operation = subcommand or "unknown"

        if subcommand in {"status", "log", "diff", "show", "branch", "rev-parse"}:
            if analysis.risk_level == "safe":
                analysis.reason = "Read-only git command"
            return

        if subcommand == "reset" and "--hard" in args:
            analysis.risk_level = "danger"
            analysis.reason = "Destructive git hard reset"
            return
        if subcommand == "push" and any(
            arg == "-f" or arg.startswith("--force") for arg in args
        ):
            analysis.risk_level = "danger"
            analysis.reason = "Destructive git force push"
            return
        if subcommand == "clean" and any(
            arg == "--force" or (arg.startswith("-") and "f" in arg[1:]) for arg in args
        ):
            analysis.risk_level = "danger"
            analysis.reason = "Destructive git clean"
            return
        if subcommand in {"clone", "fetch", "pull", "push"} and not self.allow_network:
            analysis.risk_level = "block"
            analysis.reason = "Network access is disabled by policy"
            return
        if subcommand in {"clone", "fetch", "pull", "push"}:
            self._apply_network_policy(analysis)
            if analysis.risk_level == "block":
                return
        if subcommand in {"clean", "reset", "restore", "checkout", "switch", "push"}:
            analysis.risk_level = "warn"
            analysis.reason = "Git command may change repository state"

    def _classify_package_manager(self, analysis: CommandAnalysis) -> None:
        executable = analysis.executable
        args = analysis.argv[1:]
        analysis.family = executable
        analysis.operation = " ".join(args[:2]) if args else "unknown"

        joined = " ".join(args)
        if executable == "uv" and args[:2] in (["run", "pytest"], ["run", "ruff"], ["run", "mypy"]):
            if analysis.risk_level == "safe":
                analysis.reason = "Development check command"
            return
        if executable == "uv" and args[:2] == ["run", "python"] and "-c" in args:
            analysis.risk_level = "warn"
            analysis.reason = "Inline Python execution requires confirmation"
            return
        if executable in {"pip", "pip3"} and args and args[0] in {"install", "download"}:
            analysis.risk_level = "block" if not self.allow_network else "warn"
            analysis.reason = "Package download/install command"
            return
        if executable == "uv" and re.search(r"\b(sync|add|pip|tool install)\b", joined):
            analysis.risk_level = "block" if not self.allow_network else "warn"
            analysis.reason = "uv dependency or network operation"
            return
        if executable in {"npm", "yarn", "pnpm"} and args and args[0] in {
            "install",
            "i",
            "add",
            "update",
        }:
            analysis.risk_level = "block" if not self.allow_network else "warn"
            analysis.reason = "Package install/update command"

    def _classify_runtime(self, analysis: CommandAnalysis) -> None:
        analysis.family = analysis.executable
        analysis.operation = "inline-eval" if any(arg in {"-c", "-e"} for arg in analysis.argv[1:]) else "run"
        if analysis.operation == "inline-eval":
            analysis.risk_level = "warn"
            analysis.reason = "Inline code execution requires confirmation"

    def _classify_file_mutation(self, analysis: CommandAnalysis) -> None:
        analysis.family = "file-mutation"
        analysis.operation = analysis.executable
        analysis.risk_level = "danger" if analysis.executable in {"rm", "rmdir"} else "warn"
        analysis.reason = f"File mutation command: {analysis.executable}"

    def _classify_network(self, analysis: CommandAnalysis) -> None:
        analysis.family = "network"
        analysis.operation = analysis.executable
        if not self.allow_network:
            analysis.risk_level = "block"
            analysis.reason = "Network access is disabled by policy"
            return

        self._apply_network_policy(analysis)
        if analysis.risk_level == "block":
            return

        if analysis.risk_level == "safe":
            analysis.risk_level = "warn"
            analysis.reason = f"Network command: {analysis.executable}"

    def _apply_network_policy(self, analysis: CommandAnalysis) -> None:
        url = _extract_network_target(analysis.argv)
        if not url:
            return
        method = _extract_network_method(analysis.argv, analysis.executable)
        network_analysis = self.network_policy.evaluate(url, method).to_dict()
        analysis.network_analysis = network_analysis
        network_risk = str(network_analysis.get("risk_level") or "safe")
        if self._risk_rank(network_risk) > self._risk_rank(analysis.risk_level):
            analysis.risk_level = network_risk
            analysis.reason = str(network_analysis.get("reason") or analysis.reason)


class PathLike:
    """Small path helper that avoids importing pathlib for one basename operation."""

    @staticmethod
    def executable_name(value: str) -> str:
        return os.path.basename(value).lower()


def _extract_network_target(argv: list[str]) -> str:
    for arg in argv[1:]:
        if arg.startswith(("http://", "https://", "ssh://", "git://")):
            return arg
        if "@" in arg and ":" in arg and not arg.startswith("-"):
            return "ssh://" + arg.split(":", 1)[0]
    return ""


def _extract_network_method(argv: list[str], executable: str) -> str:
    args = argv[1:]
    if executable == "curl":
        for index, arg in enumerate(args):
            if arg in {"-X", "--request"} and index + 1 < len(args):
                return args[index + 1].upper()
            if arg.startswith("--request="):
                return arg.split("=", 1)[1].upper()
        if any(arg in {"-d", "--data", "--data-raw", "--data-binary", "-F", "--form"} for arg in args):
            return "POST"
        if any(arg in {"-T", "--upload-file"} for arg in args):
            return "PUT"
    if executable == "wget":
        for index, arg in enumerate(args):
            if arg == "--method" and index + 1 < len(args):
                return args[index + 1].upper()
            if arg.startswith("--method="):
                return arg.split("=", 1)[1].upper()
        if any(arg.startswith(("--post-data", "--post-file")) for arg in args):
            return "POST"
    return "GET"
