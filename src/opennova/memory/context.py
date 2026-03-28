"""
Context Manager - Manage LLM context window.

Handles:
- Message history management
- Token counting and context window limits
- Automatic context truncation and summarization
- Context optimization for different models
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from opennova.providers.base import Message

try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "o1-preview": 128000,
    "o1-mini": 128000,
    "claude-sonnet-4": 200000,
    "claude-opus-4": 200000,
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku": 200000,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "deepseek-chat": 64000,
    "deepseek-reasoner": 64000,
}

DEFAULT_CONTEXT_WINDOW = 128000
RESERVED_OUTPUT_TOKENS = 4096


@dataclass
class ContextStats:
    """Statistics about current context."""

    total_messages: int
    total_tokens: int
    context_window: int
    available_tokens: int
    utilization_percent: float


class ContextManager:
    """
    Manages conversation context for LLM interactions.

    Features:
    - Track message history
    - Count tokens using tiktoken
    - Auto-truncate when exceeding context window
    - Support for different model context sizes
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        context_window: int | None = None,
        max_messages: int = 100,
        encoding_name: str = "cl100k_base",
    ):
        """
        Initialize context manager.

        Args:
            model: Model name for context window detection
            context_window: Override context window size
            max_messages: Maximum messages to keep
            encoding_name: Tiktoken encoding name
        """
        self.model = model
        self.context_window = context_window or self._get_context_window(model)
        self.max_messages = max_messages
        self.encoding_name = encoding_name

        self.messages: list[Message] = []
        self.system_prompt: str | None = None

        self._encoding = None
        if TIKTOKEN_AVAILABLE:
            try:
                self._encoding = tiktoken.get_encoding(encoding_name)
            except Exception:
                pass

    def _get_context_window(self, model: str) -> int:
        """Get context window for a model."""
        model_lower = model.lower()

        for key, window in MODEL_CONTEXT_WINDOWS.items():
            if key.lower() in model_lower:
                return window

        return DEFAULT_CONTEXT_WINDOW

    def count_tokens(self, text: str) -> int:
        """
        Count tokens in a text string.

        Args:
            text: Text to count

        Returns:
            Token count
        """
        if self._encoding:
            return len(self._encoding.encode(text))

        return len(text) // 4

    def count_message_tokens(self, message: Message) -> int:
        """
        Count tokens in a message.

        Args:
            message: Message to count

        Returns:
            Token count including overhead
        """
        tokens = 4

        tokens += self.count_tokens(message.role)

        if message.content:
            tokens += self.count_tokens(message.content)

        if message.tool_calls:
            for tc in message.tool_calls:
                tokens += self.count_tokens(tc.name)
                tokens += self.count_tokens(str(tc.arguments))

        if message.name:
            tokens += self.count_tokens(message.name)

        return tokens

    def get_total_tokens(self) -> int:
        """Get total tokens in context."""
        total = 0

        if self.system_prompt:
            total += self.count_tokens(self.system_prompt) + 10

        for message in self.messages:
            total += self.count_message_tokens(message)

        return total

    def get_available_tokens(self) -> int:
        """Get available tokens in context window."""
        total = self.get_total_tokens()
        return max(0, self.context_window - RESERVED_OUTPUT_TOKENS - total)

    def get_stats(self) -> ContextStats:
        """Get context statistics."""
        total_tokens = self.get_total_tokens()
        available = self.context_window - RESERVED_OUTPUT_TOKENS - total_tokens

        return ContextStats(
            total_messages=len(self.messages),
            total_tokens=total_tokens,
            context_window=self.context_window,
            available_tokens=max(0, available),
            utilization_percent=(total_tokens / self.context_window) * 100,
        )

    def add_message(self, message: Message) -> bool:
        """
        Add a message to context.

        Args:
            message: Message to add

        Returns:
            True if added successfully, False if would exceed context
        """
        if len(self.messages) >= self.max_messages:
            self._trim_old_messages()

        new_tokens = self.count_message_tokens(message)

        if new_tokens > self.get_available_tokens():
            self._trim_old_messages()

            if new_tokens > self.get_available_tokens():
                return False

        self.messages.append(message)
        return True

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        msg = Message(role="user", content=content)
        self.add_message(msg)

    def add_assistant_message(
        self,
        content: str,
        tool_calls: list[Any] | None = None,
    ) -> None:
        """Add an assistant message."""
        msg = Message(role="assistant", content=content, tool_calls=tool_calls)
        self.add_message(msg)

    def add_tool_message(
        self,
        content: str,
        tool_call_id: str,
        name: str | None = None,
    ) -> None:
        """Add a tool result message."""
        msg = Message(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
        self.add_message(msg)

    def set_system_prompt(self, prompt: str) -> None:
        """Set the system prompt."""
        self.system_prompt = prompt

    def get_messages_for_llm(self) -> list[Message]:
        """
        Get messages formatted for LLM API.

        Returns:
            List of messages with system prompt if set
        """
        result = []

        if self.system_prompt:
            result.append(Message(role="system", content=self.system_prompt))

        result.extend(self.messages)

        return result

    def _trim_old_messages(self, keep_last: int = 4) -> None:
        """
        Trim old messages to fit context window.

        Args:
            keep_last: Number of recent messages to always keep
        """
        if len(self.messages) <= keep_last:
            return

        while (
            len(self.messages) > keep_last
            and self.get_total_tokens() > self.context_window * 0.7
        ):
            self.messages.pop(0)

    def summarize_old_context(self) -> str:
        """
        Create a summary of old context for compression.

        Returns:
            Summary string of old context
        """
        if len(self.messages) < 6:
            return ""

        old_messages = self.messages[:-4]

        summary_parts = ["[Previous context summary:]"]

        topics = set()
        for msg in old_messages:
            if msg.role == "user":
                words = msg.content.lower().split()[:10]
                topics.update(w for w in words if len(w) > 4)

        if topics:
            summary_parts.append(f"Topics discussed: {', '.join(list(topics)[:5])}")

        tools_used = set()
        for msg in old_messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    tools_used.add(tc.name)

        if tools_used:
            summary_parts.append(f"Tools used: {', '.join(tools_used)}")

        return "\n".join(summary_parts)

    def clear(self) -> None:
        """Clear all messages."""
        self.messages.clear()

    def get_last_n_messages(self, n: int) -> list[Message]:
        """Get the last N messages."""
        return self.messages[-n:] if n > 0 else []

    def get_conversation_history(self) -> list[dict[str, Any]]:
        """Get conversation history as list of dicts."""
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
            }
            for msg in self.messages
        ]

    def __len__(self) -> int:
        return len(self.messages)

    def __repr__(self) -> str:
        stats = self.get_stats()
        return (
            f"ContextManager(messages={len(self.messages)}, "
            f"tokens={stats.total_tokens}/{self.context_window}, "
            f"utilization={stats.utilization_percent:.1f}%)"
        )
