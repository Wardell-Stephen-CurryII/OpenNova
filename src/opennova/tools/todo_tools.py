"""TodoWrite-style task board tool."""

from __future__ import annotations

from typing import Any, Literal

from opennova.tools.base import BaseTool, ToolResult

TodoStatus = Literal["pending", "in_progress", "done", "cancelled"]


class TodoWriteTool(BaseTool):
    """Replace the current structured todo list for a multi-step task."""

    name = "todo_write"
    description = (
        "Create or replace a structured todo list for the current task. "
        "Each todo should include id, content, and status."
    )
    max_result_chars = 20_000

    _todos: list[dict[str, Any]] = []

    def execute(self, todos: list[dict[str, Any]]) -> ToolResult:
        normalized: list[dict[str, Any]] = []
        valid_statuses = {"pending", "in_progress", "done", "cancelled"}
        for index, todo in enumerate(todos, start=1):
            content = str(todo.get("content", "")).strip()
            if not content:
                return ToolResult(success=False, output="", error=f"Todo {index} is missing content")
            status = str(todo.get("status", "pending"))
            if status not in valid_statuses:
                return ToolResult(success=False, output="", error=f"Invalid todo status: {status}")
            normalized.append(
                {
                    "id": str(todo.get("id") or index),
                    "content": content,
                    "status": status,
                }
            )

        self.__class__._todos = normalized
        lines = [f"- [{item['status']}] {item['id']}: {item['content']}" for item in normalized]
        return ToolResult(
            success=True,
            output=f"Updated {len(normalized)} todo(s).\n" + "\n".join(lines),
            metadata={"todos": normalized},
        )

    def is_read_only(self, **kwargs: Any) -> bool:
        return False

    def requires_permission(self, **kwargs: Any) -> bool:
        return False

    @classmethod
    def current_todos(cls) -> list[dict[str, Any]]:
        return list(cls._todos)
