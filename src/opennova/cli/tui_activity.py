"""Per-turn tool activity aggregation for the TUI conversation stream."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

TERMINAL_TOOL_EVENTS = {"tool_result", "tool_error", "tool_cancelled"}
MUTATING_TOOLS = {
    "write_file",
    "create_file",
    "edit_file",
    "multi_edit_file",
    "delete_file",
}
PATH_ARGUMENTS = ("file_path", "directory", "path")


@dataclass(frozen=True)
class TurnActivitySummary:
    """Compact summary of all tool activity in one conversational turn."""

    tool_count: int = 0
    file_count: int = 0
    change_count: int = 0
    failed_count: int = 0
    waiting_count: int = 0
    duration_ms: float = 0.0
    tool_names: tuple[str, ...] = ()

    @property
    def has_activity(self) -> bool:
        return self.tool_count > 0

    @property
    def status(self) -> str:
        if self.failed_count:
            return "failed"
        if self.waiting_count:
            return "waiting"
        return "done"


class TurnActivityAccumulator:
    """Collect canonical or transcript tool events until a turn is flushed."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._started_at = time.perf_counter()
        self._calls: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._paths: set[str] = set()
        self._sequence = 0

    def apply_event(self, event: Any) -> None:
        data = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        event_type = str(data.get("type") or data.get("kind") or "")
        if event_type not in {"tool_start", "permission_request", *TERMINAL_TOOL_EVENTS}:
            return
        tool_name = str(data.get("tool_name") or "tool")
        tool_id = str(data.get("tool_id") or "")
        if not tool_id:
            tool_id = self._match_or_create_tool_id(tool_name, event_type)
        call = self._ensure_call(tool_id, tool_name)
        self._collect_paths(data)

        if event_type == "permission_request":
            call["waiting"] = True
            return
        if event_type in TERMINAL_TOOL_EVENTS:
            call["terminal"] = True
            call["waiting"] = False
            call["failed"] = event_type != "tool_result" or data.get("success") is False
            call["changed"] = call["tool_name"] in MUTATING_TOOLS and not call["failed"]
            call["duration_ms"] = float(data.get("duration_ms") or 0.0)

    def apply_transcript_event(self, event: dict[str, Any]) -> None:
        """Collect the existing transcript schema without changing it."""
        kind = str(event.get("kind") or "")
        if kind not in {"tool_start", "tool_result"}:
            return
        data = dict(event)
        data["type"] = kind
        if kind == "tool_result":
            summary = str(event.get("summary_markup") or "").lower()
            data["success"] = not bool(event.get("error")) and "failed" not in summary
            data["duration_ms"] = _duration_from_summary(summary)
        self.apply_event(data)

    def snapshot(self) -> TurnActivitySummary:
        calls = list(self._calls.values())
        duration_ms = sum(float(call.get("duration_ms") or 0.0) for call in calls)
        if calls and duration_ms <= 0:
            duration_ms = (time.perf_counter() - self._started_at) * 1000
        return TurnActivitySummary(
            tool_count=len(calls),
            file_count=len(self._paths),
            change_count=sum(1 for call in calls if call.get("changed")),
            failed_count=sum(1 for call in calls if call.get("failed")),
            waiting_count=sum(1 for call in calls if call.get("waiting")),
            duration_ms=duration_ms,
            tool_names=tuple(call["tool_name"] for call in calls),
        )

    def consume(self) -> TurnActivitySummary:
        summary = self.snapshot()
        self.reset()
        return summary

    def _ensure_call(self, tool_id: str, tool_name: str) -> dict[str, Any]:
        if tool_id not in self._calls:
            self._calls[tool_id] = {
                "tool_name": tool_name,
                "failed": False,
                "waiting": False,
                "terminal": False,
                "changed": False,
                "duration_ms": 0.0,
            }
            self._order.append(tool_id)
        return self._calls[tool_id]

    def _match_or_create_tool_id(self, tool_name: str, event_type: str) -> str:
        if event_type != "tool_start":
            for tool_id in reversed(self._order):
                call = self._calls[tool_id]
                if call["tool_name"] == tool_name and not call.get("terminal"):
                    return tool_id
        self._sequence += 1
        return f"turn_tool_{self._sequence:04d}"

    def _collect_paths(self, data: dict[str, Any]) -> None:
        arguments = data.get("arguments") or {}
        metadata = data.get("metadata") or {}
        for source in (arguments, metadata, data):
            if not isinstance(source, dict):
                continue
            for key in PATH_ARGUMENTS:
                value = source.get(key)
                if isinstance(value, str) and value:
                    self._paths.add(value)


def _duration_from_summary(summary: str) -> float:
    marker = " in "
    suffix = "ms"
    if marker not in summary or suffix not in summary:
        return 0.0
    candidate = summary.rsplit(marker, 1)[-1].split(suffix, 1)[0].strip()
    try:
        return float(candidate)
    except ValueError:
        return 0.0
