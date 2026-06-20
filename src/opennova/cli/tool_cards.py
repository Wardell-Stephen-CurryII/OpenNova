"""Pure data model for TUI tool cards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opennova.runtime.events import ToolEvent


@dataclass
class ToolCard:
    """State for one visual tool card."""

    tool_id: str
    tool_name: str
    status: str = "running"
    output_preview: str = ""
    error: str | None = None
    diff: str | None = None
    collapsible: bool = False
    permission_reason: str = ""
    cancelled: bool = False
    metadata: dict[str, Any] | None = None


class ToolCardStore:
    """Maintain tool card state from canonical tool events."""

    def __init__(self, collapse_threshold: int = 1200):
        self.collapse_threshold = collapse_threshold
        self.cards: dict[str, ToolCard] = {}

    def apply_event(self, event: ToolEvent) -> ToolCard:
        card = self.cards.get(event.tool_id)
        if card is None:
            card = ToolCard(tool_id=event.tool_id, tool_name=event.tool_name, metadata={})
            self.cards[event.tool_id] = card

        if event.type == "tool_start":
            card.status = "running"
        elif event.type == "permission_request":
            card.status = "waiting_for_permission"
            card.permission_reason = str(event.metadata.get("reason", ""))
        elif event.type in {"tool_result", "tool_error"}:
            card.status = "succeeded" if event.success else "failed"
            card.error = event.error
            card.diff = event.diff
            output = event.output or ""
            card.collapsible = len(output) > self.collapse_threshold
            card.output_preview = (
                output[: self.collapse_threshold] + "\n... (output collapsed)"
                if card.collapsible
                else output
            )
        elif event.type == "tool_cancelled":
            card.status = "cancelled"
            card.cancelled = True

        card.metadata = {**(card.metadata or {}), **event.metadata}
        return card

    def cancel(self, tool_id: str) -> ToolCard:
        card = self.cards[tool_id]
        card.status = "cancelled"
        card.cancelled = True
        return card

    def get(self, tool_id: str) -> ToolCard:
        return self.cards[tool_id]
