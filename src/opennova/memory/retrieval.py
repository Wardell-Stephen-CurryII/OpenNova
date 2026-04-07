"""Memory Retrieval - Search and rank memories."""

import re
from datetime import datetime
from typing import Any

from opennova.memory.storage import MemoryStorage
from opennova.memory.types.user_memory import UserMemory



class MemoryRetriever:
    """
    Retrieves and ranks memories based on relevance.

    Implements relevance scoring and matching algorithms.
    """

    def __init__(self, storage: MemoryStorage | None = None):
        """
        Initialize memory retriever.

        Args:
            storage: Memory storage instance
        """
        if storage is None:
            storage = MemoryStorage()
        self.storage = storage

    def calculate_relevance(self, query: str, memory: UserMemory) -> float:
        """
        Calculate relevance score for a memory.

        Args:
            query: Search query
            memory: Memory to score

        Returns:
            Relevance score (0-1)
        """
        query_lower = query.lower()
        content_lower = memory.content.lower()

        score = 0.0

        # Exact match in content
        if query_lower in content_lower:
            score += 0.5

        # Word overlap
        query_words = set(query_lower.split())
        content_words = set(content_lower.split())

        overlap = len(query_words & content_words)
        if query_words:
            score += (overlap / len(query_words)) * 0.3

        # Tag matching
        for tag in memory.tags:
            if query_lower in tag.lower():
                score += 0.2

        # Recency boost (memories within 7 days get boost)
        age_days = (datetime.now() - memory.created_at).days
        if age_days < 7:
            score += 0.1
        elif age_days < 30:
            score += 0.05

        return min(score, 1.0)

    def retrieve(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
        min_relevance: float = 0.3,
    ) -> list[UserMemory]:
        """
        Retrieve memories matching query.

        Args:
            query: Search query
            category: Optional category filter
            limit: Maximum results
            min_relevance: Minimum relevance threshold

        Returns:
            List of relevant memories
        """
        # Get memories (all or filtered by category)
        if category:
            memories = self.storage.list_by_category(category)
        else:
            memories = []
            for cat in ["user", "feedback", "project", "reference"]:
                memories.extend(self.storage.list_by_category(cat))

        # Calculate relevance for each memory
        scored_memories = []
        for memory in memories:
            relevance = self.calculate_relevance(query, memory)
            if relevance >= min_relevance:
                scored_memories.append((relevance, memory))

        # Sort by relevance (highest first)
        scored_memories.sort(key=lambda x: x[0], reverse=True)

        return [memory for _, memory in scored_memories[:limit]]

    def get_recent(self, days: int = 7, limit: int = 20) -> list[UserMemory]:
        """
        Get recent memories within time window.

        Args:
            days: Time window in days
            limit: Maximum memories to return

        Returns:
            List of recent memories
        """
        cutoff = datetime.now().timestamp() - (days * 86400)
        recent_memories = []

        for category in ["user", "feedback", "project", "reference"]:
            for memory in self.storage.list_by_category(category):
                if memory.created_at.timestamp() >= cutoff:
                    recent_memories.append(memory)

        # Sort by creation time (newest first)
        recent_memories.sort(key=lambda m: m.created_at.timestamp(), reverse=True)
        return recent_memories[:limit]

    def get_tagged(self, tags: list[str], limit: int = 10) -> list[UserMemory]:
        """
        Get memories with specific tags.

        Args:
            tags: Tags to search for
            limit: Maximum results

        Returns:
            List of memories with matching tags
        """
        tagged_memories = []
        tags_lower = [tag.lower() for tag in tags]

        for category in ["user", "feedback", "project", "reference"]:
            for memory in self.storage.list_by_category(category):
                memory_tags_lower = [tag.lower() for tag in memory.tags]
                # Check if any tag matches
                if any(tag in memory_tags_lower for tag in tags_lower):
                    tagged_memories.append(memory)

        return tagged_memories[:limit]
