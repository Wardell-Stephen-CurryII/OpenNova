"""Provider-neutral run budgets derived from canonical model profiles."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from opennova.providers.base import Usage
from opennova.providers.models import ModelProfile


@dataclass(frozen=True)
class BudgetSnapshot:
    """Immutable usage report suitable for events and diagnostics."""

    turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    exhausted_reason: str | None


class RunBudget:
    """Track turn, token, output, and optional cost limits for one agent run."""

    def __init__(
        self,
        profile: ModelProfile,
        *,
        max_turns: int,
        token_budget: int = 0,
        cost_budget_usd: float = 0.0,
        max_output_tokens: int = 0,
        input_cost_per_million: float = 0.0,
        output_cost_per_million: float = 0.0,
    ) -> None:
        self.profile = profile
        self.max_turns = max(1, max_turns)
        self.token_budget = max(0, token_budget)
        self.cost_budget_usd = max(0.0, cost_budget_usd)
        configured_output = (
            max_output_tokens if max_output_tokens > 0 else profile.max_output_tokens
        )
        self.max_output_tokens = min(profile.max_output_tokens, configured_output)
        self.input_cost_per_million = max(0.0, input_cost_per_million)
        self.output_cost_per_million = max(0.0, output_cost_per_million)
        self.reset()

    def reset(self) -> None:
        self.turns = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.estimated_cost_usd = 0.0

    def record(self, usage: Usage | None) -> None:
        """Record one completed model turn, tolerating providers without usage data."""
        self.turns += 1
        if usage is None:
            return
        self.prompt_tokens += max(0, usage.prompt_tokens)
        self.completion_tokens += max(0, usage.completion_tokens)
        self.total_tokens += max(0, usage.total_tokens)
        self.estimated_cost_usd += (
            usage.prompt_tokens * self.input_cost_per_million
            + usage.completion_tokens * self.output_cost_per_million
        ) / 1_000_000

    def output_limit(self) -> int:
        """Return the maximum output tokens safe for the next request."""
        if not self.token_budget:
            return self.max_output_tokens
        remaining = max(0, self.token_budget - self.total_tokens)
        return min(self.max_output_tokens, remaining)

    def exhausted_reason(self) -> str | None:
        if self.turns >= self.max_turns:
            return f"reached maximum model turns ({self.max_turns})"
        if self.token_budget and self.total_tokens >= self.token_budget:
            return f"reached token budget ({self.token_budget})"
        if self.cost_budget_usd and self.estimated_cost_usd >= self.cost_budget_usd:
            return f"reached cost budget (${self.cost_budget_usd:.4f})"
        return None

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            turns=self.turns,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            estimated_cost_usd=round(self.estimated_cost_usd, 8),
            exhausted_reason=self.exhausted_reason(),
        )


class ProviderCircuitBreaker:
    """Small runtime-owned failure circuit with automatic cooldown."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    @staticmethod
    def key(provider: object) -> str:
        return f"{getattr(provider, 'provider_name', 'unknown')}:{getattr(provider, 'model', '')}"

    def is_open(self, provider: object) -> bool:
        key = self.key(provider)
        opened_at = self._opened_at.get(key)
        if opened_at is None:
            return False
        if monotonic() - opened_at >= self.cooldown_seconds:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)
            return False
        return True

    def record_success(self, provider: object) -> None:
        key = self.key(provider)
        self._failures.pop(key, None)
        self._opened_at.pop(key, None)

    def record_failure(self, provider: object) -> None:
        key = self.key(provider)
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        if failures >= self.failure_threshold:
            self._opened_at[key] = monotonic()
