"""Reference Memory - Stores external resource references."""

from dataclasses import dataclass, field
from typing import Any

from opennova.memory.types.user_memory import UserMemory


class ReferenceMemory(UserMemory):
    """External resource reference memory entry."""

    category: str = "reference"
    resource_type: str | None = None
    url: str | None = None
    title: str | None = None
    snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with reference-specific fields."""
        data = super().to_dict()
        data.update({
            "resource_type": self.resource_type,
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
        })
        return data
