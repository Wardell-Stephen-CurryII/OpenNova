"""Search tools for file discovery and content lookup."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from opennova.security.sandbox import Sandbox, SandboxConfig
from opennova.tools.base import BaseTool, ToolResult

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
}


def _build_sandbox(config: dict[str, Any] | None = None) -> Sandbox:
    tool_config = config or {}
    return Sandbox(
        SandboxConfig(
            working_dir=str(tool_config.get("working_dir", Path.cwd())),
            allowed_paths=tool_config.get("allowed_paths", []),
            denied_paths=tool_config.get("denied_paths"),
            read_only=bool(tool_config.get("read_only", False)),
            max_file_size=int(tool_config.get("max_file_size", 100 * 1024 * 1024)),
        )
    )


def _load_gitignore_patterns(root: Path, respect_gitignore: bool) -> list[str]:
    if not respect_gitignore:
        return []
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return []
    patterns: list[str] = []
    for raw_line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line.rstrip("/"))
    return patterns


def _is_ignored(path: Path, root: Path, patterns: list[str]) -> bool:
    rel = path.relative_to(root).as_posix()
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


class GlobFilesTool(BaseTool):
    """Find files by glob pattern inside the sandbox."""

    name = "glob_files"
    search_hint = "Find files by glob pattern without running shell commands"
    description = "Find files matching a glob pattern. Respects sandbox boundaries and common ignored directories."

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def execute(
        self,
        pattern: str,
        directory: str = ".",
        max_results: int = 100,
        include_hidden: bool = False,
        respect_gitignore: bool = True,
    ) -> ToolResult:
        root = Path(directory).resolve()
        allowed, reason = self.sandbox.is_path_allowed(root)
        if not allowed:
            return ToolResult(success=False, output="", error=reason)
        if not root.exists() or not root.is_dir():
            return ToolResult(success=False, output="", error=f"Directory does not exist: {directory}")

        ignore_patterns = _load_gitignore_patterns(root, respect_gitignore)
        matches: list[str] = []
        for path in root.rglob(pattern):
            if len(matches) >= max_results:
                break
            if any(part in DEFAULT_EXCLUDED_DIRS for part in path.parts):
                continue
            if not include_hidden and any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            if _is_ignored(path, root, ignore_patterns):
                continue
            allowed, _ = self.sandbox.is_path_allowed(path)
            if allowed and path.is_file():
                matches.append(str(path.relative_to(root)))

        output = "\n".join(matches) if matches else "(no matches)"
        return ToolResult(
            success=True,
            output=output,
            metadata={"count": len(matches), "directory": str(root), "pattern": pattern},
        )

    def is_read_only(self, **kwargs: Any) -> bool:
        return True


class GrepCodeTool(BaseTool):
    """Search file contents inside the sandbox."""

    name = "grep_code"
    search_hint = "Search code contents without running shell commands"
    description = "Search text content across files. Returns file, line number, and matching line snippets."

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.sandbox = _build_sandbox(config)

    def execute(
        self,
        pattern: str,
        directory: str = ".",
        file_glob: str = "*",
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
        include_hidden: bool = False,
        respect_gitignore: bool = True,
        context_lines: int = 0,
    ) -> ToolResult:
        root = Path(directory).resolve()
        allowed, reason = self.sandbox.is_path_allowed(root)
        if not allowed:
            return ToolResult(success=False, output="", error=reason)
        if not root.exists() or not root.is_dir():
            return ToolResult(success=False, output="", error=f"Directory does not exist: {directory}")

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags) if regex else None
        except re.error as e:
            return ToolResult(success=False, output="", error=f"Invalid regex pattern: {e}")
        needle = pattern if case_sensitive else pattern.lower()
        context_lines = max(0, context_lines)
        ignore_patterns = _load_gitignore_patterns(root, respect_gitignore)

        matches: list[str] = []
        match_count = 0
        for path in root.rglob(file_glob):
            if match_count >= max_results:
                break
            if not path.is_file() or any(part in DEFAULT_EXCLUDED_DIRS for part in path.parts):
                continue
            rel_parts = path.relative_to(root).parts
            if not include_hidden and any(part.startswith(".") for part in rel_parts):
                continue
            if _is_ignored(path, root, ignore_patterns):
                continue
            allowed, _ = self.sandbox.is_path_allowed(path)
            if not allowed:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            rel = path.relative_to(root).as_posix()
            emitted_lines: set[int] = set()
            matched_lines: set[int] = set()
            for line_number, line in enumerate(lines, 1):
                haystack = line if case_sensitive else line.lower()
                found = bool(compiled.search(line)) if compiled else needle in haystack
                if found:
                    matched_lines.add(line_number)
                    start = max(1, line_number - context_lines)
                    end = min(len(lines), line_number + context_lines)
                    emitted_lines.update(range(start, end + 1))
                    match_count += 1
                    if match_count >= max_results:
                        break
            for line_number in sorted(emitted_lines):
                prefix = "" if line_number in matched_lines else "  "
                matches.append(f"{prefix}{rel}:{line_number}: {lines[line_number - 1].strip()}")

        output = "\n".join(matches) if matches else "(no matches)"
        return ToolResult(
            success=True,
            output=output,
            metadata={"count": match_count, "directory": str(root), "pattern": pattern},
        )

    def is_read_only(self, **kwargs: Any) -> bool:
        return True
