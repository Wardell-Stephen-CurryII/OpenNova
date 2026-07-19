"""
Context Manager - Manage LLM context window.

Handles:
- Message history management
- Token counting and context window limits
- Automatic context truncation and summarization
- Context optimization for different models
"""

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from opennova.providers.base import Message
from opennova.providers.models import context_window_for_model

try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


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


@dataclass(frozen=True)
class ContextPresentationSnapshot:
    """Read-only context statistics intended for user-facing presentation."""

    total_messages: int
    total_tokens: int
    context_window: int
    available_tokens: int
    utilization_percent: float
    compression_count: int
    has_compressed_summary: bool
    compression_threshold_percent: float


class MessageAddStatus(StrEnum):
    """Outcome of adding one or more messages to the active context."""

    ADDED = "added"
    ADDED_AFTER_COMPRESSION = "added_after_compression"
    REJECTED = "rejected"


@dataclass(frozen=True)
class MessageAddResult:
    """Explicit context insertion result that remains truthy on success."""

    status: MessageAddStatus
    message_count: int = 0
    reason: str | None = None

    @property
    def added(self) -> bool:
        return self.status is not MessageAddStatus.REJECTED

    def __bool__(self) -> bool:
        return self.added


class ContextCapacityError(RuntimeError):
    """Raised when a protocol message group cannot fit in the context window."""


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
        self._compression_failures: int = 0
        self.compression_failure_limit: int = 3
        self.compression_threshold: float = 0.55
        self.keep_last_pairs: int = 6

        self._encoding = None
        if TIKTOKEN_AVAILABLE:
            with suppress(Exception):
                self._encoding = tiktoken.get_encoding(encoding_name)

    def _get_context_window(self, model: str) -> int:
        """Get context window for a model."""
        return context_window_for_model(model, DEFAULT_CONTEXT_WINDOW)

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
            head + f"\n\n... [truncated: {len(tokens)} total tokens, {limit} limit] ...\n\n" + tail
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

    def get_presentation_snapshot(self) -> ContextPresentationSnapshot:
        """Return stable context and compression statistics for UI surfaces."""
        stats = self.get_stats()
        return ContextPresentationSnapshot(
            total_messages=stats.total_messages,
            total_tokens=stats.total_tokens,
            context_window=stats.context_window,
            available_tokens=stats.available_tokens,
            utilization_percent=stats.utilization_percent,
            compression_count=self._compression_count,
            has_compressed_summary=bool(self._compressed_summary),
            compression_threshold_percent=self.compression_threshold * 100,
        )

    def _prepare_message(self, message: Message) -> Message:
        """Normalize a message before token accounting and insertion."""
        if message.role != "tool":
            return message

        token_count = self.count_tokens(message.content)
        if token_count <= self.max_tool_result_tokens:
            return message

        return Message(
            role=message.role,
            content=self._truncate_tool_result(message.content),
            tool_call_id=message.tool_call_id,
            name=message.name,
            timestamp=message.timestamp,
        )

    def _append_messages(self, messages: list[Message]) -> MessageAddResult:
        """Append a complete message group or reject it without partial writes."""
        prepared = [self._prepare_message(message) for message in messages]
        if not prepared:
            return MessageAddResult(MessageAddStatus.ADDED, message_count=0)

        required_tokens = sum(self.count_message_tokens(message) for message in prepared)
        if (
            len(self.messages) + len(prepared) > self.max_messages
            or required_tokens > self._get_effective_available_tokens()
        ):
            self._trim_old_messages(
                required_tokens=required_tokens,
                required_slots=len(prepared),
            )

        if (
            len(self.messages) + len(prepared) > self.max_messages
            or required_tokens > self._get_effective_available_tokens()
        ):
            return MessageAddResult(
                MessageAddStatus.REJECTED,
                reason=(
                    f"Message group requires {required_tokens} tokens and "
                    f"{len(prepared)} slots, but only "
                    f"{self._get_effective_available_tokens()} tokens and "
                    f"{max(0, self.max_messages - len(self.messages))} slots are available"
                ),
            )

        self.messages.extend(prepared)
        return MessageAddResult(MessageAddStatus.ADDED, message_count=len(prepared))

    def add_message(self, message: Message) -> MessageAddResult:
        """
        Add a message to context.

        Args:
            message: Message to add

        Returns:
            Explicit result describing whether the message was added
        """
        return self._append_messages([message])

    def add_user_message(self, content: str) -> MessageAddResult:
        """Add a user message."""
        msg = Message(role="user", content=content)
        return self.add_message(msg)

    def add_assistant_message(
        self,
        content: str,
        tool_calls: list[Any] | None = None,
    ) -> MessageAddResult:
        """Add an assistant message."""
        msg = Message(role="assistant", content=content, tool_calls=tool_calls)
        return self.add_message(msg)

    def add_tool_message(
        self,
        content: str,
        tool_call_id: str,
        name: str | None = None,
    ) -> MessageAddResult:
        """Add a tool result message."""
        msg = Message(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
        return self.add_message(msg)

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
        if summary and self._compression_count == 0:
            self._compression_count = 1
        elif not summary:
            self._compression_count = 0

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
        if self._compression_failures >= self.compression_failure_limit:
            return False
        min_messages = self.keep_last_pairs * 2 + 4
        return len(self.messages) >= min_messages

    def _find_safe_cut_point(self) -> int | None:
        """Find index where we can safely split old from recent messages.

        Scans from the end, counting complete (assistant-with-tool_calls +
        tool-result) pairs. Never splits a pair. Returns the index of the
        first message to keep, or None if there aren't enough messages.
        """
        messages_to_keep = max(2, self.keep_last_pairs * 2)
        if len(self.messages) <= messages_to_keep:
            return None

        cut = len(self.messages) - messages_to_keep

        # Never leave tool results without their assistant tool-call message.
        while cut > 0 and self.messages[cut].role == "tool":
            cut -= 1

        return cut if cut > 0 else None

    async def compress(self) -> bool:
        """Compress old messages into a summary, keeping recent pairs."""
        if (
            self._compressor is None
            or self._compressing
            or self._compression_failures >= self.compression_failure_limit
        ):
            return False

        cut = self._find_safe_cut_point()
        if cut is None:
            return False

        old_messages = self.messages[:cut]
        if not old_messages:
            return False

        self._compressing = True
        try:
            try:
                summary = await self._compressor.compress(old_messages, self._compressed_summary)
            except Exception:
                self._compression_failures += 1
                return False

            if not summary:
                self._compression_failures += 1
                return False

            self._compressed_summary = summary
            self._compression_count += 1
            self._compression_failures = 0
            self.messages = self.messages[cut:]
            return True
        finally:
            self._compressing = False

    async def _maybe_compress(self) -> bool:
        """Check conditions and compress if needed."""
        if self._compressor is None:
            return False
        if not self._should_compress():
            return False
        if not self._is_safe_to_compress():
            return False
        return await self.compress()

    async def add_messages_and_compress(self, messages: list[Message]) -> MessageAddResult:
        """Atomically add a protocol message group, compressing and retrying once."""
        result = self._append_messages(messages)
        if result:
            await self._maybe_compress()
            return result

        if await self.compress():
            retried = self._append_messages(messages)
            if retried:
                await self._maybe_compress()
                return MessageAddResult(
                    MessageAddStatus.ADDED_AFTER_COMPRESSION,
                    message_count=retried.message_count,
                )
            return retried

        return result

    async def add_message_and_compress(self, message: Message) -> MessageAddResult:
        """Add a message and potentially compress. Async for ReActLoop use."""
        return await self.add_messages_and_compress([message])

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

    def _oldest_protocol_group_end(self) -> int:
        """Return the end index for the oldest complete conversation group."""
        if not self.messages:
            return 0
        first = self.messages[0]
        if first.role == "user":
            end = 1
            while end < len(self.messages) and self.messages[end].role != "user":
                end += 1
            return end
        if first.role == "assistant" and first.tool_calls:
            end = 1
            while end < len(self.messages) and self.messages[end].role == "tool":
                end += 1
            return end
        if first.role == "tool":
            end = 1
            while end < len(self.messages) and self.messages[end].role == "tool":
                end += 1
            return end
        return 1

    def _trim_old_messages(
        self,
        keep_last: int = 4,
        *,
        required_tokens: int = 0,
        required_slots: int = 0,
    ) -> None:
        """Fallback trim for when compression is not available.

        Removes complete protocol groups rather than orphaning tool results.
        """
        while self.messages:
            over_message_limit = len(self.messages) + required_slots > self.max_messages
            lacks_tokens = required_tokens > self._get_effective_available_tokens()
            over_soft_limit = (
                len(self.messages) > keep_last
                and self.get_total_tokens() > self.context_window * 0.7
            )
            if not (over_message_limit or lacks_tokens or over_soft_limit):
                break
            group_end = self._oldest_protocol_group_end()
            if group_end <= 0:
                break
            if (
                len(self.messages) - group_end < keep_last
                and not over_message_limit
                and not lacks_tokens
            ):
                break
            del self.messages[:group_end]

    def clear(self) -> None:
        """Clear all messages."""
        self.messages.clear()
        self._compression_failures = 0

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
