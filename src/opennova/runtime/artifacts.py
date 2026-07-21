"""Durable offload for tool results that exceed model context budgets."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from opennova.tools.base import ToolResult


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to a complete tool output stored outside model context."""

    path: str
    chars: int


class ArtifactStore:
    """Store complete tool output under the project-local OpenNova directory."""

    def __init__(self, project_path: str | Path = ".", session_id: str = "session") -> None:
        safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id) or "session"
        self.root = Path(project_path).resolve() / ".opennova" / "artifacts" / safe_session

    def write(self, tool_id: str, content: str) -> ArtifactRef:
        self.root.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_id) or "tool"
        target = self.root / f"{safe_name}.txt"
        fd, temp_name = tempfile.mkstemp(prefix=f".{safe_name}-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return ArtifactRef(path=str(target), chars=len(content))


class ToolResultBudget:
    """Apply per-tool and aggregate model-visible output limits."""

    def __init__(self, artifact_store: ArtifactStore, per_turn_chars: int = 160_000) -> None:
        self.artifact_store = artifact_store
        self.per_turn_chars = max(1_000, int(per_turn_chars))

    def apply_one(self, result: ToolResult, tool_id: str, limit: int | None) -> ToolResult:
        output = result.output or ""
        effective_limit = max(500, int(limit or 100_000))
        if len(output) <= effective_limit:
            return result
        ref = self.artifact_store.write(tool_id, output)
        result.output = self._truncate(output, effective_limit, ref.path)
        result.metadata.update(
            {
                "artifact_path": ref.path,
                "artifact_chars": ref.chars,
                "result_truncated": True,
            }
        )
        return result

    def apply_turn(self, results: list[ToolResult], tool_ids: list[str]) -> list[ToolResult]:
        remaining = self.per_turn_chars
        for result, tool_id in zip(results, tool_ids, strict=True):
            output = result.output or ""
            if len(output) <= remaining:
                remaining -= len(output)
                continue
            ref = self.artifact_store.write(tool_id, output)
            visible = max(500, remaining)
            result.output = self._truncate(output, visible, ref.path)
            result.metadata.update(
                {
                    "artifact_path": ref.path,
                    "artifact_chars": ref.chars,
                    "turn_budget_truncated": True,
                }
            )
            remaining = 0
        return results

    @staticmethod
    def _truncate(content: str, limit: int, artifact_path: str) -> str:
        marker = f"\n\n[Full output: {artifact_path}]\n"
        available = max(100, limit - len(marker))
        head = available * 2 // 3
        tail = available - head
        omitted = max(0, len(content) - head - tail)
        return (
            content[:head]
            + f"\n\n... [{omitted} characters omitted] ...\n\n"
            + content[-tail:]
            + marker
        )
