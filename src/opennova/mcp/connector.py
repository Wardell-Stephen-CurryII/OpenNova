"""
MCP Connector - Connect to and communicate with MCP servers.

Provides:
- MCPServer: Single MCP server connection
- MCPManager: Manage multiple MCP connections
- Tool discovery and execution
"""

import asyncio
import json
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from opennova.mcp.types import (
    MCPConnectionState,
    MCPMessage,
    MCPServerConfig,
    MCPServerInfo,
    MCPTool,
    MCPToolResult,
    TransportType,
)
from opennova.tools.base import BaseTool, ToolResult, ToolRegistry


class Transport(ABC):
    """Abstract base class for MCP transports."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to MCP server."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to MCP server."""
        pass

    @abstractmethod
    async def send(self, message: MCPMessage) -> None:
        """Send a message to the server."""
        pass

    @abstractmethod
    async def receive(self) -> MCPMessage:
        """Receive a message from the server."""
        pass

    @abstractmethod
    async def receive_stream(self) -> AsyncIterator[MCPMessage]:
        """Stream messages from the server."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        pass


class StdioTransport(Transport):
    """
    Transport for MCP servers using stdio.

    Launches a subprocess and communicates via stdin/stdout.
    """

    def __init__(self, config: MCPServerConfig):
        """Initialize stdio transport."""
        self.config = config
        self.process: subprocess.Popen | None = None
        self._reader_task: asyncio.Task | None = None
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._request_id = 0

    async def connect(self) -> None:
        """Launch the MCP server process."""
        if not self.config.command:
            raise ValueError("Command is required for stdio transport")

        env = os.environ.copy()
        env.update(self.config.env)

        self.process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        self._reader_task = asyncio.create_task(self._read_loop())

    async def disconnect(self) -> None:
        """Terminate the MCP server process."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            self.process = None

    async def _read_loop(self) -> None:
        """Continuously read messages from stdout."""
        if not self.process or not self.process.stdout:
            return

        buffer = ""
        while True:
            try:
                chunk = await self.process.stdout.read(4096)
                if not chunk:
                    break

                buffer += chunk.decode("utf-8")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            message = MCPMessage.from_dict(data)
                            await self._response_queue.put(message)
                        except json.JSONDecodeError:
                            pass

            except asyncio.CancelledError:
                break
            except Exception:
                break

    async def send(self, message: MCPMessage) -> None:
        """Send a message to the server."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Not connected to MCP server")

        data = message.to_dict()
        line = json.dumps(data) + "\n"
        self.process.stdin.write(line.encode("utf-8"))
        await self.process.stdin.drain()

    async def receive(self) -> MCPMessage:
        """Receive a message from the server."""
        return await self._response_queue.get()

    async def receive_stream(self) -> AsyncIterator[MCPMessage]:
        """Stream messages from the server."""
        while True:
            message = await self._response_queue.get()
            yield message

    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return self.process is not None and self.process.returncode is None

    def get_next_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id


class SSETransport(Transport):
    """
    Transport for MCP servers using Server-Sent Events.

    Connects to an HTTP endpoint and receives SSE events.
    """

    def __init__(self, config: MCPServerConfig):
        """Initialize SSE transport."""
        self.config = config
        self._connected = False
        self._request_id = 0

    async def connect(self) -> None:
        """Connect to SSE endpoint."""
        if not self.config.url:
            raise ValueError("URL is required for SSE transport")
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from SSE endpoint."""
        self._connected = False

    async def send(self, message: MCPMessage) -> None:
        """Send a message via HTTP POST."""
        import httpx

        if not self.config.url:
            raise RuntimeError("No URL configured")

        data = message.to_dict()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.config.url,
                json=data,
                timeout=self.config.timeout,
            )
            response.raise_for_status()

    async def receive(self) -> MCPMessage:
        """Receive a message (not applicable for SSE)."""
        raise NotImplementedError("SSE uses streaming only")

    async def receive_stream(self) -> AsyncIterator[MCPMessage]:
        """Stream SSE events."""
        import httpx

        if not self.config.url:
            raise RuntimeError("No URL configured")

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET",
                self.config.url.replace("/sse", "/messages"),
                timeout=self.config.timeout,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        yield MCPMessage.from_dict(data)

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def get_next_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id


class MCPConnector:
    """
    Connector for a single MCP server.

    Manages the connection lifecycle and provides
    tool discovery and execution capabilities.
    """

    def __init__(self, config: MCPServerConfig):
        """Initialize MCP connector."""
        self.config = config
        self.transport: Transport | None = None
        self.state = MCPConnectionState.DISCONNECTED
        self.server_info: MCPServerInfo | None = None
        self.tools: dict[str, MCPTool] = {}
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._request_id = 0
        self._listener_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Connect to the MCP server."""
        if self.state == MCPConnectionState.CONNECTED:
            return

        self.state = MCPConnectionState.CONNECTING

        try:
            if self.config.transport == TransportType.STDIO:
                self.transport = StdioTransport(self.config)
            elif self.config.transport == TransportType.SSE:
                self.transport = SSETransport(self.config)
            else:
                raise ValueError(f"Unsupported transport: {self.config.transport}")

            await self.transport.connect()

            self._listener_task = asyncio.create_task(self._listen_loop())

            self.server_info = await self._initialize()

            await self._discover_tools()

            self.state = MCPConnectionState.CONNECTED

        except Exception as e:
            self.state = MCPConnectionState.ERROR
            raise RuntimeError(f"Failed to connect to MCP server: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self.transport:
            await self.transport.disconnect()
            self.transport = None

        self.state = MCPConnectionState.DISCONNECTED
        self.tools.clear()

    async def _listen_loop(self) -> None:
        """Listen for incoming messages."""
        if not self.transport:
            return

        try:
            async for message in self.transport.receive_stream():
                if message.id is not None and message.id in self._pending_requests:
                    future = self._pending_requests.pop(message.id)
                    if message.error:
                        future.set_exception(RuntimeError(message.error))
                    else:
                        future.set_result(message.result)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _get_next_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        """Send a request and wait for response."""
        if not self.transport:
            raise RuntimeError("Not connected to MCP server")

        request_id = self._get_next_id()
        message = MCPMessage(
            id=request_id,
            method=method,
            params=params,
        )

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            await self.transport.send(message)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise RuntimeError(f"Request {method} timed out")
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            raise e

    async def _initialize(self) -> MCPServerInfo:
        """Initialize connection with the server."""
        result = await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {
                    "name": "OpenNova",
                    "version": "0.1.0",
                },
                "capabilities": {},
            },
        )

        # Wait for initialized notification from server
        # Note: According to MCP spec, server should send notifications/initialized
        # after receiving initialize request. We'll wait for it in the listen loop.
        # For now, we'll proceed without waiting for the notification.

        return MCPServerInfo.from_dict(result, self.config.name)

    async def _discover_tools(self) -> None:
        """Discover available tools from the server."""
        result = await self._send_request("tools/list")

        tools_data = result.get("tools", [])

        for tool_data in tools_data:
            tool = MCPTool(
                name=tool_data.get("name", ""),
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                server_name=self.config.name,
            )
            self.tools[tool.get_full_name()] = tool

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolResult:
        """Execute a tool on the MCP server."""
        if self.state != MCPConnectionState.CONNECTED:
            raise RuntimeError("Not connected to MCP server")

        try:
            result = await self._send_request(
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments,
                },
                timeout=self.config.timeout,
            )

            content_blocks = result.get("content", [])

            if isinstance(content_blocks, list):
                content = "\n".join(
                    block.get("text", str(block))
                    for block in content_blocks
                    if isinstance(block, dict)
                )
            else:
                content = str(content_blocks)

            is_error = result.get("isError", False)

            return MCPToolResult(
                success=not is_error,
                content=content,
                error=content if is_error else None,
            )

        except Exception as e:
            return MCPToolResult(
                success=False,
                content="",
                error=str(e),
            )

    def get_tools(self) -> list[MCPTool]:
        """Get list of available tools."""
        return list(self.tools.values())

    def is_connected(self) -> bool:
        """Check if connected."""
        return self.state == MCPConnectionState.CONNECTED


class MCPToolWrapper(BaseTool):
    """Wrapper to expose MCP tools as BaseTool."""

    def __init__(self, mcp_tool: MCPTool, connector: MCPConnector):
        """Initialize wrapper."""
        self.mcp_tool = mcp_tool
        self.connector = connector
        self.name = mcp_tool.get_full_name()
        self.description = mcp_tool.description
        self.parameters = mcp_tool.input_schema

    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the MCP tool."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.ensure_future(self._async_execute(**kwargs))
            return loop.run_until_complete(future)
        else:
            return loop.run_until_complete(self._async_execute(**kwargs))

    async def _async_execute(self, **kwargs: Any) -> ToolResult:
        """Async execution."""
        # Get the original tool name (without server prefix)
        original_name = self.mcp_tool.name
        result = await self.connector.call_tool(original_name, kwargs)

        return ToolResult(
            success=result.success,
            output=result.content,
            error=result.error,
            metadata=result.metadata,
        )


class MCPManager:
    """
    Manager for multiple MCP server connections.

    Features:
    - Connect to multiple MCP servers
    - Discover and register tools
    - Execute tools on appropriate servers
    """

    def __init__(self, tool_registry: ToolRegistry):
        """Initialize MCP manager."""
        self.tool_registry = tool_registry
        self.connectors: dict[str, MCPConnector] = {}

    async def add_server(self, config: MCPServerConfig) -> bool:
        """
        Add and connect to an MCP server.

        Args:
            config: Server configuration

        Returns:
            True if connection successful
        """
        if config.name in self.connectors:
            return self.connectors[config.name].is_connected()

        if not config.enabled:
            return False

        connector = MCPConnector(config)

        try:
            await connector.connect()
            self.connectors[config.name] = connector

            for mcp_tool in connector.get_tools():
                wrapper = MCPToolWrapper(mcp_tool, connector)
                self.tool_registry.register(wrapper)

            return True

        except Exception as e:
            print(f"Failed to connect to MCP server {config.name}: {e}")
            return False

    async def remove_server(self, name: str) -> None:
        """Disconnect and remove an MCP server."""
        if name in self.connectors:
            connector = self.connectors.pop(name)
            await connector.disconnect()

    async def connect_all(self, configs: list[MCPServerConfig]) -> dict[str, bool]:
        """
        Connect to all configured servers.

        Args:
            configs: List of server configurations

        Returns:
            Dict mapping server names to connection status
        """
        results = {}

        for config in configs:
            results[config.name] = await self.add_server(config)

        return results

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for name in list(self.connectors.keys()):
            await self.remove_server(name)

    def get_server_names(self) -> list[str]:
        """Get list of connected server names."""
        return list(self.connectors.keys())

    def get_all_tools(self) -> list[MCPTool]:
        """Get all tools from all connected servers."""
        tools = []
        for connector in self.connectors.values():
            tools.extend(connector.get_tools())
        return tools

    def get_server_for_tool(self, tool_name: str) -> MCPConnector | None:
        """Get the connector that provides a tool."""
        for connector in self.connectors.values():
            if tool_name in connector.tools:
                return connector
        return None
