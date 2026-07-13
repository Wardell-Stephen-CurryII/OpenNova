"""Base memory types shared by persistent memory entries."""

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Literal, TypeVar

MemoryCategory = Literal["user", "feedback", "project", "reference"]
MEMORY_CATEGORIES: tuple[MemoryCategory, ...] = (
    "user",
    "feedback",
    "project",
    "reference",
)

MemoryType = TypeVar("MemoryType", bound="BaseMemory")


@dataclass
class BaseMemory:
    """Base class for all memory entries."""

    id: str
    category: str = "user"
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

    @classmethod
    def from_dict(cls: type[MemoryType], data: dict[str, Any]) -> MemoryType:
        """Restore a memory entry from its JSON representation."""
        payload = dict(data)
        for field_name in ("created_at", "updated_at"):
            value = payload.get(field_name)
            if isinstance(value, str):
                payload[field_name] = datetime.fromisoformat(value)

        known_fields = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in known_fields})
