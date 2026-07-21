"""Session-scoped file versions used for optimistic edit concurrency."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileVersion:
    """Stable identity for one file state."""

    path: str
    exists: bool
    mtime_ns: int | None
    size: int
    content_hash: str


class FileVersionCache:
    """Remember file states observed by one runtime session."""

    def __init__(self) -> None:
        self._versions: dict[str, FileVersion] = {}

    @staticmethod
    def canonical_path(path: str | Path) -> str:
        return str(Path(path).expanduser().resolve())

    @classmethod
    def snapshot(cls, path: str | Path) -> FileVersion:
        canonical = cls.canonical_path(path)
        target = Path(canonical)
        if not target.exists():
            return FileVersion(canonical, False, None, 0, hashlib.sha256(b"").hexdigest())
        stat = target.stat()
        content_hash = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else ""
        return FileVersion(canonical, True, stat.st_mtime_ns, stat.st_size, content_hash)

    def record(self, path: str | Path) -> FileVersion:
        version = self.snapshot(path)
        self._versions[version.path] = version
        return version

    def get(self, path: str | Path) -> FileVersion | None:
        return self._versions.get(self.canonical_path(path))

    def validate(self, path: str | Path) -> tuple[bool, FileVersion | None, FileVersion]:
        """Compare the current state with the last state observed by this session."""
        canonical = self.canonical_path(path)
        expected = self._versions.get(canonical)
        current = self.snapshot(canonical)
        return expected is None or expected == current, expected, current

    def discard(self, path: str | Path) -> None:
        self._versions.pop(self.canonical_path(path), None)

    def clear(self) -> None:
        self._versions.clear()

    def to_dict(self) -> dict[str, FileVersion]:
        return dict(self._versions)
