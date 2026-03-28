"""
OpenAI LLM Provider implementation.

Supports GPT-4o, GPT-4-turbo, o1, o1-mini, and other OpenAI models.
Fully supports streaming and tool/function calling.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from opennova.providers.base import (
    BaseLLMProvider,
    FinishReason,
    LLMResponse,
    Message,
    StreamChunk,
    ToolCall,
    ToolSchema,
    Usage,
)


class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI API provider implementation.

    Uses the official OpenAI Python SDK (openai>=1.0) for robust API interaction.
    Supports all OpenAI models including GPT-4, GPT-4o, and o1 series.
    """

    SUPPORTED_MODELS = {
        "gpt-4o": {"context_window": 128000, "supports_vision": True},
        "gpt-4o-mini": {"context_window": 128000, "supports_vision": True},
        "gpt-4-turbo": {"context_window": 128000, "supports_vision": True},
        "gpt-4": {"context_window": 8192, "supports_vision": False},
        "o1-preview": {"context_window": 128000, "supports_vision": False},
        "o1-mini": {"context_window": 128000, "supports_vision": False},
    }

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str | None = None,
        **kwargs: Any,
    ):
        """
        Initialize OpenAI provider.

        Args:
            api_key: OpenAI API key
            model: Model identifier (default: gpt-4o)
            base_url: Optional API base URL override (for proxies/compatibles)
            **kwargs: Additional options (organization, timeout, etc.)
        """
        super().__init__(api_key, model, base_url, **kwargs)

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=kwargs.get("timeout", 120.0),
            max_retries=kwargs.get("max_retries", 2),
        )

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Send a complete chat request.

        Args:
            messages: Conversation messages
            tools: Available tools for function calling
            **kwargs: OpenAI API parameters (temperature, max_tokens, etc.)

        Returns:
            Complete LLM response
        """
        openai_messages = [msg.to_openai_format() for msg in messages]

        request_params: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
        }

        if tools:
            request_params["tools"] = [t.to_openai_format() for t in tools]
            request_params["tool_choice"] = kwargs.get("tool_choice", "auto")

        if "max_tokens" in kwargs:
            request_params["max_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            request_params["temperature"] = kwargs["temperature"]
        if "top_p" in kwargs:
            request_params["top_p"] = kwargs["top_p"]

        response = await self.client.chat.completions.create(**request_params)

        choice = response.choices[0]

        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                    call_type="function",
                )
                for tc in choice.message.tool_calls
            ]

        usage = None
        if response.usage:
            usage = Usage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )

        finish_reason_map = {
            "stop": FinishReason.STOP,
            "tool_calls": FinishReason.TOOL_CALL,
            "length": FinishReason.LENGTH,
            "content_filter": FinishReason.CONTENT_FILTER,
        }

        finish_reason_raw = choice.finish_reason
        if finish_reason_raw and hasattr(finish_reason_raw, "value"):
            finish_reason_raw = finish_reason_raw.value

        return LLMResponse(
            content=choice.message.content or "",
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason_map.get(
                finish_reason_raw or "stop",
                FinishReason.STOP,
            ),
            model=response.model,
            metadata={"response_id": response.id},
        )

    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream a chat response.

        Args:
            messages: Conversation messages
            tools: Available tools
            **kwargs: API parameters

        Yields:
            Stream chunks as they arrive
        """
        openai_messages = [msg.to_openai_format() for msg in messages]

        request_params: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if tools:
            request_params["tools"] = [t.to_openai_format() for t in tools]
            request_params["tool_choice"] = kwargs.get("tool_choice", "auto")

        if "max_tokens" in kwargs:
            request_params["max_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            request_params["temperature"] = kwargs["temperature"]

        stream = await self.client.chat.completions.create(**request_params)

        tool_call_accumulator: dict[int, dict[str, Any]] = {}

        async for chunk in stream:
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    yield StreamChunk(
                        usage=Usage(
                            prompt_tokens=chunk.usage.prompt_tokens,
                            completion_tokens=chunk.usage.completion_tokens,
                            total_tokens=chunk.usage.total_tokens,
                        ),
                        delta=False,
                    )
                continue

            choice = chunk.choices[0]

            if choice.delta.content:
                yield StreamChunk(content=choice.delta.content, delta=True)

            if choice.delta.tool_calls:
                for tc in choice.delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_accumulator:
                        tool_call_accumulator[idx] = {
                            "id": tc.id,
                            "name": tc.function.name if tc.function else None,
                            "arguments": "",
                        }

                    if tc.function:
                        if tc.function.name:
                            tool_call_accumulator[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_call_accumulator[idx]["arguments"] += tc.function.arguments

            if choice.finish_reason:
                for idx, tc_data in tool_call_accumulator.items():
                    try:
                        args = json.loads(tc_data["arguments"])
                    except json.JSONDecodeError:
                        args = {"raw": tc_data["arguments"]}

                    yield StreamChunk(
                        tool_call=ToolCall(
                            id=tc_data["id"] or f"call_{idx}",
                            name=tc_data["name"] or "unknown",
                            arguments=args,
                            call_type="function",
                        ),
                        delta=False,
                    )

                finish_reason_map = {
                    "stop": FinishReason.STOP,
                    "tool_calls": FinishReason.TOOL_CALL,
                    "length": FinishReason.LENGTH,
                }
                finish_reason_raw = choice.finish_reason
                if finish_reason_raw and hasattr(finish_reason_raw, "value"):
                    finish_reason_raw = finish_reason_raw.value
                yield StreamChunk(
                    finish_reason=finish_reason_map.get(
                        finish_reason_raw or "stop",
                        FinishReason.STOP,
                    ),
                    delta=False,
                )

    def get_model_info(self) -> dict[str, Any]:
        """Get information about the current model."""
        info = self.SUPPORTED_MODELS.get(
            self.model,
            {"context_window": 8192, "supports_vision": False},
        )
        return {
            "provider": "openai",
            "model": self.model,
            **info,
        }
