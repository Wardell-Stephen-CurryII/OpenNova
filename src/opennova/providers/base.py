"""
Base LLM Provider interface and common data structures.

This module defines the abstract base class for all LLM providers
and the standard data structures used throughout the system.
"""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

ToolChoice = Literal["auto", "required", "none"]


class FinishReason(StrEnum):
    """Finish reason for LLM response."""

    STOP = "stop"
    TOOL_CALL = "tool_call"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


@dataclass
class ToolCall:
    """Represents a tool/function call from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]
    call_type: Literal["function"] = "function"


@dataclass
class ToolParameter:
    """Tool parameter definition in JSON Schema format."""

    type: str
    description: str = ""
    default: Any = None
    required: bool = True
    enum: list[Any] | None = None
    properties: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON Schema dictionary format."""
        schema: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.default is not None:
            schema["default"] = self.default
        if self.enum:
            schema["enum"] = self.enum
        if self.properties:
            schema["properties"] = self.properties
        return schema


@dataclass
class ToolSchema:
    """Tool schema for LLM tool calling in OpenAI-compatible format."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI function format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class Message:
    """Chat message structure."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    token_count: int = 0
    reasoning_content: str | None = None
    is_compression_boundary: bool = False

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI message format."""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}

        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.call_type,
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]

        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id

        if self.name:
            msg["name"] = self.name

        if self.role == "assistant" and self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content

        return msg

    def to_anthropic_format(self) -> dict[str, Any]:
        """Convert to Anthropic message format."""
        if self.role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": self.tool_call_id,
                        "content": self.content,
                    }
                ],
            }

        msg: dict[str, Any] = {"role": self.role, "content": self.content}

        if self.role == "assistant" and self.tool_calls:
            content_blocks = [{"type": "text", "text": self.content}]
            for tc in self.tool_calls:
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            msg["content"] = content_blocks

        return msg

    def to_dict(self) -> dict[str, Any]:
        """Serialize Message to a JSON-compatible dict."""
        data: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "token_count": self.token_count,
        }
        if self.tool_calls:
            data["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments, "call_type": tc.call_type}
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        if self.reasoning_content:
            data["reasoning_content"] = self.reasoning_content
        if self.is_compression_boundary:
            data["is_compression_boundary"] = True
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Message":
        """Deserialize a Message from a dict."""
        tool_calls = None
        if "tool_calls" in data and data["tool_calls"]:
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc.get("arguments", {}),
                    call_type=tc.get("call_type", "function"),
                )
                for tc in data["tool_calls"]
            ]
        timestamp = datetime.now()
        if "timestamp" in data:
            with suppress(ValueError, TypeError):
                timestamp = datetime.fromisoformat(data["timestamp"])
        return Message(
            role=data["role"],
            content=data.get("content", ""),
            tool_calls=tool_calls,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            reasoning_content=data.get("reasoning_content"),
            is_compression_boundary=data.get("is_compression_boundary", False),
            timestamp=timestamp,
            token_count=data.get("token_count", 0),
        )


@dataclass
class Usage:
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class LLMResponse:
    """Standard LLM response structure."""

    content: str
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    finish_reason: FinishReason = FinishReason.STOP
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    reasoning_content: str | None = None


@dataclass
class StreamChunk:
    """Streaming response chunk."""

    content: str | None = None
    tool_call: ToolCall | None = None
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    delta: bool = True
    reasoning_content: str | None = None


class BaseLLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    All provider implementations must inherit from this class and implement
    the abstract methods. The interface is designed to be:
    - Async-first for better performance
    - Streaming-capable for real-time output
    - Tool-calling compatible for agent functionality
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        **kwargs: Any,
    ):
        """
        Initialize the LLM provider.

        Args:
            api_key: API key for authentication
            model: Model identifier (e.g., 'gpt-4o', 'claude-sonnet-4')
            base_url: Optional base URL override
            **kwargs: Additional provider-specific configuration
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.config = kwargs

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Send a chat request to the LLM and get a complete response.

        Args:
            messages: List of conversation messages
            tools: Optional list of available tools
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            Complete LLM response
        """
        pass

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """
        Send a chat request and stream the response.

        Args:
            messages: List of conversation messages
            tools: Optional list of available tools
            **kwargs: Additional parameters

        Yields:
            Stream chunks as they arrive
        """
        pass

    @abstractmethod
    def get_model_info(self) -> dict[str, Any]:
        """
        Get information about the current model.

        Returns:
            Dictionary with model metadata
        """
        pass

    def _build_system_prompt(self, messages: list[Message]) -> str | None:
        """Combine system messages in order for providers with a system field."""
        parts = [msg.content.strip() for msg in messages if msg.role == "system" and msg.content.strip()]
        return "\n\n".join(parts) or None

    def _filter_messages_for_anthropic(self, messages: list[Message]) -> list[Message]:
        """Filter out system messages for Anthropic API."""
        return [msg for msg in messages if msg.role != "system"]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model})"
