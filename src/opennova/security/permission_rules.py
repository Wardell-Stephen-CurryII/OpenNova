"""Parameter-scoped permission rules for guardrails."""

from __future__ import annotations

import fnmatch
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PermissionRule:
    """A config-defined permission rule scoped by tool and arguments."""

    id: str
    tool: str
    decision: str
    path_globs: list[str] = field(default_factory=list)
    command_prefixes: list[str] = field(default_factory=list)
    command_families: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_config(cls, data: dict[str, Any]) -> PermissionRule | None:
        tool = str(data.get("tool") or "").strip()
        decision = str(data.get("decision") or "").strip().lower()
        if not tool or decision not in {"allow", "ask", "deny"}:
            return None
        rule_id = str(data.get("id") or f"{tool}:{decision}").strip()
        return cls(
            id=rule_id,
            tool=tool,
            decision=decision,
            path_globs=_as_list(data.get("path_globs")),
            command_prefixes=_as_list(data.get("command_prefixes")),
            command_families=_as_list(data.get("command_families")),
            reason=str(data.get("reason") or "").strip(),
        )

    def matches(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        working_dir: str | None = None,
        command_analysis: dict[str, Any] | None = None,
    ) -> bool:
        if self.tool not in {tool_name, "*"}:
            return False
        if self.path_globs and not self._matches_path(arguments, working_dir):
            return False
        if self.command_prefixes and not self._matches_command_prefix(command_analysis):
            return False
        return not (
            self.command_families and not self._matches_command_family(command_analysis)
        )

    def _matches_path(self, arguments: dict[str, Any], working_dir: str | None) -> bool:
        candidate = _extract_path(arguments)
        if not candidate:
            return False
        normalized = _normalize_candidate_path(candidate, working_dir)
        return any(
            pattern == "**"
            or fnmatch.fnmatch(normalized, pattern)
            or fnmatch.fnmatch(candidate, pattern)
            for pattern in self.path_globs
        )

    def _matches_command_prefix(self, command_analysis: dict[str, Any] | None) -> bool:
        argv = list((command_analysis or {}).get("argv") or [])
        if not argv:
            return False
        for prefix in self.command_prefixes:
            try:
                prefix_argv = shlex.split(prefix, posix=(os.name != "nt"))
            except ValueError:
                prefix_argv = prefix.split()
            if prefix_argv and argv[: len(prefix_argv)] == prefix_argv:
                return True
        return False

    def _matches_command_family(self, command_analysis: dict[str, Any] | None) -> bool:
        if not command_analysis:
            return False
        family = str(command_analysis.get("family") or "")
        executable = str(command_analysis.get("executable") or "")
        return any(value in {family, executable} for value in self.command_families)


class PermissionRuleMatcher:
    """Find the first matching parameter-scoped permission rule."""

    def __init__(self, rules: list[PermissionRule] | None = None):
        self.rules = rules or []

    @classmethod
    def from_config(cls, rules_config: list[dict[str, Any]] | None) -> PermissionRuleMatcher:
        rules: list[PermissionRule] = []
        for item in rules_config or []:
            if isinstance(item, dict):
                rule = PermissionRule.from_config(item)
                if rule:
                    rules.append(rule)
        return cls(rules)

    def match(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        working_dir: str | None = None,
        command_analysis: dict[str, Any] | None = None,
    ) -> PermissionRule | None:
        for rule in self.rules:
            if rule.matches(tool_name, arguments, working_dir, command_analysis):
                return rule
        return None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _extract_path(arguments: dict[str, Any]) -> str:
    for key in ("file_path", "path", "directory"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _normalize_candidate_path(candidate: str, working_dir: str | None) -> str:
    try:
        path = Path(candidate).expanduser()
        if not path.is_absolute() and working_dir:
            path = Path(working_dir) / path
        resolved = path.resolve()
        if working_dir:
            try:
                return resolved.relative_to(Path(working_dir).resolve()).as_posix()
            except ValueError:
                pass
        return resolved.as_posix()
    except Exception:
        return candidate.replace(os.sep, "/")
