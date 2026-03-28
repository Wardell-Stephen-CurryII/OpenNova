"""
Diff Parser - Parse LLM output file changes.

Parses structured file change output from LLM responses
and converts them to FileChange objects for processing.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChangeType(str, Enum):
    """Type of file change."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


@dataclass
class FileChange:
    """
    Represents a single file change.

    Attributes:
        file_path: Path to the file
        change_type: Type of change (create/modify/delete)
        original_content: Original content (for modify/delete)
        new_content: New content (for create/modify)
        diff: Unified diff (for modify)
    """

    file_path: str
    change_type: ChangeType
    original_content: str | None = None
    new_content: str | None = None
    diff: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_diff(self) -> bool:
        """Check if this change has a diff."""
        return self.diff is not None and self.diff.strip() != ""

    def get_lines_changed(self) -> tuple[int, int]:
        """Get number of lines added and removed."""
        if not self.diff:
            return 0, 0

        added = 0
        removed = 0

        for line in self.diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        return added, removed


class DiffParser:
    """
    Parser for LLM output file changes.

    Supports multiple formats:
    - XML-style: <file_change>...</file_change>
    - JSON: {"file_path": "...", "changes": [...]}
    - Markdown code blocks with diff
    """

    XML_PATTERN = re.compile(
        r"<file_change>\s*"
        r"<path>(.*?)</path>\s*"
        r"<type>(.*?)</type>\s*"
        r"(?:<diff>(.*?)</diff>)?\s*"
        r"(?:<content>(.*?)</content>)?\s*"
        r"</file_change>",
        re.DOTALL,
    )

    MARKDOWN_DIFF_PATTERN = re.compile(
        r"```diff\s*\n(.*?)\n```",
        re.DOTALL,
    )

    FILE_PATH_PATTERN = re.compile(
        r"^(?:---|\+\+\+)\s+[ab]/(.+)$",
        re.MULTILINE,
    )

    def parse(self, llm_output: str) -> list[FileChange]:
        """
        Parse LLM output for file changes.

        Args:
            llm_output: Raw LLM output text

        Returns:
            List of FileChange objects
        """
        changes = []

        changes.extend(self._parse_xml_format(llm_output))

        if not changes:
            changes.extend(self._parse_markdown_format(llm_output))

        if not changes:
            changes.extend(self._parse_json_format(llm_output))

        return changes

    def _parse_xml_format(self, text: str) -> list[FileChange]:
        """Parse XML-style file changes."""
        changes = []

        for match in self.XML_PATTERN.finditer(text):
            path = match.group(1).strip()
            change_type_str = match.group(2).strip().lower()
            diff = match.group(3)
            content = match.group(4)

            try:
                change_type = ChangeType(change_type_str)
            except ValueError:
                change_type = ChangeType.MODIFY

            file_change = FileChange(
                file_path=path,
                change_type=change_type,
                diff=diff.strip() if diff else None,
                new_content=content.strip() if content else None,
            )

            changes.append(file_change)

        return changes

    def _parse_markdown_format(self, text: str) -> list[FileChange]:
        """Parse markdown code block diffs."""
        changes = []

        for match in self.MARKDOWN_DIFF_PATTERN.finditer(text):
            diff_text = match.group(1)

            file_path = self._extract_file_path(diff_text)
            if not file_path:
                continue

            file_change = FileChange(
                file_path=file_path,
                change_type=ChangeType.MODIFY,
                diff=diff_text,
            )

            changes.append(file_change)

        return changes

    def _parse_json_format(self, text: str) -> list[FileChange]:
        """Parse JSON format file changes."""
        changes = []

        json_pattern = re.compile(r"\{[^{}]*" r'"file_path"[^{}]*\}",?\s*', re.DOTALL)

        for match in json_pattern.finditer(text):
            try:
                json_str = match.group(0).rstrip(",")
                data = json.loads(json_str)

                file_path = data.get("file_path", "")
                change_type_str = data.get("type", "modify").lower()
                diff = data.get("diff")
                content = data.get("content")

                try:
                    change_type = ChangeType(change_type_str)
                except ValueError:
                    change_type = ChangeType.MODIFY

                file_change = FileChange(
                    file_path=file_path,
                    change_type=change_type,
                    diff=diff,
                    new_content=content,
                )

                changes.append(file_change)

            except json.JSONDecodeError:
                continue

        return changes

    def _extract_file_path(self, diff_text: str) -> str | None:
        """Extract file path from diff header."""
        match = self.FILE_PATH_PATTERN.search(diff_text)
        if match:
            return match.group(1)
        return None

    def parse_single_change(self, text: str) -> FileChange | None:
        """
        Parse a single file change from text.

        Args:
            text: Text to parse

        Returns:
            FileChange or None if not found
        """
        changes = self.parse(text)
        return changes[0] if changes else None

    @staticmethod
    def build_xml_change(
        file_path: str,
        change_type: ChangeType,
        diff: str | None = None,
        content: str | None = None,
    ) -> str:
        """
        Build an XML-style file change string.

        Args:
            file_path: File path
            change_type: Type of change
            diff: Optional diff text
            content: Optional new content

        Returns:
            XML string
        """
        xml = f"""<file_change>
<path>{file_path}</path>
<type>{change_type.value}</type>
"""
        if diff:
            xml += f"<diff>\n{diff}\n</diff>\n"
        if content:
            xml += f"<content>\n{content}\n</content>\n"

        xml += "</file_change>"
        return xml


def parse_llm_file_changes(llm_output: str) -> list[FileChange]:
    """
    Convenience function to parse LLM output for file changes.

    Args:
        llm_output: Raw LLM output

    Returns:
        List of FileChange objects
    """
    parser = DiffParser()
    return parser.parse(llm_output)
