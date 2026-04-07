"""Base Memory class - Common structure for all memory types."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class BaseMemory:
    """Base class for all memory entries."""

    id: str
    category: str = "user"  # Use string instead of enum to avoid import issues
    content: str = ""  # Provide default for Python 3.11
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime | None = None
    relevance: float = 1.0
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "category": self.category,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "relevance": self.relevance,
            "tags": self.tags,
            "metadata": self.metadata,
        }
