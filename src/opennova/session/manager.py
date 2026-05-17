"""Session persistence — save and restore conversation history as JSONL files.

Mirrors Claude Code's session storage: each session is a UUID-named JSONL file
in ``~/.opennova/sessions/<sanitized-project-path>/``.
"""

import json
import os
import re
import uuid

from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _sanitize_path(path: str) -> str:
    """Convert a filesystem path into a safe directory name."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", str(Path(path).resolve()))


@dataclass
class SessionMeta:
    """Lightweight session metadata for the resume picker."""

    session_id: str
    created: float
    modified: float
    first_prompt: str
    message_count: int
    file_size: int
    file_path: Path


class SessionManager:
    """Manages conversation session persistence as JSONL files."""

    def __init__(self, project_path: str) -> None:
        resolved = str(Path(project_path).resolve())
        self._sessions_dir = Path.home() / ".opennova" / "sessions" / _sanitize_path(resolved)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._session_id: str | None = None
        self._file: Path | None = None
        self._message_count: int = 0
        self._first_prompt: str | None = None

    # ── session lifecycle ──────────────────────────────────────────

    def start_session(self) -> str:
        """Generate a new session ID and prepare the file (lazy creation)."""
        self._session_id = str(uuid.uuid4())
        self._file = self._sessions_dir / f"{self._session_id}.jsonl"
        self._message_count = 0
        self._first_prompt = None
        return self._session_id

    @property
    def session_id(self) -> str | None:
        return self._session_id

    # ── save ────────────────────────────────────────────────────────

    def save_message(self, message: Any) -> None:
        """Append a Message as a JSONL entry. Creates the file on first write."""
        if self._session_id is None or self._file is None:
            return
        data = message.to_dict()
        entry = {
            "type": "message",
            "session_id": self._session_id,
            "message": data,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._file, "a", encoding="utf-8") as f:
            f.write(line)
        self._message_count += 1
        # Capture first user prompt
        if self._first_prompt is None and data.get("role") == "user":
            self._first_prompt = data["content"]
            self._save_first_prompt()

    def _save_first_prompt(self) -> None:
        if self._file is None or self._first_prompt is None:
            return
        entry = {
            "type": "first_prompt",
            "session_id": self._session_id,
            "content": self._first_prompt,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._file, "a", encoding="utf-8") as f:
            f.write(line)

    def save_title(self, title: str) -> None:
        """Set a custom title for the current session."""
        if self._file is None:
            return
        entry = {
            "type": "title",
            "session_id": self._session_id,
            "title": title,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._file, "a", encoding="utf-8") as f:
            f.write(line)

    # ── load ────────────────────────────────────────────────────────

    def load_session(self, session_id: str) -> list[Any]:
        """Load and deserialize all messages from a session JSONL file.

        Returns a list of ``Message`` objects in chronological order.
        """
        from opennova.providers.base import Message

        file = self._sessions_dir / f"{session_id}.jsonl"
        if not file.exists():
            return []

        messages: list[Any] = []
        with open(file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "message" and "message" in entry:
                    messages.append(Message.from_dict(entry["message"]))
        return messages

    # ── list ────────────────────────────────────────────────────────

    def list_sessions(self) -> list[SessionMeta]:
        """Return metadata for all saved sessions, newest first."""
        result: list[SessionMeta] = []
        if not self._sessions_dir.exists():
            return result

        for file in sorted(self._sessions_dir.glob("*.jsonl")):
            if not self._is_valid_uuid(file.stem):
                continue
            stat = file.stat()
            meta = SessionMeta(
                session_id=file.stem,
                created=stat.st_ctime,
                modified=stat.st_mtime,
                first_prompt="",
                message_count=0,
                file_size=stat.st_size,
                file_path=file,
            )
            # Extract first_prompt and message count from head/tail
            self._enrich_meta(meta)
            result.append(meta)

        result.sort(key=lambda m: m.modified, reverse=True)
        return result

    def _enrich_meta(self, meta: SessionMeta) -> None:
        """Read session metadata from the JSONL file."""
        try:
            with open(meta.file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") == "message":
                        meta.message_count += 1
                        if not meta.first_prompt and entry.get("message", {}).get("role") == "user":
                            meta.first_prompt = entry["message"]["content"]
                    elif entry.get("type") == "first_prompt":
                        meta.first_prompt = entry.get("content", "")
                    elif entry.get("type") == "title":
                        meta.first_prompt = entry.get("title", "")
        except Exception:
            pass

    @staticmethod
    def _is_valid_uuid(name: str) -> bool:
        try:
            uuid.UUID(name)
            return True
        except ValueError:
            return False

    # ── clear ───────────────────────────────────────────────────────

    def clear_session(self) -> None:
        """Start a fresh session (generate new UUID)."""
        self._session_id = None
        self._file = None
        self._message_count = 0
        self._first_prompt = None
