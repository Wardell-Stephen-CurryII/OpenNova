"""Session-persisted tool permission decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PermissionDecision(StrEnum):
    """Persistable user decisions for future tool calls."""

    ALLOW_ONCE = "allow_once"
    ALWAYS_ALLOW = "always_allow"
    ALWAYS_DENY = "always_deny"
    ALWAYS_ASK = "always_ask"


@dataclass
class PermissionRule:
    """One persisted permission rule."""

    tool_name: str
    decision: PermissionDecision


class PermissionStore:
    """Small JSON-backed permission rule store."""

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path) if storage_path else None
        self.rules: dict[str, PermissionDecision] = {}
        self._load()

    def record(self, tool_name: str, decision: PermissionDecision | str) -> None:
        """Record or replace a decision for a tool."""
        decision_value = PermissionDecision(decision)
        if decision_value == PermissionDecision.ALLOW_ONCE:
            return
        self.rules[tool_name] = decision_value
        self.save()

    def decision_for(self, tool_name: str) -> PermissionDecision | None:
        """Return the persisted decision for a tool, if any."""
        return self.rules.get(tool_name)

    def allowed_tools(self) -> list[str]:
        return [
            tool_name
            for tool_name, decision in self.rules.items()
            if decision == PermissionDecision.ALWAYS_ALLOW
        ]

    def denied_tools(self) -> list[str]:
        return [
            tool_name
            for tool_name, decision in self.rules.items()
            if decision == PermissionDecision.ALWAYS_DENY
        ]

    def ask_tools(self) -> list[str]:
        return [
            tool_name
            for tool_name, decision in self.rules.items()
            if decision == PermissionDecision.ALWAYS_ASK
        ]

    def save(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rules": {tool: decision.value for tool, decision in self.rules.items()}}
        self.storage_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        for tool_name, decision in payload.get("rules", {}).items():
            self.rules[str(tool_name)] = PermissionDecision(decision)
