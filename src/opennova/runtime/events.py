"""Canonical runtime event types shared by SDK, TUI, and CLI surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ToolEventType = Literal[
    "tool_start",
    "permission_request",
    "tool_result",
    "tool_error",
    "tool_cancelled",
]


@dataclass
class ToolUseContext:
    """Stable context for one tool invocation."""

    tool_id: str
    tool_name: str
    arguments: dict[str, Any]
    session_id: str | None = None
    permission_context: dict[str, Any] = field(default_factory=dict)
    read_file_cache: dict[str, str] = field(default_factory=dict)
    abort_signal: Any | None = None
    risk_level: str = "safe"
    diff: str | None = None
    max_result_chars: int | None = None
    non_interactive: bool = False
    started_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolEvent:
    """Canonical event emitted around tool execution."""

    type: ToolEventType
    tool_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    started_at: float | None = None
    duration_ms: int | None = None
    risk_level: str = "safe"
    success: bool | None = None
    output: str = ""
    error: str | None = None
    diff: str | None = None
    collapsible: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable event payload."""
        return {
            "type": self.type,
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "risk_level": self.risk_level,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "diff": self.diff,
            "collapsible": self.collapsible,
            "metadata": self.metadata,
        }
