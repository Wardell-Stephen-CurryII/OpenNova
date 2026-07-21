"""Slash command helpers for managed layered memory."""

from __future__ import annotations

from pathlib import Path

from opennova.memory.layered import LayeredMemoryManager
from opennova.tools.base import ToolResult


def handle_memory_command(project_path: str | Path, args: str) -> ToolResult:
    """Handle `/memory list|add|delete` without depending on Textual."""
    manager = LayeredMemoryManager(project_path)
    command, _, remainder = args.strip().partition(" ")
    command = command or "list"
    try:
        if command == "list":
            records = manager.list_records()
            if not records:
                return ToolResult(True, "No layered memories found.")
            lines = []
            for record in records:
                status = "expired" if record.expired else "active"
                lines.append(
                    f"- {record.name}: {status}, scope={record.scope}, "
                    f"provenance={record.provenance}"
                )
            return ToolResult(True, "Layered memories:\n" + "\n".join(lines))
        if command == "add":
            name, separator, content = remainder.strip().partition(" ")
            if not separator or not content.strip():
                return ToolResult(False, "", "Usage: /memory add <name> <content>")
            record = manager.add(name, content)
            return ToolResult(True, f"Created memory: {record.name}")
        if command == "delete":
            name = remainder.strip()
            if not name:
                return ToolResult(False, "", "Usage: /memory delete <name>")
            deleted = manager.delete(name)
            if not deleted:
                return ToolResult(False, "", f"Memory not found: {name}")
            return ToolResult(True, f"Deleted memory: {name}")
        return ToolResult(False, "", "Usage: /memory [list|add <name> <content>|delete <name>]")
    except (OSError, ValueError) as exc:
        return ToolResult(False, "", str(exc))
