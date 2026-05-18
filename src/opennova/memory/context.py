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
    "deepseek-v4-pro": 131072,
    "deepseek-v4-flash": 131072,
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
        max_tool_result_tokens: int = 8000,
    ):
        """
        Initialize context manager.

        Args:
            model: Model name for context window detection
            context_window: Override context window size
            max_messages: Maximum messages to keep
            encoding_name: Tiktoken encoding name
            max_tool_result_tokens: Truncate tool results exceeding this
        """
        self.model = model
        self.context_window = context_window or self._get_context_window(model)
        self.max_messages = max_messages
        self.encoding_name = encoding_name
        self.max_tool_result_tokens = max_tool_result_tokens

        self.messages: list[Message] = []
        self.system_prompt: str | None = None

        # Compression state
        self._compressed_summary: str | None = None
        self._compressor: Any = None
        self._compressing: bool = False
        self._compression_count: int = 0
        self.compression_threshold: float = 0.55
        self.keep_last_pairs: int = 6

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

    def _truncate_tool_result(self, content: str) -> str:
        """Truncate a tool result that exceeds max_tool_result_tokens.

        Keeps head (20%) and tail (80% of budget) to preserve structure.
        """
        if self._encoding is None:
            return content
        tokens = self._encoding.encode(content)
        limit = self.max_tool_result_tokens
        if len(tokens) <= limit:
            return content
        head_tokens = int(limit * 0.2)
        tail_tokens = limit - head_tokens
        head = self._encoding.decode(tokens[:head_tokens])
        tail = self._encoding.decode(tokens[-tail_tokens:])
        return (
            head
            + f"\n\n... [truncated: {len(tokens)} total tokens, {limit} limit] ...\n\n"
            + tail
        )

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

    def _get_effective_available_tokens(self) -> int:
        """Get available tokens without reserving output in undersized test contexts."""
        if self.context_window <= RESERVED_OUTPUT_TOKENS:
            return max(0, self.context_window - self.get_total_tokens())
        return self.get_available_tokens()

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
        # Truncate large tool results before counting/adding
        if message.role == "tool" and self._encoding:
            tok = self.count_tokens(message.content)
            if tok > self.max_tool_result_tokens:
                message = Message(
                    role=message.role,
                    content=self._truncate_tool_result(message.content),
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                    timestamp=message.timestamp,
                )

        if len(self.messages) >= self.max_messages:
            self._trim_old_messages()

        new_tokens = self.count_message_tokens(message)

        if new_tokens > self._get_effective_available_tokens():
            self._trim_old_messages()

            if new_tokens > self._get_effective_available_tokens():
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

    # ── compression ──────────────────────────────────────────────────

    def set_compressor(self, compressor: Any) -> None:
        """Wire in a ContextCompressor for LLM-based compression."""
        self._compressor = compressor

    def set_compressed_summary(self, summary: str | None) -> None:
        """Restore compression state (used when resuming sessions)."""
        self._compressed_summary = summary

    def get_compressed_summary(self) -> str | None:
        """Expose current summary for session persistence."""
        return self._compressed_summary

    def _should_compress(self) -> bool:
        """Check if token utilization exceeds the compression threshold."""
        tokens = self.get_total_tokens()
        return tokens > self.context_window * self.compression_threshold

    def _is_safe_to_compress(self) -> bool:
        """Check there are enough messages and we're not currently compressing."""
        if self._compressing:
            return False
        min_messages = self.keep_last_pairs * 2 + 4
        return len(self.messages) >= min_messages

    def _find_safe_cut_point(self) -> int | None:
        """Find index where we can safely split old from recent messages.

        Scans from the end, counting complete (assistant-with-tool_calls +
        tool-result) pairs. Never splits a pair. Returns the index of the
        first message to keep, or None if there aren't enough messages.
        """
        if len(self.messages) < self.keep_last_pairs * 2:
            return None

        pair_count = 0
        i = len(self.messages) - 1
        open_tool_ids: set[str] = set()

        while i >= 0 and pair_count < self.keep_last_pairs:
            msg = self.messages[i]
            if msg.role == "tool" and msg.tool_call_id:
                open_tool_ids.add(msg.tool_call_id)
            elif msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.id in open_tool_ids:
                        open_tool_ids.discard(tc.id)
                        pair_count += 1
            i -= 1

        # Walk back to include orphaned tool results
        while open_tool_ids and i >= 0:
            msg = self.messages[i]
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.id in open_tool_ids:
                        open_tool_ids.discard(tc.id)
            i -= 1

        cut = i + 1
        return cut if cut > 0 else None

    async def compress(self) -> bool:
        """Compress old messages into a summary, keeping recent pairs."""
        if self._compressor is None or self._compressing:
            return False

        cut = self._find_safe_cut_point()
        if cut is None:
            return False

        old_messages = self.messages[:cut]
        if not old_messages:
            return False

        try:
            summary = await self._compressor.compress(
                old_messages, self._compressed_summary
            )
        except Exception:
            return False

        if not summary:
            return False

        self._compressed_summary = summary
        self._compression_count += 1
        self.messages = self.messages[cut:]
        return True

    async def _maybe_compress(self) -> bool:
        """Check conditions and compress if needed."""
        if self._compressor is None:
            return False
        if not self._should_compress():
            return False
        if not self._is_safe_to_compress():
            return False
        self._compressing = True
        try:
            return await self.compress()
        finally:
            self._compressing = False

    async def add_message_and_compress(self, message: Message) -> bool:
        """Add a message and potentially compress. Async for ReActLoop use."""
        added = self.add_message(message)
        if added:
            await self._maybe_compress()
        return added

    # ── llm output ─────────────────────────────────────────────────

    def get_messages_for_llm(self) -> list[Message]:
        """Get messages with compression summary injected at the boundary."""
        result: list[Message] = []

        if self.system_prompt:
            result.append(Message(role="system", content=self.system_prompt))

        if self._compressed_summary:
            result.append(
                Message(
                    role="user",
                    content=(
                        "[Compressed conversation context"
                        f" ({self._compression_count} compression(s))]\n\n"
                        + self._compressed_summary
                    ),
                    is_compression_boundary=True,
                )
            )
            result.append(
                Message(
                    role="user",
                    content="[Continuing conversation after context compression]",
                    is_compression_boundary=True,
                )
            )

        result.extend(self.messages)
        return result

    def _trim_old_messages(self, keep_last: int = 4) -> None:
        """Fallback trim for when compression is not available.

        Pops oldest messages while token count exceeds 70% of window and
        more than keep_last messages remain.
        """
        if len(self.messages) <= keep_last:
            return
        while (
            len(self.messages) > keep_last
            and self.get_total_tokens() > self.context_window * 0.7
        ):
            self.messages.pop(0)

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
