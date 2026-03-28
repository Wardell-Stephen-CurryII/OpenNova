"""
Project Memory - Long-term persistent memory.

Manages memory that persists across sessions:
- Project structure and metadata
- Key decisions and patterns
- User preferences
- Historical context
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

MEMORY_DIR = ".opennova"
MEMORY_FILE = "memory.json"


@dataclass
class ProjectStructure:
    """Project file structure summary."""

    root_path: str
    total_files: int = 0
    total_dirs: int = 0
    file_types: dict[str, int] = field(default_factory=dict)
    last_scanned: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "root_path": self.root_path,
            "total_files": self.total_files,
            "total_dirs": self.total_dirs,
            "file_types": self.file_types,
            "last_scanned": self.last_scanned.isoformat() if self.last_scanned else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectStructure":
        """Create from dictionary."""
        return cls(
            root_path=data.get("root_path", ""),
            total_files=data.get("total_files", 0),
            total_dirs=data.get("total_dirs", 0),
            file_types=data.get("file_types", {}),
            last_scanned=(
                datetime.fromisoformat(data["last_scanned"])
                if data.get("last_scanned")
                else None
            ),
        )


@dataclass
class DecisionRecord:
    """Record of a key decision."""

    id: str
    description: str
    reasoning: str
    timestamp: datetime = field(default_factory=datetime.now)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "description": self.description,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionRecord":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            description=data["description"],
            reasoning=data["reasoning"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            context=data.get("context", {}),
        )


@dataclass
class UserPreference:
    """User preference setting."""

    key: str
    value: Any
    category: str = "general"
    last_used: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "last_used": self.last_used.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserPreference":
        """Create from dictionary."""
        return cls(
            key=data["key"],
            value=data["value"],
            category=data.get("category", "general"),
            last_used=(
                datetime.fromisoformat(data["last_used"])
                if data.get("last_used")
                else datetime.now()
            ),
        )


class ProjectMemory:
    """
    Long-term memory stored in project directory.

    Features:
    - Persist across sessions
    - Track project structure
    - Store key decisions
    - Cache user preferences
    """

    def __init__(self, project_path: str = "."):
        """
        Initialize project memory.

        Args:
            project_path: Root path of the project
        """
        self.project_path = Path(project_path).resolve()
        self.memory_path = self.project_path / MEMORY_DIR / MEMORY_FILE

        self.structure = ProjectStructure(root_path=str(self.project_path))
        self.decisions: list[DecisionRecord] = []
        self.preferences: dict[str, UserPreference] = {}
        self.session_history: list[dict[str, Any]] = []
        self.metadata: dict[str, Any] = {}

        self._load()

    def _load(self) -> None:
        """Load memory from disk."""
        if self.memory_path.exists():
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.structure = ProjectStructure.from_dict(
                    data.get("structure", {"root_path": str(self.project_path)})
                )

                self.decisions = [
                    DecisionRecord.from_dict(d) for d in data.get("decisions", [])
                ]

                self.preferences = {
                    k: UserPreference.from_dict(v)
                    for k, v in data.get("preferences", {}).items()
                }

                self.session_history = data.get("sessions", [])
                self.metadata = data.get("metadata", {})

            except Exception:
                pass

    def save(self) -> None:
        """Save memory to disk."""
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "structure": self.structure.to_dict(),
            "decisions": [d.to_dict() for d in self.decisions],
            "preferences": {k: v.to_dict() for k, v in self.preferences.items()},
            "sessions": self.session_history[-20:],
            "metadata": self.metadata,
            "last_updated": datetime.now().isoformat(),
        }

        with open(self.memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def scan_project(self) -> None:
        """Scan and record project structure."""
        file_types: dict[str, int] = {}
        total_files = 0
        total_dirs = 0

        ignore_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".eggs", "*.egg-info", "dist", "build"}

        for path in self.project_path.rglob("*"):
            if any(part in ignore_dirs for part in path.parts):
                continue

            if path.is_file():
                total_files += 1
                ext = path.suffix.lower() or "no_extension"
                file_types[ext] = file_types.get(ext, 0) + 1
            elif path.is_dir():
                total_dirs += 1

        self.structure = ProjectStructure(
            root_path=str(self.project_path),
            total_files=total_files,
            total_dirs=total_dirs,
            file_types=file_types,
            last_scanned=datetime.now(),
        )

        self.save()

    def add_decision(
        self,
        description: str,
        reasoning: str,
        context: dict[str, Any] | None = None,
    ) -> DecisionRecord:
        """
        Record a key decision.

        Args:
            description: What decision was made
            reasoning: Why it was made
            context: Additional context

        Returns:
            The created DecisionRecord
        """
        decision_id = f"decision_{len(self.decisions) + 1}"

        decision = DecisionRecord(
            id=decision_id,
            description=description,
            reasoning=reasoning,
            context=context or {},
        )

        self.decisions.append(decision)

        if len(self.decisions) > 50:
            self.decisions = self.decisions[-50:]

        self.save()

        return decision

    def get_relevant_decisions(self, topic: str, limit: int = 5) -> list[DecisionRecord]:
        """
        Get decisions relevant to a topic.

        Args:
            topic: Topic to search for
            limit: Maximum number of results

        Returns:
            List of relevant decisions
        """
        topic_lower = topic.lower()
        relevant = []

        for decision in reversed(self.decisions):
            if (
                topic_lower in decision.description.lower()
                or topic_lower in decision.reasoning.lower()
            ):
                relevant.append(decision)

            if len(relevant) >= limit:
                break

        return relevant

    def set_preference(self, key: str, value: Any, category: str = "general") -> None:
        """
        Set a user preference.

        Args:
            key: Preference key
            value: Preference value
            category: Preference category
        """
        self.preferences[key] = UserPreference(
            key=key,
            value=value,
            category=category,
        )
        self.save()

    def get_preference(self, key: str, default: Any = None) -> Any:
        """
        Get a user preference.

        Args:
            key: Preference key
            default: Default value if not found

        Returns:
            Preference value or default
        """
        if key in self.preferences:
            pref = self.preferences[key]
            pref.last_used = datetime.now()
            return pref.value
        return default

    def record_session(
        self,
        task: str,
        success: bool,
        duration_seconds: float,
    ) -> None:
        """
        Record a session summary.

        Args:
            task: Task description
            success: Whether task completed successfully
            duration_seconds: Duration of the session
        """
        session = {
            "task": task[:200],
            "success": success,
            "duration_seconds": duration_seconds,
            "timestamp": datetime.now().isoformat(),
        }

        self.session_history.append(session)

        if len(self.session_history) > 20:
            self.session_history = self.session_history[-20:]

        self.save()

    def get_project_context(self) -> str:
        """
        Get a text summary of project context.

        Returns:
            Project context string
        """
        parts = [f"Project: {self.project_path.name}"]

        if self.structure.total_files > 0:
            parts.append(f"Files: {self.structure.total_files}")

            top_types = sorted(
                self.structure.file_types.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5]

            if top_types:
                type_strs = [f"{ext} ({count})" for ext, count in top_types]
                parts.append(f"Main types: {', '.join(type_strs)}")

        if self.decisions:
            parts.append(f"Key decisions: {len(self.decisions)}")

        return "\n".join(parts)

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of project memory."""
        return {
            "project_path": str(self.project_path),
            "total_files": self.structure.total_files,
            "total_dirs": self.structure.total_dirs,
            "file_types": self.structure.file_types,
            "decisions_count": len(self.decisions),
            "preferences_count": len(self.preferences),
            "sessions_count": len(self.session_history),
        }

    def clear(self) -> None:
        """Clear all project memory."""
        self.structure = ProjectStructure(root_path=str(self.project_path))
        self.decisions.clear()
        self.preferences.clear()
        self.session_history.clear()
        self.metadata.clear()
        self.save()

    def __repr__(self) -> str:
        return (
            f"ProjectMemory(path={self.project_path.name}, "
            f"files={self.structure.total_files}, "
            f"decisions={len(self.decisions)})"
        )
