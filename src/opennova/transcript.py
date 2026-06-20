"""Session transcript export helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


class TranscriptExporter:
    """Export session messages and tool events to Markdown."""

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)

    def export(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        tool_events: list[dict[str, Any]] | None = None,
    ) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{session_id}.md"
        lines = [
            f"# OpenNova Transcript: {session_id}",
            "",
            f"- Exported at: {datetime.now().isoformat(timespec='seconds')}",
            "",
            "## Messages",
            "",
        ]
        for message in messages:
            lines.append(f"### {message.get('role', 'message')}")
            lines.append("")
            lines.append(str(message.get("content", "")))
            lines.append("")
        lines.extend(["## Tool Events", ""])
        for event in tool_events or []:
            lines.append(
                f"- `{event.get('type', 'event')}` "
                f"{event.get('tool_name', '')} {event.get('tool_id', '')}".rstrip()
            )
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return path

    def export_runtime(self, runtime: Any, output_path: str | Path | None = None) -> Path:
        """Export transcript data from an AgentRuntime-like object."""
        session_id = getattr(getattr(runtime, "session_manager", None), "session_id", "session")
        context_manager = getattr(runtime, "context_manager", None)
        raw_messages = getattr(context_manager, "messages", []) if context_manager else []
        messages: list[dict[str, Any]] = []
        for message in raw_messages:
            if hasattr(message, "to_openai_format"):
                messages.append(message.to_openai_format())
            elif isinstance(message, dict):
                messages.append(message)
            else:
                messages.append(
                    {
                        "role": getattr(message, "role", "message"),
                        "content": getattr(message, "content", str(message)),
                    }
                )
        events = list(getattr(runtime, "tool_events", []))
        if output_path:
            self.output_dir = Path(output_path).parent
            session_id = Path(output_path).stem
        return self.export(session_id=session_id, messages=messages, tool_events=events)
