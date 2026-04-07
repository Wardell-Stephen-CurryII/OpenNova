"""Memory Extractor - Extracts memory from conversations."""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from opennova.memory.types.user_memory import UserMemory
from opennova.memory.types.feedback_memory import FeedbackMemory, FeedbackType
from opennova.memory.types.project_memory import ProjectMemory
from opennova.memory.types.reference_memory import ReferenceMemory
from opennova.providers.base import Message


@dataclass
class ExtractionResult:
    """Result of memory extraction."""

    user_memories: list[UserMemory] = field(default_factory=list)
    feedback_memories: list[FeedbackMemory] = field(default_factory=list)
    project_memories: list[ProjectMemory] = field(default_factory=list)
    reference_memories: list[ReferenceMemory] = field(default_factory=list)


class MemoryExtractor:
    """
    Extracts relevant information from conversations for memory.

    Analyzes messages to identify:
    - User preferences and information
    - Feedback patterns and behavioral data
    - Project decisions and context
    - External resource references
    """

    # Patterns for memory extraction
    PREFERENCE_PATTERNS = [
        r"(?:i|I) (?:prefer|like|want|choose|should use) (?:\w+\s+)?(.+?)(?:\.|,|;|$)",
        r"(?:i|I) (?:don't|do not|avoid|hate|prefer not) (?:\w+\s+)?(.+?)(?:\.|,|;|$)",
        r"(?:my|our|the project) (?:\w+\s+)?(?:uses|uses|convention|style|pattern|architecture|framework)",
    ]

    REFERENCE_PATTERNS = [
        r"(?:https?://|http://|www\.)[^\s]+",
        r"(?:github|gitlab|bitbucket|stack\s+overflow)\.(?:com|io|org)/(?:issues|pr|pull|commit|repo)",
        r"(?:documentation|docs?|reference)\.?(?:\w+|\s+)(?:at|in|for)",
    ]

    PROJECT_PATTERNS = [
        r"(?:i|I) (?:decided|chose|chose|went with) (?:\w+\s+)?(.+?)(?:for|to use|because)",
        r"(?:i|I) (?:think|thought|considered|evaluated) (?:\w+\s+)?(.+?)(?:might|should|would be)",
        r"(?:the project|this repo) (?:\w+\s+)?(?:needs|should|requires) (?:\w+\s+)?(.+?)",
    ]

    def extract_from_messages(self, messages: list[Message]) -> ExtractionResult:
        """
        Extract memories from conversation messages.

        Args:
            messages: List of conversation messages

        Returns:
            ExtractionResult with all extracted memories
        """
        result = ExtractionResult()

        for message in messages:
            if message.role != "user":
                continue

            content = message.content or ""
            extracted = self._extract_from_user_message(content, message.timestamp)

            result.user_memories.extend(extracted["user"])
            result.feedback_memories.extend(extracted["feedback"])
            result.project_memories.extend(extracted["project"])
            result.reference_memories.extend(extracted["reference"])

        return result

    def _extract_from_user_message(
        self, content: str, timestamp: datetime
    ) -> dict[str, list[UserMemory]]:
        """Extract memories from a user message.

        Args:
            content: User message content
            timestamp: When the message was sent

        Returns:
            Dict with categorized memories
        """
        extracted: dict[str, list[UserMemory]] = {
            "user": [],
            "feedback": [],
            "project": [],
        }

        # Extract preferences
        for pattern in self.PREFERENCE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                preference = match.group(1).strip()
                if preference and len(preference) > 2:  # Filter out very short matches
                    extracted["user"].append(
                        UserMemory(
                            id=str(uuid.uuid4()),
                            content=preference,
                            created_at=timestamp,
                            tags=["preference"],
                        )
                    )

        # Extract references
        for pattern in self.REFERENCE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                url_or_reference = match.group(0)
                extracted["reference"].append(
                        ReferenceMemory(
                            id=str(uuid.uuid4()),
                            content=f"Referenced: {url_or_reference}",
                            created_at=timestamp,
                            tags=["reference"],
                            url=url_or_reference if url_or_reference.startswith("http") else None,
                        )
                    )

        # Extract project decisions
        for pattern in self.PROJECT_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                decision = match.group(1).strip()
                if len(decision) > 5:  # Filter out very short matches
                    extracted["project"].append(
                        ProjectMemory(
                            id=str(uuid.uuid4()),
                            content=f"Decision: {decision}",
                            created_at=timestamp,
                            tags=["decision"],
                        )
                    )

        # Extract feedback (positive/negative)
        feedback_indicators = {
            "positive": ["good", "great", "excellent", "perfect", "thanks", "helpful"],
            "negative": ["bad", "wrong", "error", "issue", "problem", "doesn't work"],
        }

        words = content.lower().split()
        for word in words:
            if word in feedback_indicators["positive"]:
                extracted["feedback"].append(
                        FeedbackMemory(
                            id=str(uuid.uuid4()),
                            content=f"Positive feedback: {word}",
                            feedback_type=FeedbackType.APPROVAL,
                            created_at=timestamp,
                            tags=["feedback", "positive"],
                        )
                    )
            elif word in feedback_indicators["negative"]:
                extracted["feedback"].append(
                        FeedbackMemory(
                            id=str(uuid.uuid4()),
                            content=f"Negative feedback: {word}",
                            feedback_type=FeedbackType.NEATIVE,
                            created_at=timestamp,
                            tags=["feedback", "negative"],
                        )
                    )

        return extracted

    def extract_preferences(
        self, content: str, context: str = ""
    ) -> list[str]:
        """
        Extract user preferences from content.

        Args:
            content: Text to analyze
            context: Additional context

        Returns:
            List of extracted preferences
        """
        preferences = []
        for pattern in self.PREFERENCE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                preference = match.group(1).strip()
                if preference and len(preference) > 2:
                    preferences.append(preference)
        return preferences

    def extract_references(
        self, content: str
    ) -> list[str]:
        """
        Extract resource references from content.

        Args:
            content: Text to analyze

        Returns:
            List of extracted references
        """
        references = []
        for pattern in self.REFERENCE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                references.append(match.group(0))
        return references

    def extract_project_context(
        self, content: str
    ) -> dict[str, Any]:
        """
        Extract project decisions and context.

        Args:
            content: Text to analyze

        Returns:
            Dict with project context
        """
        context = {
            "decisions": [],
            "requirements": [],
            "architecture": [],
        }

        for pattern in self.PROJECT_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                context["decisions"].append(match.group(1).strip())

        return context
