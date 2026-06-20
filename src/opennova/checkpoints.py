"""Lightweight file snapshot checkpoints for local rollback."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Checkpoint:
    """One local checkpoint entry."""

    id: str
    label: str
    files: list[str]


class CheckpointManager:
    """Create and restore simple file snapshots under .opennova/checkpoints."""

    def __init__(self, project_path: str | Path = "."):
        self.project_path = Path(project_path).resolve()
        self.root = self.project_path / ".opennova" / "checkpoints"
        self.index_path = self.root / "index.json"

    def create(self, label: str, files: list[str | Path]) -> str:
        checkpoint_id = str(uuid.uuid4())
        checkpoint_dir = self.root / checkpoint_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        relative_files: list[str] = []
        for file_value in files:
            file_path = Path(file_value).resolve()
            relative = file_path.relative_to(self.project_path)
            target = checkpoint_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target)
            relative_files.append(str(relative))
        checkpoints = self.list_checkpoints()
        checkpoints.insert(0, Checkpoint(id=checkpoint_id, label=label, files=relative_files))
        self._save(checkpoints)
        return checkpoint_id

    def restore(self, checkpoint_id: str) -> None:
        checkpoint = next(
            item for item in self.list_checkpoints() if item.id == checkpoint_id
        )
        checkpoint_dir = self.root / checkpoint_id
        for relative in checkpoint.files:
            source = checkpoint_dir / relative
            target = self.project_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def list_checkpoints(self) -> list[Checkpoint]:
        if not self.index_path.exists():
            return []
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        return [Checkpoint(**item) for item in payload.get("checkpoints", [])]

    def _save(self, checkpoints: list[Checkpoint]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"checkpoints": [asdict(item) for item in checkpoints]}
        self.index_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
