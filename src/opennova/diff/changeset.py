"""
ChangeSet - Track and manage multiple file changes.

Provides a unified interface for:
- Collecting multiple file changes
- Previewing all changes
- Applying changes with rollback support
- Serializing changes for persistence
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from opennova.diff.engine import ApplyResult, DiffEngine
from opennova.diff.parser import ChangeType, FileChange


@dataclass
class ChangeResult:
    """Result of applying a ChangeSet."""

    success: bool
    message: str
    applied_changes: list[ApplyResult] = field(default_factory=list)
    failed_changes: list[tuple[FileChange, str]] = field(default_factory=list)
    backup_dir: str | None = None

    @property
    def total_changes(self) -> int:
        """Total number of changes attempted."""
        return len(self.applied_changes) + len(self.failed_changes)

    @property
    def success_count(self) -> int:
        """Number of successfully applied changes."""
        return len(self.applied_changes)

    @property
    def failure_count(self) -> int:
        """Number of failed changes."""
        return len(self.failed_changes)


@dataclass
class ChangeSet:
    """
    A collection of file changes to be applied together.

    Features:
    - Track multiple file changes
    - Preview all changes
    - Apply atomically with rollback
    - Serialize for persistence
    """

    task: str
    changes: list[FileChange] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    _engine: DiffEngine | None = field(default=None, repr=False)

    def __post_init__(self):
        """Initialize the diff engine."""
        if self._engine is None:
            self._engine = DiffEngine()

    def add_change(self, change: FileChange) -> None:
        """
        Add a file change to the set.

        Args:
            change: FileChange to add
        """
        self.changes.append(change)

    def add_new_file(self, file_path: str, content: str) -> None:
        """
        Add a new file creation change.

        Args:
            file_path: Path for the new file
            content: Content for the new file
        """
        change = FileChange(
            file_path=file_path,
            change_type=ChangeType.CREATE,
            new_content=content,
        )
        self.add_change(change)

    def add_modification(
        self,
        file_path: str,
        original: str,
        new_content: str,
    ) -> None:
        """
        Add a file modification change.

        Args:
            file_path: Path to the file
            original: Original content
            new_content: New content
        """
        diff = self._engine.generate_diff(original, new_content, file_path)
        change = FileChange(
            file_path=file_path,
            change_type=ChangeType.MODIFY,
            original_content=original,
            new_content=new_content,
            diff=diff,
        )
        self.add_change(change)

    def add_deletion(self, file_path: str) -> None:
        """
        Add a file deletion change.

        Args:
            file_path: Path to delete
        """
        change = FileChange(
            file_path=file_path,
            change_type=ChangeType.DELETE,
        )
        self.add_change(change)

    def get_preview(self) -> str:
        """
        Get a preview of all changes.

        Returns:
            Formatted preview string
        """
        lines = [f"ChangeSet for: {self.task}", "=" * 50, ""]

        for i, change in enumerate(self.changes, 1):
            lines.append(f"[{i}] {change.change_type.value.upper()}: {change.file_path}")

            if change.change_type == ChangeType.CREATE:
                lines.append(f"    New file ({len(change.new_content or '')} bytes)")
            elif change.change_type == ChangeType.MODIFY:
                added, removed = change.get_lines_changed()
                lines.append(f"    +{added} lines, -{removed} lines")
            elif change.change_type == ChangeType.DELETE:
                lines.append("    File will be deleted")

            lines.append("")

        return "\n".join(lines)

    def get_diff_preview(self) -> str:
        """
        Get a colored diff preview of all changes.

        Returns:
            ANSI-colored diff string
        """
        previews = []

        for change in self.changes:
            if change.diff:
                preview = self._engine.preview_diff(change.diff)
                previews.append(f"--- {change.file_path} ---\n{preview}")

        return "\n\n".join(previews)

    def apply(self, backup: bool = True) -> ChangeResult:
        """
        Apply all changes.

        Args:
            backup: Whether to create backups

        Returns:
            ChangeResult with details
        """
        applied = []
        failed = []
        backup_dir = None

        for change in self.changes:
            result = self._apply_single_change(change, backup)

            if result.success:
                applied.append(result)
                if result.backup_path and not backup_dir:
                    backup_dir = str(Path(result.backup_path).parent)
            else:
                failed.append((change, result.error or result.message))

                if applied and backup:
                    self._rollback(applied)

                break

        return ChangeResult(
            success=len(failed) == 0,
            message=f"Applied {len(applied)}/{len(self.changes)} changes",
            applied_changes=applied,
            failed_changes=failed,
            backup_dir=backup_dir,
        )

    def _apply_single_change(
        self,
        change: FileChange,
        backup: bool,
    ) -> ApplyResult:
        """Apply a single file change."""
        path = Path(change.file_path)

        if change.change_type == ChangeType.CREATE:
            return self._apply_create(path, change.new_content or "", backup)
        elif change.change_type == ChangeType.MODIFY:
            return self._apply_modify(path, change.diff or "", backup)
        elif change.change_type == ChangeType.DELETE:
            return self._apply_delete(path, backup)

        return ApplyResult(success=False, message="Unknown change type")

    def _apply_create(
        self,
        path: Path,
        content: str,
        backup: bool,
    ) -> ApplyResult:
        """Create a new file."""
        try:
            if path.exists():
                return ApplyResult(
                    success=False,
                    message=f"File already exists: {path}",
                    error="File exists",
                )

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

            return ApplyResult(
                success=True,
                message=f"Created file: {path}",
                file_path=str(path),
            )

        except Exception as e:
            return ApplyResult(
                success=False,
                message=f"Failed to create file: {e}",
                error=str(e),
            )

    def _apply_modify(
        self,
        path: Path,
        diff: str,
        backup: bool,
    ) -> ApplyResult:
        """Modify an existing file with a diff."""
        return self._engine.apply_patch(str(path), diff, backup)

    def _apply_delete(self, path: Path, backup: bool) -> ApplyResult:
        """Delete a file."""
        try:
            if not path.exists():
                return ApplyResult(
                    success=False,
                    message=f"File not found: {path}",
                    error="File not found",
                )

            backup_path = None
            if backup:
                content = path.read_text(encoding="utf-8")
                backup_path = self._engine._create_backup(str(path), content)

            path.unlink()

            return ApplyResult(
                success=True,
                message=f"Deleted file: {path}",
                file_path=str(path),
                backup_path=backup_path,
            )

        except Exception as e:
            return ApplyResult(
                success=False,
                message=f"Failed to delete file: {e}",
                error=str(e),
            )

    def _rollback(self, applied: list[ApplyResult]) -> None:
        """Rollback applied changes."""
        for result in reversed(applied):
            if result.backup_path:
                backup = Path(result.backup_path)
                if backup.exists():
                    content = backup.read_text(encoding="utf-8")
                    Path(result.file_path).write_text(content, encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "task": self.task,
            "created_at": self.created_at.isoformat(),
            "changes": [
                {
                    "file_path": c.file_path,
                    "change_type": c.change_type.value,
                    "diff": c.diff,
                    "new_content": c.new_content,
                }
                for c in self.changes
            ],
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChangeSet":
        """Create ChangeSet from dictionary."""
        changes = []
        for c in data.get("changes", []):
            change = FileChange(
                file_path=c["file_path"],
                change_type=ChangeType(c["change_type"]),
                diff=c.get("diff"),
                new_content=c.get("new_content"),
            )
            changes.append(change)

        return cls(
            task=data["task"],
            changes=changes,
            created_at=datetime.fromisoformat(data["created_at"]),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ChangeSet":
        """Create ChangeSet from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self):
        return iter(self.changes)

    def __repr__(self) -> str:
        return f"ChangeSet(task={self.task!r}, changes={len(self.changes)})"
