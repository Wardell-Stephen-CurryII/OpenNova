"""Turn-level file history with conflict-safe rollback."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class CheckpointConflictError(RuntimeError):
    """Raised when rollback would overwrite changes made after a checkpoint."""


@dataclass
class CheckpointEntry:
    """Before/after identity for one path in a turn checkpoint."""

    path: str
    operation: str = "pending"
    before_exists: bool = True
    after_exists: bool | None = None
    before_hash: str = ""
    after_hash: str = ""
    before_size: int = 0
    after_size: int = 0


@dataclass
class Checkpoint:
    """One local turn-level checkpoint entry."""

    id: str
    label: str
    files: list[str]
    entries: list[CheckpointEntry] = field(default_factory=list)
    created_at: str = ""
    run_id: str | None = None
    user_message: str | None = None
    tool_id: str | None = None


class CheckpointManager:
    """Create, finalize, inspect, and restore project-local file history."""

    def __init__(self, project_path: str | Path = "."):
        self.project_path = Path(project_path).resolve()
        self.root = self.project_path / ".opennova" / "checkpoints"
        self.index_path = self.root / "index.json"

    def create(
        self,
        label: str,
        files: list[str | Path],
        *,
        run_id: str | None = None,
        user_message: str | None = None,
        tool_id: str | None = None,
    ) -> str:
        checkpoint_id = str(uuid.uuid4())
        checkpoint_dir = self.root / checkpoint_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        relative_files: list[str] = []
        entries: list[CheckpointEntry] = []
        for file_value in files:
            file_path = Path(file_value).expanduser().resolve()
            relative = file_path.relative_to(self.project_path)
            relative_text = relative.as_posix()
            before_exists = file_path.is_file()
            before_hash, before_size = self._identity(file_path)
            if before_exists:
                target = checkpoint_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, target)
            relative_files.append(relative_text)
            entries.append(
                CheckpointEntry(
                    path=relative_text,
                    before_exists=before_exists,
                    before_hash=before_hash,
                    before_size=before_size,
                )
            )
        checkpoint = Checkpoint(
            id=checkpoint_id,
            label=label,
            files=relative_files,
            entries=entries,
            created_at=datetime.now(UTC).isoformat(),
            run_id=run_id,
            user_message=user_message,
            tool_id=tool_id,
        )
        checkpoints = self.list_checkpoints()
        checkpoints.insert(0, checkpoint)
        self._save(checkpoints)
        return checkpoint_id

    def finalize(self, checkpoint_id: str) -> Checkpoint:
        """Capture post-tool identities so later restore can detect conflicts."""
        checkpoints = self.list_checkpoints()
        checkpoint = self._resolve(checkpoint_id, checkpoints)
        after_root = self.root / checkpoint.id / "_after"
        for entry in checkpoint.entries:
            target = self.project_path / entry.path
            entry.after_exists = target.is_file()
            entry.after_hash, entry.after_size = self._identity(target)
            if not entry.before_exists and entry.after_exists:
                entry.operation = "create"
            elif entry.before_exists and not entry.after_exists:
                entry.operation = "delete"
            elif entry.before_hash != entry.after_hash:
                entry.operation = "modify"
            else:
                entry.operation = "unchanged"
            if entry.after_exists:
                snapshot = after_root / entry.path
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, snapshot)
        self._save(checkpoints)
        return checkpoint

    def restore(self, checkpoint_id: str, *, force: bool = False) -> None:
        checkpoint = self._resolve(checkpoint_id, self.list_checkpoints())
        checkpoint_dir = self.root / checkpoint.id
        entries = checkpoint.entries or [CheckpointEntry(path=item) for item in checkpoint.files]
        conflicts: list[str] = []
        if not force:
            for entry in entries:
                if entry.after_exists is None:
                    continue
                target = self.project_path / entry.path
                current_exists = target.is_file()
                current_hash, _ = self._identity(target)
                if current_exists != entry.after_exists or (
                    current_exists and current_hash != entry.after_hash
                ):
                    conflicts.append(entry.path)
        if conflicts:
            raise CheckpointConflictError(
                "Files changed after checkpoint execution: "
                + ", ".join(conflicts)
                + ". Use force=True only after reviewing the diff."
            )

        for entry in entries:
            target = self.project_path / entry.path
            if entry.before_exists:
                source = checkpoint_dir / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            elif target.exists():
                target.unlink()

    def diff(self, checkpoint_id: str) -> str:
        """Return a robust before/current diff including create/delete tombstones."""
        checkpoint = self._resolve(checkpoint_id, self.list_checkpoints())
        checkpoint_dir = self.root / checkpoint.id
        entries = checkpoint.entries or [CheckpointEntry(path=item) for item in checkpoint.files]
        chunks: list[str] = []
        for entry in entries:
            before_path = checkpoint_dir / entry.path
            current_path = self.project_path / entry.path
            before = self._read_text(before_path) if entry.before_exists else ""
            current = self._read_text(current_path) if current_path.is_file() else ""
            chunks.extend(
                difflib.unified_diff(
                    before.splitlines(),
                    current.splitlines(),
                    fromfile=f"checkpoint/{entry.path}",
                    tofile=entry.path,
                    lineterm="",
                )
            )
        return "\n".join(chunks) or "No differences"

    def list_checkpoints(self) -> list[Checkpoint]:
        if not self.index_path.exists():
            return []
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        checkpoints: list[Checkpoint] = []
        for item in payload.get("checkpoints", []):
            data = dict(item)
            data["entries"] = [
                entry if isinstance(entry, CheckpointEntry) else CheckpointEntry(**entry)
                for entry in data.get("entries", [])
            ]
            checkpoints.append(Checkpoint(**data))
        return checkpoints

    def _resolve(
        self,
        checkpoint_id: str,
        checkpoints: list[Checkpoint],
    ) -> Checkpoint:
        matches = [item for item in checkpoints if item.id.startswith(checkpoint_id)]
        if len(matches) != 1:
            raise ValueError(f"Checkpoint not found or ambiguous: {checkpoint_id}")
        return matches[0]

    def _save(self, checkpoints: list[Checkpoint]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "version": 2,
            "checkpoints": [asdict(item) for item in checkpoints],
        }
        fd, temp_name = tempfile.mkstemp(prefix=".index-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.index_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _identity(path: Path) -> tuple[str, int]:
        if not path.is_file():
            return "", 0
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest(), len(content)

    @staticmethod
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
