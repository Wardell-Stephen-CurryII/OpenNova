"""Layered project memory loaded from .opennova/memory markdown files."""

from __future__ import annotations

import hashlib
from pathlib import Path

MEMORY_DIR = ".opennova/memory"
DEFAULT_LAYERED_MEMORY_MAX_CHARS = 5000


class LayeredMemoryManager:
    """Load manually maintained project memory snippets for context injection."""

    def __init__(self, project_path: str | Path = "."):
        self.project_path = Path(project_path).resolve()
        self.memory_dir = self.project_path / MEMORY_DIR

    def load_for_context(
        self,
        max_chars: int = DEFAULT_LAYERED_MEMORY_MAX_CHARS,
        exclude_hashes: set[str] | None = None,
    ) -> str | None:
        """Load markdown memory files with exact-content dedupe and truncation."""
        if not self.memory_dir.exists():
            return None

        seen = set(exclude_hashes or set())
        parts: list[str] = []

        for path in sorted(self.memory_dir.rglob("*.md")):
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(self.memory_dir).parts):
                continue

            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                continue

            digest = self.content_hash(text)
            if digest in seen:
                continue
            seen.add(digest)

            rel_path = path.relative_to(self.project_path).as_posix()
            parts.append(f"### Memory file: {rel_path}\n{text}")

        if not parts:
            return None

        content = "\n\n".join(parts).strip()
        if len(content) <= max_chars:
            return content

        return (
            content[:max_chars].rstrip()
            + "\n\n[... .opennova/memory content truncated for context budget ...]"
        )

    @staticmethod
    def content_hash(text: str) -> str:
        """Stable hash for exact memory snippet dedupe."""
        normalized = text.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()
