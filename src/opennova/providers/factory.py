"""
LLM Provider Factory.

Creates and manages LLM provider instances based on configuration.
Supports OpenAI, Anthropic, and DeepSeek providers with extensibility
for custom providers.
"""

from typing import Any

from opennova.providers.base import BaseLLMProvider
from opennova.providers.openai import OpenAIProvider
from opennova.providers.anthropic import AnthropicProvider
from opennova.providers.deepseek import DeepSeekProvider


class ProviderFactory:
    """
    Factory for creating LLM provider instances.

    Singleton pattern ensures consistent provider management across the application.
    Supports runtime registration of custom providers.
    """

    _instance: "ProviderFactory | None" = None
    _providers: dict[str, type[BaseLLMProvider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "deepseek": DeepSeekProvider,
    }

    def __new__(cls) -> "ProviderFactory":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register_provider(cls, name: str, provider_class: type[BaseLLMProvider]) -> None:
        """
        Register a custom provider class.

        Args:
            name: Provider identifier (e.g., 'gemini', 'qwen')
            provider_class: Provider class (must inherit from BaseLLMProvider)
        """
        cls._providers[name] = provider_class

    @classmethod
    def create_provider(
        cls,
        provider_config: dict[str, Any],
        provider_name: str | None = None,
    ) -> BaseLLMProvider:
        """
        Create a provider instance from configuration.

        Args:
            provider_config: Full configuration dict containing 'providers' section
            provider_name: Optional specific provider name (uses default otherwise)

        Returns:
            Configured provider instance

        Raises:
            ValueError: If provider is unsupported or configuration is invalid
        """
        providers_config = provider_config.get("providers", {})
        default_provider = provider_name or provider_config.get("default_provider", "openai")

        if default_provider not in providers_config:
            raise ValueError(
                f"Provider '{default_provider}' not found in configuration. "
                f"Available: {list(providers_config.keys())}"
            )

        config = providers_config[default_provider]

        provider_type = config.get("type", default_provider)

        if provider_type not in cls._providers:
            available = list(cls._providers.keys())
            raise ValueError(
                f"Unknown provider type: '{provider_type}'. "
                f"Available providers: {available}"
            )

        provider_class = cls._providers[provider_type]

        api_key = config.get("api_key", "")
        if not api_key:
            raise ValueError(
                f"API key not found for provider '{default_provider}'. "
                "Please set it in configuration or environment variables."
            )

        model = config.get("default_model", config.get("model", ""))

        if not model:
            default_models = {
                "openai": "gpt-4o",
                "anthropic": "claude-sonnet-4",
                "deepseek": "deepseek-chat",
            }
            model = default_models.get(provider_type, "")
            if not model:
                raise ValueError(f"Model not specified for provider '{default_provider}'")

        kwargs: dict[str, Any] = {}
        if "base_url" in config:
            kwargs["base_url"] = config["base_url"]
        if "timeout" in config:
            kwargs["timeout"] = config["timeout"]

        return provider_class(api_key=api_key, model=model, **kwargs)

    @classmethod
    def list_providers(cls) -> list[str]:
        """Get list of registered provider names."""
        return list(cls._providers.keys())

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (mainly for testing)."""
        cls._instance = None
