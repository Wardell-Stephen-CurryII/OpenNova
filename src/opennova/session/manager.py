"""Session persistence — save and restore conversation history as JSONL files.

Mirrors Claude Code's session storage: each session is a UUID-named JSONL file
in ``~/.opennova/sessions/<sanitized-project-path>/``.
"""

import json
import os
import re
import uuid
from dataclasses import dataclass, field
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


@dataclass
class SessionTranscriptEvent:
    """Replayable TUI transcript event stored alongside a session snapshot."""

    kind: str
    payload: dict[str, Any]


@dataclass
class CompressionMarker:
    """Marks a compression boundary in a session JSONL file."""

    session_id: str
    summary: str
    message_count: int


@dataclass
class LoadedSession:
    """Messages and compression state loaded from a persisted session."""

    session_id: str
    messages: list[Any]
    transcript_events: list[SessionTranscriptEvent]
    plan_state: dict[str, Any] = field(default_factory=dict)
    runtime_state: dict[str, Any] = field(default_factory=dict)
    state_events: list[dict[str, Any]] = field(default_factory=list)
    compression_summary: str | None = None
    compression_markers: list[CompressionMarker] | None = None


def format_session_title_snippet(first_prompt: str, limit: int = 20) -> str:
    """Return a short session title derived from the first user prompt."""
    compact = " ".join((first_prompt or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


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
        self._title: str | None = None

    # ── session lifecycle ──────────────────────────────────────────

    def start_session(self) -> str:
        """Generate a new session ID and prepare the file (lazy creation)."""
        self._session_id = str(uuid.uuid4())
        self._file = self._sessions_dir / f"{self._session_id}.jsonl"
        self._message_count = 0
        self._first_prompt = None
        self._title = None
        return self._session_id

    def resume_session(self, session_id: str) -> str:
        """Switch the active writer back to an existing session file."""
        self._session_id = session_id
        self._file = self._sessions_dir / f"{session_id}.jsonl"
        self._message_count = 0
        self._first_prompt = None
        self._title = None
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
        self._title = title

    def save_snapshot(
        self,
        messages: list[Any],
        *,
        compression_summary: str | None = None,
        transcript_events: list[dict[str, Any]] | list[SessionTranscriptEvent] | None = None,
        plan_state: dict[str, Any] | None = None,
        runtime_state: dict[str, Any] | None = None,
        state_events: list[dict[str, Any]] | None = None,
    ) -> None:
        """Rewrite the current session file with a single deduplicated snapshot."""
        if self._session_id is None or self._file is None:
            return

        self._message_count = len(messages)
        self._first_prompt = None
        for message in messages:
            data = message.to_dict()
            if data.get("role") == "user":
                self._first_prompt = data.get("content", "")
                break

        entries: list[dict[str, Any]] = []
        if self._first_prompt:
            entries.append(
                {
                    "type": "first_prompt",
                    "session_id": self._session_id,
                    "content": self._first_prompt,
                }
            )
        if self._title:
            entries.append(
                {
                    "type": "title",
                    "session_id": self._session_id,
                    "title": self._title,
                }
            )
        for message in messages:
            entries.append(
                {
                    "type": "message",
                    "session_id": self._session_id,
                    "message": message.to_dict(),
                }
            )
        if compression_summary:
            entries.append(
                {
                    "type": "compression_boundary",
                    "session_id": self._session_id,
                    "summary": compression_summary,
                    "message_count": 0,
                }
            )
        for event in transcript_events or []:
            payload = event.payload if isinstance(event, SessionTranscriptEvent) else dict(event)
            entries.append(
                {
                    "type": "transcript_event",
                    "session_id": self._session_id,
                    "event": payload,
                }
            )
        if plan_state:
            entries.append(
                {
                    "type": "plan_state",
                    "session_id": self._session_id,
                    "plan_state": plan_state,
                }
            )
        if runtime_state:
            entries.append(
                {
                    "type": "runtime_state",
                    "session_id": self._session_id,
                    "runtime_state": runtime_state,
                }
            )
        for event in state_events or []:
            entries.append(
                {
                    "type": "runtime_state_event",
                    "session_id": self._session_id,
                    "event": event,
                }
            )

        temporary = self._file.with_name(f".{self._file.name}.tmp")
        with open(temporary, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, self._file)

    def save_compression_marker(self, summary: str, message_count: int) -> None:
        """Write a compression boundary marker to the current session JSONL."""
        if self._file is None:
            return
        entry = {
            "type": "compression_boundary",
            "session_id": self._session_id,
            "summary": summary,
            "message_count": message_count,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._file, "a", encoding="utf-8") as f:
            f.write(line)

    def get_compression_markers(self, session_id: str) -> list[CompressionMarker]:
        """Read all compression markers from a session JSONL file."""
        file = self._sessions_dir / f"{session_id}.jsonl"
        if not file.exists():
            return []
        markers: list[CompressionMarker] = []
        with open(file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "compression_boundary":
                    markers.append(
                        CompressionMarker(
                            session_id=entry.get("session_id", session_id),
                            summary=entry.get("summary", ""),
                            message_count=entry.get("message_count", 0),
                        )
                    )
        return markers

    # ── load ────────────────────────────────────────────────────────

    def load_session(
        self, session_id: str, apply_compression: bool = True
    ) -> list[Any]:
        """Load and deserialize messages from a session JSONL file.

        If apply_compression is True and compression markers exist, only
        messages after the last marker are returned. The caller must inject
        the summary message separately via get_compression_markers().

        Returns a list of ``Message`` objects in chronological order.
        """
        from opennova.providers.base import Message

        file = self._sessions_dir / f"{session_id}.jsonl"
        if not file.exists():
            return []

        # Determine the earliest message index to keep (after last marker)
        skip_until_count: int | None = None
        if apply_compression:
            markers = self.get_compression_markers(session_id)
            if markers:
                skip_until_count = markers[-1].message_count

        messages: list[Any] = []
        msg_index: int = 0
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
                    msg_index += 1
                    if skip_until_count is not None and msg_index <= skip_until_count:
                        continue
                    messages.append(Message.from_dict(entry["message"]))
        return self._dedupe_legacy_messages(messages)

    def load_session_with_summary(
        self, session_id: str, apply_compression: bool = True
    ) -> LoadedSession:
        """Load messages and the latest compression summary together."""
        markers = self.get_compression_markers(session_id) if apply_compression else []
        messages = self.load_session(session_id, apply_compression=apply_compression)
        summary = markers[-1].summary if markers else None
        transcript_events = self._load_transcript_events(session_id)
        plan_state = self._load_plan_state(session_id)
        runtime_state = self._load_runtime_state(session_id)
        state_events = self._load_runtime_state_events(session_id)
        return LoadedSession(
            session_id=session_id,
            messages=messages,
            transcript_events=transcript_events,
            plan_state=plan_state,
            runtime_state=runtime_state,
            state_events=state_events,
            compression_summary=summary,
            compression_markers=markers,
        )

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

    def _load_transcript_events(self, session_id: str) -> list[SessionTranscriptEvent]:
        """Load replayable transcript events from a session file."""
        file = self._sessions_dir / f"{session_id}.jsonl"
        if not file.exists():
            return []

        events: list[SessionTranscriptEvent] = []
        with open(file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "transcript_event" and isinstance(entry.get("event"), dict):
                    event = dict(entry["event"])
                    kind = str(event.get("kind") or "")
                    if kind:
                        events.append(SessionTranscriptEvent(kind=kind, payload=event))
        return events

    def _load_plan_state(self, session_id: str) -> dict[str, Any]:
        """Load persisted runtime plan state from a session file."""
        file = self._sessions_dir / f"{session_id}.jsonl"
        if not file.exists():
            return {}

        latest: dict[str, Any] = {}
        with open(file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "plan_state" and isinstance(entry.get("plan_state"), dict):
                    latest = dict(entry["plan_state"])
        return latest

    def _load_runtime_state(self, session_id: str) -> dict[str, Any]:
        """Load the latest schema-versioned runtime state snapshot."""
        file = self._sessions_dir / f"{session_id}.jsonl"
        if not file.exists():
            return {}
        latest: dict[str, Any] = {}
        with open(file, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "runtime_state" and isinstance(
                    entry.get("runtime_state"), dict
                ):
                    latest = dict(entry["runtime_state"])
        return latest

    def _load_runtime_state_events(self, session_id: str) -> list[dict[str, Any]]:
        """Load persisted transition metadata following the runtime snapshot."""
        file = self._sessions_dir / f"{session_id}.jsonl"
        if not file.exists():
            return []
        events: list[dict[str, Any]] = []
        with open(file, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "runtime_state_event" and isinstance(
                    entry.get("event"), dict
                ):
                    events.append(dict(entry["event"]))
        return events

    def _dedupe_legacy_messages(self, messages: list[Any]) -> list[Any]:
        """Collapse repeated appended snapshots from legacy session files."""
        serialized = [message.to_dict() for message in messages]
        changed = True
        while changed:
            changed = False
            half = len(serialized) // 2
            for prefix_len in range(1, half + 1):
                if serialized[:prefix_len] == serialized[prefix_len : prefix_len * 2]:
                    serialized = serialized[prefix_len:]
                    changed = True
                    break

        if len(serialized) == len(messages):
            return messages

        from opennova.providers.base import Message

        return [Message.from_dict(item) for item in serialized]

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
        self._title = None
