"""User Memory - Stores user-related information."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from opennova.memory.types.base import BaseMemory


class UserMemory(BaseMemory):
    """User-related memory entry."""

    category: str = "user"
    feedback_type: str | None = None
    action: str | None = None
    tool: str | None = None
