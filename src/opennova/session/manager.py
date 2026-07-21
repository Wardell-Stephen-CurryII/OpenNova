"""Session persistence — save and restore conversation history as JSONL files.

Mirrors Claude Code's session storage: each session is a UUID-named JSONL file
in ``~/.opennova/sessions/<sanitized-project-path>/``.
"""

import hashlib
import importlib
import json
import os
import re
import tempfile
import threading
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_fcntl: Any
try:
    _fcntl = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover - non-POSIX fallback
    _fcntl = None

SESSION_SCHEMA_VERSION = 2
DEFAULT_PERSISTENCE_CONFIG = {
    "debounce_ms": 250,
    "snapshot_event_threshold": 100,
    "snapshot_size_threshold": 1_048_576,
    "fsync_critical": True,
}


def _sanitize_path(path: str) -> str:
    """Return the pre-0.4.2 project directory key for migration only."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", str(Path(path).resolve()))


def _project_directory_name(path: str | Path) -> str:
    """Build a readable collision-resistant key from a canonical project path."""
    resolved = Path(path).resolve()
    normalized_path = unicodedata.normalize("NFC", str(resolved))
    raw_slug = unicodedata.normalize("NFKC", resolved.name or "root")
    slug = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in raw_slug
    )
    slug = re.sub(r"[-_]{2,}", "-", slug).strip("-_") or "project"
    digest = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()[:12]
    return f"{slug[:48]}-{digest}"


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
    schema_version: int = 1
    recovery_warnings: list[str] = field(default_factory=list)
    last_valid_revision: int = 0
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

    def __init__(
        self,
        project_path: str,
        persistence_config: dict[str, Any] | None = None,
    ) -> None:
        resolved = str(Path(project_path).resolve())
        self._project_root = Path(resolved)
        self._sessions_root = (Path.home() / ".opennova" / "sessions").resolve()
        self._sessions_dir = self._sessions_root / _project_directory_name(resolved)
        legacy_dir = self._sessions_root / _sanitize_path(resolved)
        self._legacy_sessions_dir = legacy_dir if legacy_dir != self._sessions_dir else None
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_verified_legacy_sessions()
        self._session_id: str | None = None
        self._file: Path | None = None
        self._message_count: int = 0
        self._first_prompt: str | None = None
        self._title: str | None = None
        self._lock = threading.RLock()
        self._pending_runtime_events: list[dict[str, Any]] = []
        self._flush_timer: threading.Timer | None = None
        self._events_since_snapshot = 0
        self._journal_bytes_since_snapshot = 0
        self._last_snapshot_revision = 0
        self._last_event_id: str | None = None
        self._last_snapshot_args: dict[str, Any] | None = None
        self._created_at: str | None = None
        self._forked_from: str | None = None
        self._header_written = False
        config = {**DEFAULT_PERSISTENCE_CONFIG, **(persistence_config or {})}
        self._debounce_seconds = max(0, int(config["debounce_ms"])) / 1000
        self._snapshot_event_threshold = max(1, int(config["snapshot_event_threshold"]))
        self._snapshot_size_threshold = max(1, int(config["snapshot_size_threshold"]))
        self._fsync_critical = bool(config["fsync_critical"])

    # ── session lifecycle ──────────────────────────────────────────

    def start_session(self) -> str:
        """Generate a new session ID and prepare the file (lazy creation)."""
        self.flush_runtime_events()
        self._session_id = str(uuid.uuid4())
        self._file = self._session_path(self._session_id)
        self._message_count = 0
        self._first_prompt = None
        self._title = None
        self._reset_runtime_journal_tracking()
        self._created_at = datetime.now(UTC).isoformat()
        self._forked_from = None
        self._header_written = False
        return self._session_id

    def resume_session(self, session_id: str) -> str:
        """Switch the active writer back to an existing session file."""
        self.flush_runtime_events()
        canonical_id = self._validate_session_id(session_id)
        source = self._resolve_session_file(canonical_id)
        if not source.exists():
            raise FileNotFoundError(f"Session not found: {canonical_id}")
        if source.parent != self._sessions_dir.resolve():
            source = self._copy_legacy_session_for_resume(source, canonical_id)
        self._session_id = canonical_id
        self._file = source
        self._message_count = 0
        self._first_prompt = None
        self._title = None
        self._reset_runtime_journal_tracking()
        self._restore_header_metadata()
        return self._session_id

    def fork_session(self, session_id: str) -> str:
        """Copy a persisted session into a new independently writable timeline."""
        source_id = self._validate_session_id(session_id)
        source = self._resolve_session_file(source_id)
        if not source.exists():
            raise FileNotFoundError(f"Session not found: {source_id}")
        fork_id = str(uuid.uuid4())
        destination = self._session_path(fork_id)
        entries = self._read_entries_unlocked(source)

        def rewrite(value: Any) -> Any:
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if not isinstance(value, dict):
                return value
            updated = {key: rewrite(item) for key, item in value.items()}
            if updated.get("session_id") == source_id:
                updated["session_id"] = fork_id
            return updated

        rewritten = [rewrite(entry) for entry in entries]
        for entry in rewritten:
            if entry.get("type") == "session_header":
                entry["session_id"] = fork_id
                entry["created_at"] = datetime.now(UTC).isoformat()
                entry["forked_from"] = source_id
                break
        else:
            rewritten.insert(
                0,
                {
                    "type": "session_header",
                    "schema_version": SESSION_SCHEMA_VERSION,
                    "session_id": fork_id,
                    "project_root": str(self._project_root),
                    "created_at": datetime.now(UTC).isoformat(),
                    "forked_from": source_id,
                },
            )

        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._sessions_dir,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = stream.name
                for entry in rewritten:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
            return fork_id
        finally:
            if temporary_path:
                with suppress(FileNotFoundError):
                    Path(temporary_path).unlink()

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
        with self._lock:
            self._append_entries([entry], fsync=False)
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
        with self._lock:
            self._append_entries([entry], fsync=False)

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
        """Compatibility wrapper for the Session v2 runtime snapshot writer."""
        self.save_runtime_snapshot(
            messages,
            compression_summary=compression_summary,
            transcript_events=transcript_events,
            plan_state=plan_state,
            runtime_state=runtime_state,
            state_events=state_events,
        )

    def save_runtime_snapshot(
        self,
        messages: list[Any],
        *,
        compression_summary: str | None = None,
        transcript_events: list[dict[str, Any]] | list[SessionTranscriptEvent] | None = None,
        plan_state: dict[str, Any] | None = None,
        runtime_state: dict[str, Any] | None = None,
        state_events: list[dict[str, Any]] | None = None,
    ) -> None:
        """Atomically compact messages and runtime state into a v2 snapshot."""
        if self._session_id is None or self._file is None:
            return

        self._message_count = len(messages)
        self._first_prompt = None
        for message in messages:
            data = message.to_dict()
            if data.get("role") == "user":
                self._first_prompt = data.get("content", "")
                break

        snapshot_revision = int((runtime_state or {}).get("revision", 0))
        entries: list[dict[str, Any]] = [self._session_header()]
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
                    "type": "runtime_snapshot",
                    "session_id": self._session_id,
                    "revision": snapshot_revision,
                    "last_event_id": self._last_event_id,
                    "state": runtime_state,
                }
            )
        supplied_events = [
            self._runtime_event_entry(event)
            for event in state_events or []
            if int(event.get("revision", 0)) > snapshot_revision and event.get("actions")
        ]

        with self._lock:
            self._cancel_flush_timer()
            pending = list(self._pending_runtime_events)
            self._pending_runtime_events.clear()
            with self._file_lock(exclusive=True):
                retained = self._runtime_events_after_revision(
                    self._read_entries_unlocked(self._file), snapshot_revision
                )
                retained.extend(
                    entry
                    for entry in pending
                    if int(entry.get("event", {}).get("revision", 0)) > snapshot_revision
                )
                retained.extend(supplied_events)
                retained = self._dedupe_runtime_event_entries(retained)
                self._atomic_write_entries([*entries, *retained])
                self._header_written = True
            self._last_snapshot_revision = snapshot_revision
            self._events_since_snapshot = len(retained)
            self._journal_bytes_since_snapshot = sum(
                len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) + 1 for entry in retained
            )
            self._last_snapshot_args = {
                "messages": list(messages),
                "compression_summary": compression_summary,
                "transcript_events": list(transcript_events or []),
                "plan_state": plan_state,
                "runtime_state": runtime_state,
            }

    def append_runtime_event(self, event: Any, durable: bool = False) -> None:
        """Append or debounce one replayable runtime event."""
        if self._session_id is None or self._file is None:
            return
        payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        if payload.get("session_id") and payload["session_id"] != self._session_id:
            raise ValueError("Runtime event session id does not match active session")
        payload["session_id"] = self._session_id
        entry = self._runtime_event_entry(payload)
        encoded_size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) + 1
        with self._lock:
            self._last_event_id = str(payload.get("event_id") or self._last_event_id or "") or None
            self._events_since_snapshot += 1
            self._journal_bytes_since_snapshot += encoded_size
            if durable:
                pending = [*self._pending_runtime_events, entry]
                self._pending_runtime_events.clear()
                self._cancel_flush_timer()
                self._append_entries(pending, fsync=self._fsync_critical)
                return
            self._pending_runtime_events.append(entry)
            self._schedule_runtime_flush()

    def flush_runtime_events(self) -> None:
        """Flush queued non-critical runtime events to the session journal."""
        with self._lock:
            self._cancel_flush_timer()
            if not self._pending_runtime_events:
                return
            entries = list(self._pending_runtime_events)
            self._pending_runtime_events.clear()
            self._append_entries(entries, fsync=False)

    def compact_session(self) -> None:
        """Rewrite the most recently supplied snapshot and absorb its journal."""
        args = self._last_snapshot_args
        if args is None:
            self.flush_runtime_events()
            return
        self.save_runtime_snapshot(**args)

    @property
    def needs_runtime_snapshot(self) -> bool:
        return (
            self._events_since_snapshot >= self._snapshot_event_threshold
            or self._journal_bytes_since_snapshot >= self._snapshot_size_threshold
        )

    def _schedule_runtime_flush(self) -> None:
        if self._debounce_seconds <= 0:
            self.flush_runtime_events()
            return
        if self._flush_timer is not None:
            return
        self._flush_timer = threading.Timer(self._debounce_seconds, self.flush_runtime_events)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _cancel_flush_timer(self) -> None:
        timer = self._flush_timer
        self._flush_timer = None
        if timer is not None:
            timer.cancel()

    def _reset_runtime_journal_tracking(self) -> None:
        self._cancel_flush_timer()
        self._pending_runtime_events.clear()
        self._events_since_snapshot = 0
        self._journal_bytes_since_snapshot = 0
        self._last_snapshot_revision = 0
        self._last_event_id = None
        self._last_snapshot_args = None

    def _append_entries(self, entries: list[dict[str, Any]], *, fsync: bool) -> None:
        if not entries or self._file is None:
            return
        with self._file_lock(exclusive=True):
            needs_header = not self._header_written
            with open(self._file, "a", encoding="utf-8") as stream:
                if needs_header:
                    stream.write(json.dumps(self._session_header(), ensure_ascii=False) + "\n")
                    self._header_written = True
                for entry in entries:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stream.flush()
                if fsync:
                    os.fsync(stream.fileno())

    def _atomic_write_entries(self, entries: list[dict[str, Any]]) -> None:
        if self._file is None:
            return
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._file.parent,
                prefix=f".{self._file.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = stream.name
                for entry in entries:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._file)
        finally:
            if temporary_path:
                with suppress(FileNotFoundError):
                    Path(temporary_path).unlink()

    @contextmanager
    def _file_lock(self, *, exclusive: bool) -> Iterator[None]:
        if self._file is None:
            yield
            return
        lock_path = self._file.with_suffix(self._file.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_stream:
            if _fcntl is not None:
                operation = _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH
                _fcntl.flock(lock_stream.fileno(), operation)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(lock_stream.fileno(), _fcntl.LOCK_UN)

    def _session_header(self) -> dict[str, Any]:
        if self._created_at is None:
            self._created_at = datetime.now(UTC).isoformat()
        header = {
            "type": "session_header",
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": self._session_id,
            "project_root": str(self._project_root),
            "created_at": self._created_at,
        }
        if self._forked_from:
            header["forked_from"] = self._forked_from
        return header

    def _restore_header_metadata(self) -> None:
        """Reuse the original v2 creation time when appending or compacting."""
        self._header_written = False
        self._created_at = None
        self._forked_from = None
        if self._file is None or not self._file.exists():
            return
        for entry in self._read_entries_unlocked(self._file):
            if entry.get("type") != "session_header":
                continue
            self._header_written = True
            created_at = entry.get("created_at")
            self._created_at = str(created_at) if created_at else None
            forked_from = entry.get("forked_from")
            self._forked_from = str(forked_from) if forked_from else None
            return
        self._created_at = datetime.fromtimestamp(self._file.stat().st_ctime, UTC).isoformat()

    def _runtime_event_entry(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "runtime_event",
            "session_id": self._session_id,
            "event": dict(event),
        }

    @staticmethod
    def _runtime_events_after_revision(
        entries: list[dict[str, Any]], revision: int
    ) -> list[dict[str, Any]]:
        return [
            entry
            for entry in entries
            if entry.get("type") == "runtime_event"
            and int(entry.get("event", {}).get("revision", 0)) > revision
        ]

    @staticmethod
    def _dedupe_runtime_event_entries(
        entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for entry in entries:
            event_id = str(entry.get("event", {}).get("event_id") or "")
            if event_id:
                unique[event_id] = entry
        return sorted(unique.values(), key=lambda item: int(item["event"]["revision"]))

    @staticmethod
    def _read_entries_unlocked(file: Path) -> list[dict[str, Any]]:
        if not file.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(file, encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
        return entries

    def _read_v2_runtime(
        self, session_id: str
    ) -> tuple[int, dict[str, Any], list[dict[str, Any]], list[str], int]:
        file = self._resolve_session_file(session_id)
        if not file.exists():
            return 1, {}, [], [], 0
        warnings: list[str] = []
        entries: list[dict[str, Any]] = []
        lines = file.read_text(encoding="utf-8").splitlines()
        nonempty_indexes = [index for index, line in enumerate(lines) if line.strip()]
        last_nonempty = nonempty_indexes[-1] if nonempty_indexes else -1
        runtime_corrupt = False
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                if index == last_nonempty:
                    warnings.append(f"Ignored truncated session tail at line {index + 1}")
                else:
                    warnings.append(f"Corrupt session entry at line {index + 1}")
                    runtime_corrupt = True
                continue
            if isinstance(entry, dict):
                if runtime_corrupt and entry.get("type") == "runtime_event":
                    continue
                entries.append(entry)

        header = next((entry for entry in entries if entry.get("type") == "session_header"), None)
        schema_version = int((header or {}).get("schema_version", 1))
        if schema_version > SESSION_SCHEMA_VERSION:
            raise ValueError(
                f"Session schema v{schema_version} is newer than supported v{SESSION_SCHEMA_VERSION}"
            )

        snapshots = [entry for entry in entries if entry.get("type") == "runtime_snapshot"]
        snapshot_entry = (
            max(snapshots, key=lambda item: int(item.get("revision", 0))) if snapshots else None
        )
        if snapshot_entry:
            snapshot = dict(snapshot_entry.get("state") or {})
            snapshot_revision = int(
                snapshot_entry.get("revision", snapshot.get("revision", 0)) or 0
            )
            schema_version = SESSION_SCHEMA_VERSION
        else:
            legacy_snapshot = next(
                (entry for entry in reversed(entries) if entry.get("type") == "runtime_state"),
                None,
            )
            snapshot = dict((legacy_snapshot or {}).get("runtime_state") or {})
            snapshot_revision = int(snapshot.get("revision", 0))
            if snapshot:
                schema_version = SESSION_SCHEMA_VERSION

        events = [
            dict(entry["event"])
            for entry in entries
            if entry.get("type") == "runtime_event"
            and isinstance(entry.get("event"), dict)
            and int(entry["event"].get("revision", 0)) > snapshot_revision
        ]
        return schema_version, snapshot, events, warnings, snapshot_revision

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
        with self._lock:
            self._append_entries([entry], fsync=False)

    def get_compression_markers(self, session_id: str) -> list[CompressionMarker]:
        """Read all compression markers from a session JSONL file."""
        file = self._resolve_session_file(session_id)
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

    def load_session(self, session_id: str, apply_compression: bool = True) -> list[Any]:
        """Load and deserialize messages from a session JSONL file.

        If apply_compression is True and compression markers exist, only
        messages after the last marker are returned. The caller must inject
        the summary message separately via get_compression_markers().

        Returns a list of ``Message`` objects in chronological order.
        """
        from opennova.providers.base import Message

        file = self._resolve_session_file(session_id)
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
        (
            schema_version,
            runtime_state,
            state_events,
            recovery_warnings,
            last_valid_revision,
        ) = self._read_v2_runtime(session_id)
        return LoadedSession(
            session_id=session_id,
            messages=messages,
            transcript_events=transcript_events,
            plan_state=plan_state,
            runtime_state=runtime_state,
            state_events=state_events,
            schema_version=schema_version,
            recovery_warnings=recovery_warnings,
            last_valid_revision=last_valid_revision,
            compression_summary=summary,
            compression_markers=markers,
        )

    # ── list ────────────────────────────────────────────────────────

    def list_sessions(self) -> list[SessionMeta]:
        """Return metadata for all saved sessions, newest first."""
        result: list[SessionMeta] = []
        for file in self._iter_session_files():
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
        file = self._resolve_session_file(session_id)
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
        file = self._resolve_session_file(session_id)
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
        return self._read_v2_runtime(session_id)[1]

    def _load_runtime_state_events(self, session_id: str) -> list[dict[str, Any]]:
        """Load persisted transition metadata following the runtime snapshot."""
        return self._read_v2_runtime(session_id)[2]

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

    @staticmethod
    def _validate_session_id(session_id: str) -> str:
        """Return a canonical UUID or reject path-like and malformed identifiers."""
        if not isinstance(session_id, str):
            raise ValueError("Session id must be a UUID string")
        try:
            parsed = uuid.UUID(session_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid session id: {session_id!r}") from exc
        canonical = str(parsed)
        if canonical != session_id.lower():
            raise ValueError(f"Session id must use canonical UUID form: {canonical}")
        return canonical

    def _session_path(self, session_id: str, directory: Path | None = None) -> Path:
        """Create a confined path for a validated session UUID."""
        canonical_id = self._validate_session_id(session_id)
        root = (directory or self._sessions_dir).resolve()
        candidate = (root / f"{canonical_id}.jsonl").resolve()
        if candidate.parent != root:
            raise ValueError("Session path escapes the configured session directory")
        return candidate

    def _resolve_session_file(self, session_id: str) -> Path:
        """Resolve a session in the current directory or a read-only legacy directory."""
        current = self._session_path(session_id)
        if current.exists():
            return current
        if self._legacy_sessions_dir is not None:
            legacy = self._session_path(session_id, self._legacy_sessions_dir)
            if legacy.exists() and self._legacy_session_belongs_to_project(legacy) is not False:
                return legacy
        return current

    def _iter_session_files(self) -> list[Path]:
        """List current and compatible legacy files without duplicate session ids."""
        files: dict[str, Path] = {}
        if self._sessions_dir.exists():
            for file in sorted(self._sessions_dir.glob("*.jsonl")):
                if self._is_valid_uuid(file.stem):
                    files[file.stem] = file
        legacy = self._legacy_sessions_dir
        if legacy is not None and legacy.exists():
            for file in sorted(legacy.glob("*.jsonl")):
                if (
                    self._is_valid_uuid(file.stem)
                    and file.stem not in files
                    and self._legacy_session_belongs_to_project(file) is not False
                ):
                    files[file.stem] = file
        return list(files.values())

    def _legacy_session_belongs_to_project(self, file: Path) -> bool | None:
        """Return verified ownership, or None for headerless legacy sessions."""
        for entry in self._read_entries_unlocked(file):
            if entry.get("type") != "session_header":
                continue
            project_root = entry.get("project_root")
            if not project_root:
                return None
            try:
                return Path(str(project_root)).resolve() == self._project_root
            except (OSError, RuntimeError):
                return False
        return None

    def _migrate_verified_legacy_sessions(self) -> None:
        """Atomically move only legacy sessions whose project identity is verified."""
        legacy = self._legacy_sessions_dir
        if legacy is None or not legacy.exists():
            return
        for source in legacy.glob("*.jsonl"):
            if not self._is_valid_uuid(source.stem):
                continue
            if self._legacy_session_belongs_to_project(source) is not True:
                continue
            destination = self._session_path(source.stem)
            if destination.exists():
                continue
            os.replace(source, destination)

    def _copy_legacy_session_for_resume(self, source: Path, session_id: str) -> Path:
        """Copy an ambiguous v1 legacy session before appending, preserving its source."""
        destination = self._session_path(session_id)
        if destination.exists():
            return destination
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self._sessions_dir,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = stream.name
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
            return destination
        finally:
            if temporary_path:
                with suppress(FileNotFoundError):
                    Path(temporary_path).unlink()

    # ── clear ───────────────────────────────────────────────────────

    def clear_session(self) -> None:
        """Start a fresh session (generate new UUID)."""
        self.flush_runtime_events()
        self._session_id = None
        self._file = None
        self._message_count = 0
        self._first_prompt = None
        self._title = None
        self._reset_runtime_journal_tracking()
        self._created_at = None
        self._forked_from = None
        self._header_written = False
