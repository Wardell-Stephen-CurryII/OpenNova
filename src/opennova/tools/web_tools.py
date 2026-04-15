"""
Web Tools - WebSearch and WebFetch tools for external information retrieval.

Provides:
- WebSearch: Search the web for current information
- WebFetch: Fetch and extract content from web pages
- Source tracking for proper attribution
"""

import asyncio
import re
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

from opennova.tools.base import BaseTool, ToolResult


class WebSearchTool(BaseTool):
    """Search the web for up-to-date information."""

    name = "web_search"
    description = "Search the web and use results to inform responses. Use this for accessing information beyond the knowledge cutoff, current events, recent data, or documentation updates."

    def execute(
        self,
        query: str,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        num_results: int = 10,
        **kwargs: Any,
    ) -> ToolResult:
        current_year = datetime.now().year
        return ToolResult(
            success=False,
            output="",
            error="Web search is not configured in this runtime.",
            metadata={
                "query": query,
                "allowed_domains": allowed_domains or [],
                "blocked_domains": blocked_domains or [],
                "requested_count": num_results,
                "count": 0,
                "current_year": current_year,
            },
        )


class WebFetchTool(BaseTool):
    """Fetch and extract content from web pages."""

    name = "web_fetch"
    description = "Fetch content from a web URL. Use this to retrieve specific pages, documentation, or resources that were referenced in search results or by the user."
    _max_output_chars = 4000

    def execute(self, url: str, **kwargs: Any) -> ToolResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_execute(url=url, **kwargs))
        raise RuntimeError("web_fetch must be executed via async_execute inside the runtime loop")

    async def async_execute(self, url: str, **kwargs: Any) -> ToolResult:
        try:
            parsed = urlparse(url)
            if not all([parsed.scheme, parsed.netloc]):
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Invalid URL: {url}",
                )

            timeout = float(kwargs.get("timeout", self.config.get("timeout", 15.0)))
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                response = await client.get(url)
                response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            extracted = self._extract_content(response.text, content_type)
            if len(extracted) > self._max_output_chars:
                extracted = extracted[: self._max_output_chars] + "\n... [truncated]"

            final_url = str(response.url)
            return ToolResult(
                success=True,
                output=extracted,
                metadata={
                    "url": url,
                    "final_url": final_url,
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "fetched_at": datetime.now().isoformat(),
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def _extract_content(self, content: str, content_type: str) -> str:
        if "html" not in content_type.lower():
            return content.strip()

        text = re.sub(r"<script[\s\S]*?</script>", " ", content, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|section|article|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = unescape(text)
        text = text.replace("\r", "")
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()
