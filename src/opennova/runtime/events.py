"""Canonical runtime event types shared by SDK and TUI surfaces."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Literal

from opennova.runtime.cancellation import CancellationToken

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
    read_file_cache: Any = field(default_factory=dict)
    abort_signal: CancellationToken | None = None
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


_CURRENT_TOOL_CONTEXT: ContextVar[ToolUseContext | None] = ContextVar(
    "opennova_tool_use_context",
    default=None,
)


def set_current_tool_context(context: ToolUseContext | None) -> Token[ToolUseContext | None]:
    """Bind a tool context to the current async execution flow."""
    return _CURRENT_TOOL_CONTEXT.set(context)


def reset_current_tool_context(token: Token[ToolUseContext | None]) -> None:
    """Restore the prior tool context after execution."""
    _CURRENT_TOOL_CONTEXT.reset(token)


def current_tool_context() -> ToolUseContext | None:
    """Return the active tool context for cancellation-aware tools."""
    return _CURRENT_TOOL_CONTEXT.get()
