"""Memory Storage - Persistent storage for memories."""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Any

from opennova.memory.types.user_memory import UserMemory
from opennova.memory.types.feedback_memory import FeedbackMemory
from opennova.memory.types.project_memory import ProjectMemory
from opennova.memory.types.reference_memory import ReferenceMemory


class MemoryStorage:
    """
    Persistent storage for memory entries.

    Stores memories in the .claude/memory/ directory.
    """

    def __init__(self, memory_dir: str | None = None):
        """
        Initialize memory storage.

        Args:
            memory_dir: Custom memory directory path
        """
        if memory_dir is None:
            # Use XDG_DATA_HOME or fallback
            data_home = os.environ.get("XDG_DATA_HOME")
            if data_home:
                base = Path(data_home) / "opennova"
            else:
                base = Path.home() / ".local" / "share" / "opennova"

            self.memory_dir = base / "memory"
        else:
            self.memory_dir = Path(memory_dir)

        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories for each memory type
        self.user_dir = self.memory_dir / "user"
        self.feedback_dir = self.memory_dir / "feedback"
        self.project_dir = self.memory_dir / "project"
        self.reference_dir = self.memory_dir / "reference"

        for dir_path in [self.user_dir, self.feedback_dir, self.project_dir, self.reference_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

    def _get_category_dir(self, category: str) -> Path:
        """Get directory for a memory category."""
        mapping = {
            MemoryCategory.USER: self.user_dir,
            MemoryCategory.FEEDBACK: self.feedback_dir,
            MemoryCategory.PROJECT: self.project_dir,
            MemoryCategory.REFERENCE: self.reference_dir,
        }
        return mapping.get(category, self.memory_dir)

    def _get_memory_file(self, memory: UserMemory) -> Path:
        """Get file path for a memory entry."""
        category_dir = self._get_category_dir(memory.category)
        return category_dir / f"{memory.id}.json"

    def save(self, memory: UserMemory) -> None:
        """
        Save a memory entry to storage.

        Args:
            memory: Memory entry to save
        """
        category = memory.category
        memory.updated_at = datetime.now()
        memory_file = self._get_memory_file(memory)
        """
        Save a memory entry to storage.

        Args:
            memory: Memory entry to save
        """
        memory.updated_at = datetime.now()
        memory_file = self._get_memory_file(memory)

        memory_data = memory.to_dict()

        with open(memory_file, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=2, ensure_ascii=False)

    def get(self, memory_id: str, category: str) -> UserMemory | None:
        """
        Retrieve a memory entry by ID.

        Args:
            memory_id: Memory ID
            category: Memory category

        Returns:
            Memory entry or None if not found
        """
        category_dir = self._get_category_dir(category)
        memory_file = category_dir / f"{memory_id}.json"

        if not memory_file.exists():
            return None

        try:
            with open(memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return UserMemory.from_dict(data)
        except Exception:
            return None

    def list_by_category(self, category: str) -> list[UserMemory]:
        """
        List all memories in a category.

        Args:
            category: Memory category

        Returns:
            List of memory entries
        """
        category_dir = self._get_category_dir(category)
        memories = []

        for memory_file in category_dir.glob("*.json"):
            try:
                with open(memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if category == MemoryCategory.USER:
                        memories.append(UserMemory.from_dict(data))
                    elif category == MemoryCategory.FEEDBACK:
                        memories.append(FeedbackMemory.from_dict(data))
                    elif category == MemoryCategory.PROJECT:
                        memories.append(ProjectMemory.from_dict(data))
                    elif category == MemoryCategory.REFERENCE:
                        memories.append(ReferenceMemory.from_dict(data))
            except Exception:
                pass

        # Sort by creation time (newest first) and relevance
        memories.sort(key=lambda m: (m.created_at.timestamp(), m.relevance), reverse=True)
        return memories

    def search(self, query: str, category: str | None = None, limit: int = 10) -> list[UserMemory]:
        """
        Search memories by query string.

        Args:
            query: Search query
            category: Optional category filter
            limit: Maximum results to return

        Returns:
            List of matching memory entries
        """
        query_lower = query.lower()

        if category:
            memories = self.list_by_category(category)
        else:
            # Search all categories
            memories = []
            for cat in [MemoryCategory.USER, MemoryCategory.FEEDBACK, MemoryCategory.PROJECT, MemoryCategory.REFERENCE]:
                memories.extend(self.list_by_category(cat))

        # Filter by query
        matches = []
        for memory in memories:
            # Check content
            if query_lower in memory.content.lower():
                matches.append(memory)
            # Check tags
            elif any(query_lower in tag.lower() for tag in memory.tags):
                matches.append(memory)

        return matches[:limit] if limit else matches

    def delete(self, memory_id: str, category: str) -> bool:
        """
        Delete a memory entry.

        Args:
            memory_id: Memory ID
            category: Memory category

        Returns:
            True if deleted
        """
        category_dir = self._get_category_dir(category)
        memory_file = category_dir / f"{memory_id}.json"

        if memory_file.exists():
            memory_file.unlink()
            return True
        return False

    def cleanup_old_memories(self, days: int = 30) -> int:
        """
        Delete memories older than specified days.

        Args:
            days: Number of days to keep

        Returns:
            Number of memories deleted
        """
        cutoff = datetime.now().timestamp() - (days * 86400)
        deleted = 0

        for category in ["user", "feedback", "project", "reference"]:
            category_dir = self._get_category_dir(category)
            for memory_file in category_dir.glob("*.json"):
                try:
                    with open(memory_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    created_at = datetime.fromisoformat(data["created_at"]).timestamp()

                    if created_at < cutoff:
                        memory_file.unlink()
                        deleted += 1
                except Exception:
                    pass

        return deleted
