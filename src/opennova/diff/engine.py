"""
Diff Engine - Generate and apply unified diffs.

Provides safe code modification through:
- Unified diff generation
- Patch validation
- Patch preview with syntax highlighting
- Backup mechanism for rollback
"""

import difflib
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ApplyResult:
    """Result of applying a patch."""

    success: bool
    message: str
    file_path: str | None = None
    backup_path: str | None = None
    lines_added: int = 0
    lines_removed: int = 0
    error: str | None = None


@dataclass
class Hunk:
    """A single hunk in a diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)

    def to_string(self) -> str:
        """Convert hunk to string."""
        header = f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@"
        return "\n".join([header] + self.lines)


class DiffEngine:
    """
    Engine for generating and applying unified diffs.

    Features:
    - Generate unified diff between two texts
    - Apply patches to files
    - Validate patches before applying
    - Automatic backup before modification
    - Preview with color highlighting
    """

    def __init__(self, backup_dir: str = ".opennova/backups"):
        """
        Initialize diff engine.

        Args:
            backup_dir: Directory to store backups
        """
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def generate_diff(
        self,
        original: str,
        modified: str,
        file_path: str = "file",
        context_lines: int = 3,
    ) -> str:
        """
        Generate a unified diff between two texts.

        Args:
            original: Original content
            modified: Modified content
            file_path: File path for diff header
            context_lines: Number of context lines

        Returns:
            Unified diff string
        """
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=context_lines,
        )

        return "".join(diff)

    def parse_diff(self, diff_text: str) -> list[Hunk]:
        """
        Parse a unified diff into hunks.

        Args:
            diff_text: Unified diff text

        Returns:
            List of Hunk objects
        """
        hunks = []
        current_hunk = None
        hunk_pattern = re.compile(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
        )

        for line in diff_text.splitlines():
            match = hunk_pattern.match(line)
            if match:
                if current_hunk:
                    hunks.append(current_hunk)

                old_start = int(match.group(1))
                old_count = int(match.group(2) or "1")
                new_start = int(match.group(3))
                new_count = int(match.group(4) or "1")

                current_hunk = Hunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                )
            elif current_hunk and (line.startswith("+") or line.startswith("-") or line.startswith(" ") or line.startswith("\\ ")):
                current_hunk.lines.append(line)

        if current_hunk:
            hunks.append(current_hunk)

        return hunks

    def validate_patch(self, diff_text: str) -> tuple[bool, str]:
        """
        Validate a patch for correctness.

        Args:
            diff_text: Unified diff text

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not diff_text.strip():
            return False, "Empty diff"

        if not diff_text.startswith("---"):
            return False, "Invalid diff format: missing --- header"

        if not re.search(r"\+\+\+ b/", diff_text):
            return False, "Invalid diff format: missing +++ header"

        hunks = self.parse_diff(diff_text)

        if not hunks:
            return False, "No hunks found in diff"

        for i, hunk in enumerate(hunks):
            if hunk.old_start < 1:
                return False, f"Hunk {i + 1}: invalid old_start"

            if hunk.new_start < 1:
                return False, f"Hunk {i + 1}: invalid new_start"

        return True, ""

    def apply_patch(
        self,
        file_path: str,
        diff_text: str,
        backup: bool = True,
    ) -> ApplyResult:
        """
        Apply a unified diff patch to a file.

        Args:
            file_path: Path to the file to patch
            diff_text: Unified diff text
            backup: Whether to create a backup

        Returns:
            ApplyResult with success status and details
        """
        is_valid, error = self.validate_patch(diff_text)
        if not is_valid:
            return ApplyResult(success=False, message="Invalid patch", error=error)

        path = Path(file_path)

        if not path.exists():
            return ApplyResult(
                success=False,
                message=f"File not found: {file_path}",
                error="File not found",
            )

        try:
            original_content = path.read_text(encoding="utf-8")
        except Exception as e:
            return ApplyResult(
                success=False,
                message=f"Failed to read file: {e}",
                error=str(e),
            )

        backup_path = None
        if backup:
            backup_path = self._create_backup(file_path, original_content)

        try:
            patched_content = self._apply_diff(original_content, diff_text)

            path.write_text(patched_content, encoding="utf-8")

            lines_added, lines_removed = self._count_changes(diff_text)

            return ApplyResult(
                success=True,
                message=f"Patch applied successfully to {file_path}",
                file_path=str(path),
                backup_path=backup_path,
                lines_added=lines_added,
                lines_removed=lines_removed,
            )

        except Exception as e:
            if backup_path and Path(backup_path).exists():
                Path(path).write_text(original_content, encoding="utf-8")

            return ApplyResult(
                success=False,
                message=f"Failed to apply patch: {e}",
                error=str(e),
                backup_path=backup_path,
            )

    def _apply_diff(self, original: str, diff_text: str) -> str:
        """
        Apply a diff to original content.

        Args:
            original: Original file content
            diff_text: Unified diff text

        Returns:
            Patched content
        """
        original_lines = original.splitlines()
        hunks = self.parse_diff(diff_text)

        result_lines = original_lines.copy()
        offset = 0

        for hunk in hunks:
            start_idx = hunk.old_start - 1 + offset
            end_idx = start_idx + hunk.old_count

            for i in range(min(hunk.old_count, len(result_lines) - start_idx)):
                if i + start_idx < len(result_lines):
                    expected = hunk.lines[i] if i < len(hunk.lines) else None
                    if expected and expected.startswith("-"):
                        actual = result_lines[start_idx + i]
                        if expected[1:].strip() != actual.strip():
                            pass

            new_lines = []
            old_lines_to_remove = 0

            for line in hunk.lines:
                if line.startswith("-"):
                    old_lines_to_remove += 1
                elif line.startswith("+"):
                    new_lines.append(line[1:])
                elif line.startswith(" "):
                    new_lines.append(line[1:])
                elif line.startswith("\\"):
                    pass

            result_lines = result_lines[:start_idx] + new_lines + result_lines[end_idx:]
            offset += len(new_lines) - old_lines_to_remove

        return "\n".join(result_lines) + ("\n" if original.endswith("\n") else "")

    def _create_backup(self, file_path: str, content: str) -> str:
        """
        Create a backup of the file.

        Args:
            file_path: Original file path
            content: File content to backup

        Returns:
            Path to backup file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{Path(file_path).stem}_{timestamp}.bak"
        backup_path = self.backup_dir / backup_name

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(content, encoding="utf-8")

        return str(backup_path)

    def _count_changes(self, diff_text: str) -> tuple[int, int]:
        """
        Count lines added and removed in a diff.

        Args:
            diff_text: Unified diff text

        Returns:
            Tuple of (lines_added, lines_removed)
        """
        added = 0
        removed = 0

        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        return added, removed

    def preview_diff(self, diff_text: str) -> str:
        """
        Generate a preview of the diff with ANSI colors.

        Args:
            diff_text: Unified diff text

        Returns:
            Colored diff preview
        """
        lines = []

        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                lines.append(f"\033[1;36m{line}\033[0m")
            elif line.startswith("@@"):
                lines.append(f"\033[1;34m{line}\033[0m")
            elif line.startswith("+"):
                lines.append(f"\033[32m{line}\033[0m")
            elif line.startswith("-"):
                lines.append(f"\033[31m{line}\033[0m")
            else:
                lines.append(line)

        return "\n".join(lines)

    def reverse_diff(self, diff_text: str) -> str:
        """
        Reverse a diff (for undoing changes).

        Args:
            diff_text: Original unified diff

        Returns:
            Reversed diff
        """
        lines = diff_text.splitlines()
        reversed_lines = []

        for line in lines:
            if line.startswith("---"):
                reversed_lines.append(line.replace("--- ", "+++ ", 1))
            elif line.startswith("+++"):
                reversed_lines.append(line.replace("+++ ", "--- ", 1))
            elif line.startswith("-") and not line.startswith("---"):
                reversed_lines.append("+" + line[1:])
            elif line.startswith("+") and not line.startswith("+++"):
                reversed_lines.append("-" + line[1:])
            else:
                reversed_lines.append(line)

        return "\n".join(reversed_lines)
