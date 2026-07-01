"""Best-effort local security audit logging."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

SENSITIVE_KEYWORDS = ("token", "secret", "password", "api_key", "apikey", "content")


class SecurityAuditLogger:
    """Write redacted security events to JSONL without affecting tool execution."""

    def __init__(
        self,
        path: str | Path = ".opennova/audit/security.jsonl",
        enabled: bool = True,
        max_arg_chars: int = 500,
        session_id: str | None = None,
    ):
        self.path = Path(path)
        self.enabled = enabled
        self.max_arg_chars = max_arg_chars
        self.session_id = session_id

    def log_tool_event(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        guard_result: Any | None = None,
        result: Any | None = None,
        confirmation_outcome: str | None = None,
        checkpoint_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            event = {
                "timestamp": time.time(),
                "session_id": self.session_id,
                "tool_name": tool_name,
                "arguments": self._redact(arguments),
                "confirmation_outcome": confirmation_outcome,
                "checkpoint_id": checkpoint_id,
                "duration_ms": duration_ms,
            }
            if guard_result is not None:
                metadata = getattr(guard_result, "metadata", {}) or {}
                event["guard"] = {
                    "allowed": getattr(guard_result, "allowed", None),
                    "risk_level": str(getattr(guard_result, "risk_level", "")),
                    "requires_confirmation": getattr(guard_result, "requires_confirmation", None),
                    "reason": getattr(guard_result, "reason", ""),
                    "rule_id": metadata.get("rule_id"),
                    "rule_reason": metadata.get("rule_reason"),
                    "command_analysis": metadata.get("command_analysis"),
                }
            if result is not None:
                event["result"] = {
                    "success": getattr(result, "success", None),
                    "error": self._truncate(getattr(result, "error", "") or ""),
                }

            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _redact(self, value: Any, key: str = "") -> Any:
        if any(keyword in key.lower() for keyword in SENSITIVE_KEYWORDS):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): self._redact(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(item, key) for item in value]
        if isinstance(value, str):
            return self._truncate(value)
        return value

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_arg_chars:
            return value
        return value[: self.max_arg_chars] + "...[truncated]"
