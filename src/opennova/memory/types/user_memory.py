"""User Memory - Stores user-related information."""

from dataclasses import dataclass

from opennova.memory.types.base import BaseMemory


@dataclass
class UserMemory(BaseMemory):
    """User-related memory entry."""

    category: str = "user"
    feedback_type: str | None = None
    action: str | None = None
    tool: str | None = None
