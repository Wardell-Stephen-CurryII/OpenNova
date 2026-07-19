"""Network destination policy for HTTP and shell-backed access."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class NetworkAnalysis:
    """Policy analysis for one network destination."""

    url: str
    method: str = "GET"
    scheme: str = ""
    hostname: str = ""
    is_internal: bool = False
    matched_domain: str | None = None
    risk_level: str = "safe"
    reason: str = "Network destination appears safe"

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "method": self.method,
            "scheme": self.scheme,
            "hostname": self.hostname,
            "is_internal": self.is_internal,
            "matched_domain": self.matched_domain,
            "risk_level": self.risk_level,
            "reason": self.reason,
        }


class NetworkPolicy:
    """Evaluate URL and host access against allow/deny rules."""

    def __init__(
        self,
        *,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        allow_localhost: bool = False,
        mutating_methods_require_confirmation: bool = True,
    ):
        self.allowed_domains = [_normalize_domain(domain) for domain in allowed_domains or []]
        self.blocked_domains = [_normalize_domain(domain) for domain in blocked_domains or []]
        self.allow_localhost = allow_localhost
        self.mutating_methods_require_confirmation = mutating_methods_require_confirmation

    @classmethod
    def from_config(cls, config: dict[str, object] | None) -> NetworkPolicy:
        data = config or {}
        return cls(
            allowed_domains=_as_str_list(data.get("allowed_domains")),
            blocked_domains=_as_str_list(data.get("blocked_domains")),
            allow_localhost=bool(data.get("allow_localhost", False)),
            mutating_methods_require_confirmation=bool(
                data.get("mutating_methods_require_confirmation", True)
            ),
        )

    def evaluate(self, url: str, method: str = "GET") -> NetworkAnalysis:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        analysis = NetworkAnalysis(
            url=url,
            method=method.upper(),
            scheme=parsed.scheme,
            hostname=hostname,
            is_internal=_is_internal_host(hostname),
        )

        if parsed.scheme not in {"http", "https", "ssh", "git", ""}:
            analysis.risk_level = "block"
            analysis.reason = f"Unsupported network URL scheme: {parsed.scheme}"
            return analysis

        if analysis.is_internal and not self.allow_localhost:
            analysis.risk_level = "danger"
            analysis.reason = "Network request targets localhost or a private address"

        blocked_match = self._match_domain(hostname, self.blocked_domains)
        if blocked_match:
            analysis.matched_domain = blocked_match
            analysis.risk_level = "block"
            analysis.reason = f"Domain is blocked by policy: {blocked_match}"
            return analysis

        allowed_match = self._match_domain(hostname, self.allowed_domains)
        if self.allowed_domains and not allowed_match:
            analysis.risk_level = "block"
            analysis.reason = f"Domain is not in allowed_domains: {hostname}"
            return analysis
        if allowed_match:
            analysis.matched_domain = allowed_match

        if (
            self.mutating_methods_require_confirmation
            and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
            and analysis.risk_level in {"safe", "warn"}
        ):
            analysis.risk_level = "danger"
            analysis.reason = f"Mutating HTTP request: {method.upper()}"

        return analysis

    @staticmethod
    def _match_domain(hostname: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            if hostname == pattern or hostname.endswith(f".{pattern}"):
                return pattern
        return None


def _normalize_domain(domain: str) -> str:
    return domain.strip().lower().lstrip(".")


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [_normalize_domain(value)]
    if isinstance(value, list):
        return [_normalize_domain(str(item)) for item in value if str(item).strip()]
    return []


def _is_internal_host(hostname: str) -> bool:
    if not hostname:
        return False
    if hostname in {"localhost", "0.0.0.0"}:
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return hostname.endswith(".localhost")
