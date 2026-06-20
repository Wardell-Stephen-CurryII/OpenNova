"""Tool progress state helpers for the Textual TUI."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from opennova.tools.base import ToolResult


@dataclass
class ToolProgressTracker:
    """Track current tool progress and render concise status text."""

    clock: Callable[[], float] = time.time
    collapse_threshold: int = 1200
    current_tool_name: str = ""
    current_args: dict[str, Any] = field(default_factory=dict)
    current_tool_id: str = ""
    started_at: float = 0.0
    _sequence: int = 0
    waiting_for_interaction: bool = False
    interaction_label: str = ""
    last_summary: str = ""

    def start_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        tool_id = f"tool_{self._sequence:04d}"
        self.current_tool_name = tool_name
        self.current_args = args
        self.current_tool_id = tool_id
        self.started_at = self.clock()
        self.waiting_for_interaction = False
        self.interaction_label = ""
        self.last_summary = ""
        return {
            "tool_id": tool_id,
            "tool_name": tool_name,
            "arguments": args,
            "started_at": self.started_at,
        }

    def finish_tool(self, result: ToolResult) -> dict[str, Any]:
        tool_name = self.current_tool_name or "tool"
        status = "succeeded" if result.success else "failed"
        elapsed = max(0.0, self.clock() - self.started_at) if self.started_at else 0.0
        self.last_summary = f"{tool_name} {status} in {elapsed:.1f}s"
        output = result.output or ""
        collapsible = len(output) > self.collapse_threshold
        output_preview = (
            output[: self.collapse_threshold] + "\n... (output collapsed)"
            if collapsible
            else output
        )
        event = {
            "tool_id": self.current_tool_id,
            "tool_name": tool_name,
            "summary": self.last_summary,
            "success": result.success,
            "error": result.error,
            "duration_ms": int(elapsed * 1000),
            "started_at": self.started_at,
            "diff": result.metadata.get("diff") if isinstance(result.metadata, dict) else None,
            "collapsible": collapsible,
            "output_preview": output_preview,
        }
        self.current_tool_name = ""
        self.current_args = {}
        self.current_tool_id = ""
        self.started_at = 0.0
        self.waiting_for_interaction = False
        self.interaction_label = ""
        return event

    def start_interaction(self, metadata: dict[str, Any]) -> None:
        self.waiting_for_interaction = True
        questions = metadata.get("questions") or []
        if questions:
            self.interaction_label = str(questions[0].get("header") or "Confirm")
        else:
            self.interaction_label = str(metadata.get("interaction_type") or "Confirm")

    def clear_interaction(self) -> None:
        self.waiting_for_interaction = False
        self.interaction_label = ""

    def status_text(self, frame: str = "") -> str:
        if self.waiting_for_interaction:
            label = self.interaction_label or "Confirm"
            return f"  {frame} Waiting for {label}..."
        if self.current_tool_name:
            elapsed = max(0.0, self.clock() - self.started_at) if self.started_at else 0.0
            return f"  {frame} Running {self.current_tool_name}... ({elapsed:.1f}s)"
        return f"  {frame} Working..."
