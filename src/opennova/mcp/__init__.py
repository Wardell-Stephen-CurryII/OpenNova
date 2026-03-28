"""
MCP (Model Context Protocol) Integration.

This module provides:
- MCPConnector: Connect to MCP servers
- Transport layer support (stdio, SSE)
- Tool discovery and execution
"""

from opennova.mcp.types import (
    MCPServerConfig,
    MCPTool,
    MCPToolResult,
    MCPConnectionState,
)
from opennova.mcp.connector import MCPConnector, MCPManager

__all__ = [
    "MCPServerConfig",
    "MCPTool",
    "MCPToolResult",
    "MCPConnectionState",
    "MCPConnector",
    "MCPManager",
]
