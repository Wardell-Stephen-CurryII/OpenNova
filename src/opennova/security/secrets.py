"""High-confidence secret scanning and redaction."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SecretFinding:
    """One high-confidence secret-like match."""

    kind: str
    start: int
    end: int

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "start": self.start, "end": self.end}


class SecretScanner:
    """Detect and redact common high-confidence secret patterns."""

    def __init__(self, enabled: bool = True, max_scan_chars: int = 200_000):
        self.enabled = enabled
        self.max_scan_chars = max_scan_chars
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            (
                "private-key",
                re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
            ),
            ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
            ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
            ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
            ("deepseek-key", re.compile(r"\bsk-deepseek-[A-Za-z0-9_-]{12,}\b")),
            (
                "secret-assignment",
                re.compile(
                    r"(?im)\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*['\"]?([^\s'\"]{8,})['\"]?"
                ),
            ),
        ]

    @classmethod
    def from_config(cls, config: dict[str, object] | None) -> SecretScanner:
        data = config or {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            max_scan_chars=int(data.get("max_scan_chars", 200_000)),
        )

    def scan(self, text: str) -> list[SecretFinding]:
        if not self.enabled or not text:
            return []
        haystack = text[: self.max_scan_chars]
        findings: list[SecretFinding] = []
        for kind, pattern in self._patterns:
            for match in pattern.finditer(haystack):
                findings.append(SecretFinding(kind=kind, start=match.start(), end=match.end()))
        return _dedupe_findings(findings)

    def redact(self, text: str) -> str:
        findings = self.scan(text)
        if not findings:
            return text
        redacted = []
        cursor = 0
        for finding in findings:
            redacted.append(text[cursor : finding.start])
            redacted.append("[REDACTED_SECRET]")
            cursor = finding.end
        redacted.append(text[cursor:])
        return "".join(redacted)


def _dedupe_findings(findings: list[SecretFinding]) -> list[SecretFinding]:
    ordered = sorted(findings, key=lambda item: (item.start, -(item.end - item.start)))
    selected: list[SecretFinding] = []
    last_end = -1
    for finding in ordered:
        if finding.start < last_end:
            continue
        selected.append(finding)
        last_end = finding.end
    return selected
