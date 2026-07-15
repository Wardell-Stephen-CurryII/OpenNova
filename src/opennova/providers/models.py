"""Canonical model capability profiles shared by providers and context management."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelProfile:
    """Provider-neutral capabilities and context budgets for one model."""

    provider: str
    model: str
    context_window: int
    max_output_tokens: int
    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    supports_structured_output: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_PROFILES = [
    ModelProfile(
        "openai", "gpt-4o", 128_000, 16_384, supports_vision=True, supports_structured_output=True
    ),
    ModelProfile(
        "openai",
        "gpt-4o-mini",
        128_000,
        16_384,
        supports_vision=True,
        supports_structured_output=True,
    ),
    ModelProfile("openai", "gpt-4-turbo", 128_000, 4_096, supports_vision=True),
    ModelProfile("openai", "gpt-4", 8_192, 4_096),
    ModelProfile("openai", "o1-preview", 128_000, 32_768, supports_reasoning=True),
    ModelProfile("openai", "o1-mini", 128_000, 65_536, supports_reasoning=True),
    ModelProfile(
        "anthropic",
        "claude-sonnet-4-20250514",
        200_000,
        64_000,
        supports_vision=True,
        supports_reasoning=True,
    ),
    ModelProfile(
        "anthropic",
        "claude-opus-4-20250514",
        200_000,
        32_000,
        supports_vision=True,
        supports_reasoning=True,
    ),
    ModelProfile("anthropic", "claude-3-5-sonnet-20241022", 200_000, 8_192, supports_vision=True),
    ModelProfile("anthropic", "claude-3-5-haiku-20241022", 200_000, 8_192, supports_vision=True),
    ModelProfile("anthropic", "claude-3-opus-20240229", 200_000, 4_096, supports_vision=True),
    ModelProfile("anthropic", "claude-3-sonnet-20240229", 200_000, 4_096, supports_vision=True),
    ModelProfile("anthropic", "claude-3-haiku-20240307", 200_000, 4_096, supports_vision=True),
    ModelProfile("deepseek", "deepseek-chat", 64_000, 8_192),
    ModelProfile("deepseek", "deepseek-reasoner", 64_000, 8_192, supports_reasoning=True),
    ModelProfile("deepseek", "deepseek-v4-pro", 131_072, 16_384, supports_reasoning=True),
    ModelProfile("deepseek", "deepseek-v4-flash", 131_072, 8_192),
]

MODEL_PROFILES: dict[tuple[str, str], ModelProfile] = {
    (profile.provider, profile.model): profile for profile in _PROFILES
}

MODEL_ALIASES: dict[str, str] = {
    "claude-sonnet-4": "claude-sonnet-4-20250514",
    "claude-opus-4": "claude-opus-4-20250514",
    "claude-3.5-sonnet": "claude-3-5-sonnet-20241022",
    "claude-3.5-haiku": "claude-3-5-haiku-20241022",
}

DEFAULT_CONTEXT_WINDOWS = {
    "openai": 8_192,
    "anthropic": 200_000,
    "deepseek": 64_000,
}


def resolve_model_name(model: str) -> str:
    """Resolve a user-facing model alias to its canonical identifier."""
    return MODEL_ALIASES.get(model, model)


def get_model_profile(provider: str, model: str) -> ModelProfile:
    """Return a canonical profile, with a conservative provider fallback."""
    resolved = resolve_model_name(model)
    profile = MODEL_PROFILES.get((provider, resolved))
    if profile is not None:
        return profile
    return ModelProfile(
        provider=provider,
        model=resolved,
        context_window=DEFAULT_CONTEXT_WINDOWS.get(provider, 8_192),
        max_output_tokens=4_096,
    )


def model_capabilities_for_provider(provider: str) -> dict[str, dict[str, object]]:
    """Return backward-compatible capability dictionaries from the canonical registry."""
    return {
        profile.model: {
            "context_window": profile.context_window,
            "max_output_tokens": profile.max_output_tokens,
            "supports_tools": profile.supports_tools,
            "supports_vision": profile.supports_vision,
            "supports_reasoning": profile.supports_reasoning,
            "supports_structured_output": profile.supports_structured_output,
        }
        for profile in MODEL_PROFILES.values()
        if profile.provider == provider
    }


def context_window_for_model(model: str, default: int = 128_000) -> int:
    """Resolve a context window without requiring the caller to know the provider."""
    resolved = resolve_model_name(model)
    for profile in MODEL_PROFILES.values():
        if profile.model == resolved:
            return profile.context_window
    return default
