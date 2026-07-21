"""Shared nested .gitignore semantics for repository traversal tools."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".opennova",
}


@dataclass(frozen=True)
class IgnorePattern:
    base: Path
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool


class GitIgnoreService:
    """Evaluate root and nested .gitignore files in Git-compatible order."""

    def __init__(self, root: str | Path, enabled: bool = True) -> None:
        self.root = Path(root).resolve()
        self.enabled = enabled
        self._cache: dict[Path, list[IgnorePattern]] = {}

    def is_ignored(self, path: str | Path, *, is_dir: bool | None = None) -> bool:
        target = Path(path).resolve()
        try:
            relative = target.relative_to(self.root)
        except ValueError:
            return True
        if any(part in DEFAULT_EXCLUDED_DIRS for part in relative.parts):
            return True
        if not self.enabled:
            return False

        ignored = False
        for ignore_file in self._ignore_files_for(target):
            for rule in self._patterns(ignore_file):
                if self._matches(rule, target, is_dir=is_dir):
                    ignored = not rule.negated
        return ignored

    def _ignore_files_for(self, target: Path) -> list[Path]:
        parent = target if target.is_dir() else target.parent
        try:
            relative_parent = parent.relative_to(self.root)
        except ValueError:
            return []
        directories = [self.root]
        current = self.root
        for part in relative_parent.parts:
            current = current / part
            directories.append(current)
        return [directory / ".gitignore" for directory in directories]

    def _patterns(self, ignore_file: Path) -> list[IgnorePattern]:
        if ignore_file in self._cache:
            return self._cache[ignore_file]
        patterns: list[IgnorePattern] = []
        if ignore_file.is_file():
            for raw in ignore_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if not raw:
                    continue
                escaped_marker = raw.startswith((r"\#", r"\!"))
                if raw.startswith("#") and not escaped_marker:
                    continue
                negated = raw.startswith("!") and not escaped_marker
                value = raw[1:] if negated else raw
                if escaped_marker:
                    value = value[1:]
                value = value.rstrip()
                if not value:
                    continue
                directory_only = value.endswith("/")
                value = value.rstrip("/")
                anchored = value.startswith("/")
                value = value.lstrip("/")
                patterns.append(
                    IgnorePattern(
                        base=ignore_file.parent,
                        pattern=value,
                        negated=negated,
                        directory_only=directory_only,
                        anchored=anchored,
                    )
                )
        self._cache[ignore_file] = patterns
        return patterns

    def _matches(self, rule: IgnorePattern, target: Path, *, is_dir: bool | None) -> bool:
        try:
            rel = target.relative_to(rule.base).as_posix()
        except ValueError:
            return False
        target_is_dir = target.is_dir() if is_dir is None else is_dir
        pattern = rule.pattern
        if "/" in pattern or rule.anchored:
            matched = fnmatch.fnmatchcase(rel, pattern)
            if rule.directory_only:
                matched = matched or rel.startswith(pattern.rstrip("/") + "/")
            return matched and (target_is_dir or not rule.directory_only or "/" in rel)

        parts = rel.split("/")
        if rule.directory_only:
            directory_parts = parts if target_is_dir else parts[:-1]
            return any(fnmatch.fnmatchcase(part, pattern) for part in directory_parts)
        return any(fnmatch.fnmatchcase(part, pattern) for part in parts)
