"""
MCP Types - Data structures for MCP protocol.

Defines the types used in MCP communication:
- MCPServerConfig: Server connection configuration
- MCPTool: Tool definition from MCP server
- MCPToolResult: Result from tool execution
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TransportType(str, Enum):
    """MCP transport types."""

    STDIO = "stdio"
    SSE = "sse"
    WEBSOCKET = "websocket"


class MCPConnectionState(str, Enum):
    """MCP connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class MCPServerConfig:
    """
    Configuration for an MCP server connection.

    Supports multiple transport types:
    - stdio: Launch a subprocess and communicate via stdin/stdout
    - sse: Connect via Server-Sent Events
    - websocket: Connect via WebSocket
    """

    name: str
    transport: TransportType = TransportType.STDIO
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPServerConfig":
        """Create config from dictionary."""
        transport = TransportType(data.get("transport", "stdio"))

        return cls(
            name=data["name"],
            transport=transport,
            command=data.get("command"),
            args=data.get("args", []),
            url=data.get("url"),
            env=data.get("env", {}),
            timeout=data.get("timeout", 30.0),
            enabled=data.get("enabled", True),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "transport": self.transport.value,
            "command": self.command,
            "args": self.args,
            "url": self.url,
            "env": self.env,
            "timeout": self.timeout,
            "enabled": self.enabled,
        }


@dataclass
class MCPToolParameter:
    """Parameter definition for an MCP tool."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema format."""
        schema: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass
class MCPTool:
    """Tool definition from an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""
    annotations: dict[str, Any] = field(default_factory=dict)

    def get_full_name(self) -> str:
        """Get fully qualified tool name."""
        if self.server_name:
            return f"{self.server_name}_{self.name}"
        return self.name

    def to_tool_schema(self) -> dict[str, Any]:
        """Convert to OpenAI tool schema format."""
        return {
            "type": "function",
            "function": {
                "name": self.get_full_name(),
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class MCPToolResult:
    """Result from executing an MCP tool."""

    success: bool
    content: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_string(self) -> str:
        """Convert to string representation."""
        if self.success:
            return self.content
        return f"Error: {self.error}\n{self.content}"


@dataclass
class MCPMessage:
    """Message in MCP protocol."""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    result: Any | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data: dict[str, Any] = {"jsonrpc": self.jsonrpc}
        if self.id is not None:
            data["id"] = self.id
        if self.method:
            data["method"] = self.method
        if self.params is not None:
            data["params"] = self.params
        if self.result is not None:
            data["result"] = self.result
        if self.error is not None:
            data["error"] = self.error
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPMessage":
        """Create from dictionary."""
        return cls(
            jsonrpc=data.get("jsonrpc", "2.0"),
            id=data.get("id"),
            method=data.get("method"),
            params=data.get("params"),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass
class MCPServerInfo:
    """Information about an MCP server."""

    name: str
    version: str = ""
    protocol_version: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    instructions: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], server_name: str) -> "MCPServerInfo":
        """Create from initialize result."""
        return cls(
            name=server_name,
            version=data.get("serverInfo", {}).get("version", ""),
            protocol_version=data.get("protocolVersion", ""),
            capabilities=data.get("capabilities", {}),
            instructions=data.get("instructions", ""),
        )
