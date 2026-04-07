"""Project Memory - Stores project-specific information."""

from dataclasses import dataclass, field
from typing import Any

from opennova.memory.types.user_memory import UserMemory


class ProjectMemory(UserMemory):
    """Project-specific memory entry."""

    category: str = "project"
    project_path: str | None = None
    decision: str | None = None
    reasoning: str | None = None
    context: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with project-specific fields."""
        data = super().to_dict()
        data.update({
            "project_path": self.project_path,
            "decision": self.decision,
            "reasoning": self.reasoning,
            "context": self.context,
        })
        return data
