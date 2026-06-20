"""Tools for listing and reading MCP resources."""

from __future__ import annotations

import asyncio
from typing import Any

from opennova.tools.base import BaseTool, ToolResult


class _MCPResourceTool(BaseTool):
    """Shared helpers for MCP resource tools."""

    def _manager(self) -> Any:
        manager = self.config.get("mcp_manager")
        if manager is None and self.config.get("runtime") is not None:
            manager = getattr(self.config["runtime"], "mcp_manager", None)
        if manager is None:
            raise RuntimeError("MCP manager is not available")
        return manager

    def is_read_only(self, **kwargs: Any) -> bool:
        return True


class ListMCPResourcesTool(_MCPResourceTool):
    """List resources exposed by connected MCP servers."""

    name = "list_mcp_resources"
    search_hint = "List resources exposed by connected MCP servers"
    description = "List resources exposed by connected MCP servers. Returns URI, name, server, and MIME type."

    def execute(self, server_name: str | None = None) -> ToolResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_execute(server_name=server_name))
        raise RuntimeError("list_mcp_resources must be executed via async_execute inside the runtime loop")

    async def async_execute(self, server_name: str | None = None) -> ToolResult:
        try:
            resources = await self._manager().list_resources(server_name=server_name)
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

        if not resources:
            return ToolResult(success=True, output="No MCP resources available.", metadata={"resources": []})

        lines = []
        for resource in resources:
            label = resource.name or resource.uri
            server_prefix = f"{resource.server_name}: " if resource.server_name else ""
            detail = f" ({resource.mime_type})" if resource.mime_type else ""
            lines.append(f"- {server_prefix}{label}{detail}\n  URI: {resource.uri}")
            if resource.description:
                lines.append(f"  Description: {resource.description}")

        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={"resources": [resource.to_dict() for resource in resources]},
        )


class ReadMCPResourceTool(_MCPResourceTool):
    """Read a resource exposed by an MCP server."""

    name = "read_mcp_resource"
    search_hint = "Read a resource exposed by a connected MCP server"
    description = "Read a resource by URI from a connected MCP server. Optionally pass server_name to disambiguate."

    def execute(self, uri: str, server_name: str | None = None) -> ToolResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_execute(uri=uri, server_name=server_name))
        raise RuntimeError("read_mcp_resource must be executed via async_execute inside the runtime loop")

    async def async_execute(self, uri: str, server_name: str | None = None) -> ToolResult:
        if not uri:
            return ToolResult(success=False, output="", error="uri must not be empty")

        try:
            result = await self._manager().read_resource(uri, server_name=server_name)
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

        return ToolResult(
            success=result.success,
            output=result.content,
            error=result.error,
            metadata=result.metadata,
        )
