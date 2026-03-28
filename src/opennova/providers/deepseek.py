"""
DeepSeek LLM Provider implementation.

DeepSeek API is OpenAI-compatible, so this provider extends OpenAIProvider
with a different base URL and model configurations.

Supports deepseek-chat, deepseek-reasoner, and other DeepSeek models.
"""

from typing import Any

from opennova.providers.openai import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """
    DeepSeek API provider implementation.

    DeepSeek uses an OpenAI-compatible API, so we inherit from OpenAIProvider
    and just configure the base URL appropriately.

    DeepSeek models:
    - deepseek-chat: General-purpose chat model
    - deepseek-reasoner: Reasoning-focused model (similar to o1)
    """

    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"

    SUPPORTED_MODELS = {
        "deepseek-chat": {"context_window": 64000, "supports_vision": False},
        "deepseek-reasoner": {"context_window": 64000, "supports_vision": False},
    }

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str | None = None,
        **kwargs: Any,
    ):
        """
        Initialize DeepSeek provider.

        Args:
            api_key: DeepSeek API key
            model: Model identifier (default: deepseek-chat)
            base_url: Optional API base URL override
            **kwargs: Additional options passed to OpenAI client
        """
        actual_base_url = base_url or self.DEFAULT_BASE_URL
        super().__init__(api_key, model, actual_base_url, **kwargs)

    def get_model_info(self) -> dict[str, Any]:
        """Get information about the current model."""
        info = self.SUPPORTED_MODELS.get(
            self.model,
            {"context_window": 64000, "supports_vision": False},
        )
        return {
            "provider": "deepseek",
            "model": self.model,
            **info,
        }
