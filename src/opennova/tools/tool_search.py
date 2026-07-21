"""Deferred tool discovery without exposing every schema on every model turn."""

from __future__ import annotations

import re
from typing import Any

from opennova.tools.base import BaseTool, ToolRegistry, ToolResult


class ToolSearchTool(BaseTool):
    """Search the runtime registry and enable matching deferred tools."""

    name = "tool_search"
    description = (
        "Search available deferred tools by capability. Use this when the visible core tools do "
        "not cover the task, such as Git, diagnostics, background tasks, MCP, or worktrees."
    )
    search_hint = "Discover additional tools without loading every schema into context"
    max_result_chars = 20_000

    def __init__(self, registry: ToolRegistry):
        super().__init__({})
        self.registry = registry

    def execute(  # type: ignore[override]
        self,
        query: str,
        max_results: int = 8,
    ) -> ToolResult:
        terms = {term for term in re.findall(r"[a-z0-9_]+", query.lower()) if len(term) > 1}
        ranked: list[tuple[int, BaseTool]] = []
        for name in self.registry.list_names():
            if name == self.name:
                continue
            tool = self.registry.get(name)
            haystack = " ".join(
                [
                    tool.name,
                    tool.description,
                    getattr(tool, "search_hint", ""),
                    " ".join(getattr(tool, "aliases", [])),
                ]
            ).lower()
            score = sum(4 if term in tool.name.lower() else 1 for term in terms if term in haystack)
            if not terms or score:
                ranked.append((score, tool))
        ranked.sort(key=lambda item: (-item[0], item[1].name))
        selected = [tool for _, tool in ranked[: max(1, min(max_results, 20))]]
        if not selected:
            return ToolResult(
                success=True,
                output="No deferred tools matched. Refine the capability query.",
                metadata={"discovered_tools": [], "query": query},
            )
        output = "Discovered deferred tools:\n" + "\n".join(
            f"- {tool.name}: {tool.describe()}" for tool in selected
        )
        return ToolResult(
            success=True,
            output=output,
            metadata={
                "discovered_tools": [tool.name for tool in selected],
                "query": query,
            },
        )

    def is_read_only(self, **kwargs: Any) -> bool:
        return True
