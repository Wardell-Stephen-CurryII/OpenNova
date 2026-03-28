"""
Anthropic LLM Provider implementation.

Supports Claude 4 (Sonnet, Opus) and Claude 3.5 models.
Fully supports streaming and tool use with Anthropic-specific format handling.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

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


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic API provider implementation.

    Uses the official Anthropic Python SDK (anthropic>=0.30) for Claud- API interaction.
    Supports Claude 4 and Claude 3.5 series models.

    Note: Anthropic has some differences from OpenAI:
    - System prompt is a separate parameter, not a message
    - Tool use format differs slightly
    - Streaming structure is different
    """

    SUPPORTED_MODELS = {
        "claude-sonnet-4-20250514": {"context_window": 200000, "supports_vision": True},
        "claude-opus-4-20250514": {"context_window": 200000, "supports_vision": True},
        "claude-3-5-sonnet-20241022": {"context_window": 200000, "supports_vision": True},
        "claude-3-5-haiku-20241022": {"context_window": 200000, "supports_vision": True},
        "claude-3-opus-20240229": {"context_window": 200000, "supports_vision": True},
        "claude-3-sonnet-20240229": {"context_window": 200000, "supports_vision": True},
        "claude-3-haiku-20240307": {"context_window": 200000, "supports_vision": True},
    }

    # Aliases for easier model selection
    MODEL_ALIASES = {
        "claude-sonnet-4": "claude-sonnet-4-20250514",
        "claude-opus-4": "claude-opus-4-20250514",
        "claude-3.5-sonnet": "claude-3-5-sonnet-20241022",
        "claude-3.5-haiku": "claude-3-5-haiku-20241022",
    }

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4",
        base_url: str | None = None,
        **kwargs: Any,
    ):
        """
        Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key
            model: Model identifier or alias (default: claude-sonnet-4)
            base_url: Optional API base URL override
            **kwargs: Additional options (timeout, max_retries, etc.)
        """
        resolved_model = self.MODEL_ALIASES.get(model, model)
        super().__init__(api_key, resolved_model, base_url, **kwargs)

        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": kwargs.get("timeout", 120.0),
            "max_retries": kwargs.get("max_retries", 2),
        }

        self.client = AsyncAnthropic(**client_kwargs)

    def _convert_tools_to_anthropic(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        """Convert OpenAI-style tools to Anthropic format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Send a complete chat request to Anthropic.

        Args:
            messages: Conversation messages (system message will be extracted)
            tools: Available tools
            **kwargs: Optional parameters (temperature, max_tokens, etc.)

        Returns:
            Complete LLM response
        """
        system_prompt = self._build_system_prompt(messages)
        filtered_messages = self._filter_messages_for_anthropic(messages)

        anthropic_messages = [msg.to_anthropic_format() for msg in filtered_messages]

        request_params: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }

        if system_prompt:
            request_params["system"] = system_prompt

        if tools:
            request_params["tools"] = self._convert_tools_to_anthropic(tools)

        if "temperature" in kwargs:
            request_params["temperature"] = kwargs["temperature"]
        if "top_p" in kwargs:
            request_params["top_p"] = kwargs["top_p"]

        response = await self.client.messages.create(**request_params)

        text_content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                        call_type="function",
                    )
                )

        usage = Usage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )

        finish_reason = FinishReason.STOP
        if tool_calls:
            finish_reason = FinishReason.TOOL_CALL
        elif response.stop_reason == "max_tokens":
            finish_reason = FinishReason.LENGTH
        elif response.stop_reason == "end_turn":
            finish_reason = FinishReason.STOP

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls if tool_calls else None,
            usage=usage,
            finish_reason=finish_reason,
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
        Stream a chat response from Anthropic.

        Args:
            messages: Conversation messages
            tools: Available tools
            **kwargs: API parameters

        Yields:
            Stream chunks as they arrive
        """
        system_prompt = self._build_system_prompt(messages)
        filtered_messages = self._filter_messages_for_anthropic(messages)

        anthropic_messages = [msg.to_anthropic_format() for msg in filtered_messages]

        request_params: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }

        if system_prompt:
            request_params["system"] = system_prompt

        if tools:
            request_params["tools"] = self._convert_tools_to_anthropic(tools)

        if "temperature" in kwargs:
            request_params["temperature"] = kwargs["temperature"]

        tool_call_accumulator: dict[str, dict[str, Any]] = {}

        async with self.client.messages.stream(**request_params) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text") and event.delta.text:
                        yield StreamChunk(content=event.delta.text, delta=True)

                    if hasattr(event.delta, "partial_json"):
                        block_id = event.index
                        tool_id = f"toolu_{block_id}"
                        if tool_id not in tool_call_accumulator:
                            tool_call_accumulator[tool_id] = {
                                "id": tool_id,
                                "name": "",
                                "arguments": "",
                            }
                        if event.delta.partial_json:
                            tool_call_accumulator[tool_id]["arguments"] += event.delta.partial_json

                elif event.type == "content_block_start":
                    if hasattr(event.content_block, "name"):
                        tool_id = event.content_block.id
                        tool_call_accumulator[tool_id] = {
                            "id": tool_id,
                            "name": event.content_block.name,
                            "arguments": "",
                        }

                elif event.type == "message_stop":
                    for tool_id, tc_data in tool_call_accumulator.items():
                        try:
                            args = json.loads(tc_data["arguments"])
                        except json.JSONDecodeError:
                            args = {"raw": tc_data["arguments"]}

                        yield StreamChunk(
                            tool_call=ToolCall(
                                id=tc_data["id"],
                                name=tc_data["name"],
                                arguments=args,
                                call_type="function",
                            ),
                            delta=False,
                        )

                    final_message = await stream.get_final_message()
                    finish_reason = FinishReason.STOP
                    if tool_call_accumulator:
                        finish_reason = FinishReason.TOOL_CALL
                    elif final_message.stop_reason == "max_tokens":
                        finish_reason = FinishReason.LENGTH

                    usage = Usage(
                        prompt_tokens=final_message.usage.input_tokens,
                        completion_tokens=final_message.usage.output_tokens,
                        total_tokens=final_message.usage.input_tokens
                        + final_message.usage.output_tokens,
                    )

                    yield StreamChunk(
                        finish_reason=finish_reason,
                        usage=usage,
                        delta=False,
                    )

    def get_model_info(self) -> dict[str, Any]:
        """Get information about the current model."""
        info = self.SUPPORTED_MODELS.get(
            self.model,
            {"context_window": 200000, "supports_vision": True},
        )
        return {
            "provider": "anthropic",
            "model": self.model,
            **info,
        }
