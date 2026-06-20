"""
MCP (Model Context Protocol) Integration.

This module provides:
- MCPConnector: Connect to MCP servers
- Transport layer support (stdio, SSE)
- Tool discovery and execution
"""

from opennova.mcp.connector import MCPConnector, MCPManager
from opennova.mcp.types import (
    MCPConnectionState,
    MCPResource,
    MCPResourceContent,
    MCPServerConfig,
    MCPTool,
    MCPToolResult,
)

__all__ = [
    "MCPServerConfig",
    "MCPTool",
    "MCPToolResult",
    "MCPResource",
    "MCPResourceContent",
    "MCPConnectionState",
    "MCPConnector",
    "MCPManager",
]
