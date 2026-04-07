"""Feedback Memory - Stores user feedback and behavioral patterns."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from opennova.memory.types.user_memory import UserMemory


class FeedbackType(str, Enum):
    """Types of user feedback."""

    PREFERENCE = "preference"
    CORRECTION = "correction"
    APPROVAL = "approval"
    REJECTION = "rejection"
    ERROR_REPORT = "error_report"


@dataclass
class FeedbackMemory(UserMemory):
    """User feedback and behavioral pattern memory."""

    category: str = "feedback"
    feedback_type: str | None = None  # "preference", "correction", "approval", "rejection", "error_report"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with feedback-specific fields."""
        data = super().to_dict()
        data.update({
            "feedback_type": self.feedback_type,
            "action": self.action,
            "tool": self.tool,
        })
        return data
