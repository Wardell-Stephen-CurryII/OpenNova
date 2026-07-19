"""Context Compressor — compress old conversation context into an LLM-generated summary."""

from __future__ import annotations

from opennova.providers.base import BaseLLMProvider, Message


class ContextCompressor:
    """Compresses old conversation messages into a concise summary using an LLM."""

    COMPRESSION_PROMPT = """\
You are a context compressor for an AI coding agent. Summarize the conversation below, preserving:

1. User's explicit requests and goals
2. Key decisions made and their rationale
3. Files read, modified, or created (with full paths)
4. Errors encountered and how they were resolved
5. Current task state (completed, in-progress, remaining)
6. Important code patterns, conventions, or constraints discovered

Output a single paragraph (max 1500 tokens). Use flowing prose, NOT bullet points.

{previous_summary_block}

<conversation>
{messages}
</conversation>

Summary:"""

    def __init__(self, llm_provider: BaseLLMProvider):
        self.llm = llm_provider

    def build_compression_prompt(
        self,
        messages_text: str,
        previous_summary: str | None = None,
    ) -> str:
        if previous_summary:
            previous_summary_block = f"<previous_summary>\n{previous_summary}\n</previous_summary>"
        else:
            previous_summary_block = ""
        return self.COMPRESSION_PROMPT.format(
            previous_summary_block=previous_summary_block,
            messages=messages_text,
        )

    def _format_messages(self, messages: list[Message]) -> str:
        """Convert messages to a compact text format for compression."""
        lines: list[str] = []
        for msg in messages:
            role = msg.role
            content = msg.content or ""
            if msg.tool_calls:
                tools = ", ".join(tc.name for tc in msg.tool_calls)
                content = f"[called tools: {tools}] {content}"
            if len(content) > 2000:
                content = content[:2000] + "..."
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    async def compress(
        self,
        messages: list[Message],
        previous_summary: str | None = None,
    ) -> str:
        """Call the LLM to produce a compressed summary. Returns the summary string."""
        messages_text = self._format_messages(messages)
        prompt = self.build_compression_prompt(messages_text, previous_summary)

        response = await self.llm.chat(
            messages=[Message(role="user", content=prompt)],
            tools=None,
            max_tokens=2048,
            temperature=0.3,
        )
        return response.content.strip()
