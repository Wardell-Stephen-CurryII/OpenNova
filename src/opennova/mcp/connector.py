"""
MCP Connector - Connect to and communicate with MCP servers.

Provides:
- MCPServer: Single MCP server connection
- MCPManager: Manage multiple MCP connections
- Tool discovery and execution
"""

import asyncio
import inspect
import json
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

from opennova.mcp.types import (
    MCPConnectionState,
    MCPMessage,
    MCPResource,
    MCPResourceContent,
    MCPServerConfig,
    MCPServerInfo,
    MCPTool,
    MCPToolResult,
    TransportType,
)
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult

SUPPORTED_PROTOCOL_VERSION = "2024-11-05"


def _client_version() -> str:
    try:
        return version("opennova")
    except PackageNotFoundError:
        return "0.4.2"


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
    def receive_stream(self) -> AsyncIterator[MCPMessage]:
        """Stream messages from the server."""
        raise NotImplementedError

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
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._response_queue: asyncio.Queue[MCPMessage] = asyncio.Queue()
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
            with suppress(asyncio.CancelledError):
                await self._reader_task

        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except TimeoutError:
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
        self._client: httpx.AsyncClient | None = None

    def _message_url(self) -> str:
        if not self.config.url:
            raise RuntimeError("No URL configured")
        if self.config.url.endswith("/sse"):
            return self.config.url[:-4] + "/messages"
        raise RuntimeError("SSE transport URL must end with /sse")

    async def connect(self) -> None:
        """Connect to SSE endpoint."""
        if not self.config.url:
            raise ValueError("URL is required for SSE transport")
        import httpx

        self._client = httpx.AsyncClient(timeout=self.config.timeout)
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from SSE endpoint."""
        self._connected = False
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, message: MCPMessage) -> None:
        """Send a message via HTTP POST."""
        import httpx

        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self.config.timeout)
            self._client = client
        data = message.to_dict()

        response = await client.post(
            self._message_url(),
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
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self.config.timeout)
            self._client = client

        async with client.stream(
            "GET",
            self.config.url,
            timeout=self.config.timeout,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Invalid SSE payload from MCP server") from exc
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
        self._pending_requests: dict[int, asyncio.Future[Any]] = {}
        self._request_id = 0
        self._listener_task: asyncio.Task[None] | None = None
        self._initialized = asyncio.Event()
        self.last_error: str | None = None
        self.on_tools_changed: Callable[[], Any] | None = None
        self.roots_provider: Callable[[], list[dict[str, Any]]] | None = None
        self.elicitation_handler: Callable[[dict[str, Any]], Any] | None = None

    async def connect(self) -> None:
        """Connect to the MCP server."""
        if self.state == MCPConnectionState.CONNECTED:
            return

        self.state = MCPConnectionState.CONNECTING
        self._initialized = asyncio.Event()
        self.last_error = None

        try:
            if self.config.transport == TransportType.STDIO:
                transport: Transport = StdioTransport(self.config)
            elif self.config.transport == TransportType.SSE:
                transport = SSETransport(self.config)
            else:
                raise ValueError(f"Unsupported transport: {self.config.transport}")
            self.transport = transport

            await transport.connect()

            self._listener_task = asyncio.create_task(self._listen_loop())

            self.server_info = await self._initialize()

            await self._discover_tools()

            self.state = MCPConnectionState.CONNECTED

        except Exception as e:
            self.last_error = str(e)
            self.state = MCPConnectionState.ERROR
            self._fail_pending_requests(e)
            if self._listener_task:
                self._listener_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._listener_task
                self._listener_task = None
            if self.transport:
                try:
                    await self.transport.disconnect()
                finally:
                    self.transport = None
            raise RuntimeError(f"Failed to connect to MCP server: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        self._fail_pending_requests(RuntimeError("MCP server disconnected"))

        if self._listener_task:
            self._listener_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None

        if self.transport:
            await self.transport.disconnect()
            self.transport = None

        self.state = MCPConnectionState.DISCONNECTED
        self.tools.clear()

    async def _listen_loop(self) -> None:
        """Listen for incoming messages."""
        transport = self.transport
        if transport is None:
            return

        try:
            async for message in transport.receive_stream():
                if message.id is not None and message.id in self._pending_requests:
                    future = self._pending_requests.pop(message.id)
                    if future.done():
                        continue
                    if message.error:
                        future.set_exception(RuntimeError(message.error))
                    else:
                        future.set_result(message.result)
                    continue

                if message.method == "notifications/initialized":
                    self._initialized.set()
                    continue
                if message.method in {
                    "notifications/tools/list_changed",
                    "notifications/resources/list_changed",
                    "notifications/prompts/list_changed",
                }:
                    await self._handle_list_changed(message.method)
                    continue
                if message.id is not None and message.method:
                    await self._handle_server_request(message)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.last_error = str(e)
            self.state = MCPConnectionState.ERROR
            self._fail_pending_requests(e)

    def _fail_pending_requests(self, error: Exception) -> None:
        """Fail all pending requests with the provided error."""
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(error)
        self._pending_requests.clear()

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
        transport = self.transport
        if transport is None:
            raise RuntimeError("Not connected to MCP server")

        request_id = self._get_next_id()
        message = MCPMessage(
            id=request_id,
            method=method,
            params=params,
        )

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            await transport.send(message)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            with suppress(Exception):
                await asyncio.shield(
                    transport.send(
                        MCPMessage(
                            method="notifications/cancelled",
                            params={"requestId": request_id, "reason": "OpenNova run cancelled"},
                        )
                    )
                )
            raise
        except TimeoutError:
            if not future.done():
                future.cancel()
            raise RuntimeError(f"Request {method} timed out") from None
        finally:
            self._pending_requests.pop(request_id, None)

    async def _initialize(self) -> MCPServerInfo:
        """Initialize connection with the server."""
        result = await self._send_request(
            "initialize",
            {
                "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
                "clientInfo": {
                    "name": "OpenNova",
                    "version": _client_version(),
                },
                "capabilities": {
                    "roots": {"listChanged": True},
                    "elicitation": {},
                },
            },
        )
        transport = self.transport
        if transport is None:
            raise RuntimeError("MCP transport disconnected during initialization")
        await transport.send(MCPMessage(method="notifications/initialized", params={}))
        self._initialized.set()
        return MCPServerInfo.from_dict(result, self.config.name)

    async def _discover_tools(self) -> None:
        """Discover available tools from the server."""
        tools_data = await self._list_paginated("tools/list", "tools")
        self.tools.clear()
        for tool_data in tools_data:
            tool = MCPTool(
                name=tool_data.get("name", ""),
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                server_name=self.config.name,
            )
            self.tools[tool.get_full_name()] = tool

    async def _list_paginated(
        self,
        method: str,
        key: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Collect every cursor page for an MCP list method."""
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            request_params = dict(params or {})
            if cursor:
                request_params["cursor"] = cursor
            result = await self._send_request(
                method,
                request_params or None,
                timeout=self.config.timeout,
            )
            page = result.get(key, []) if isinstance(result, dict) else []
            items.extend(item for item in page if isinstance(item, dict))
            next_cursor = result.get("nextCursor") if isinstance(result, dict) else None
            if not next_cursor or str(next_cursor) in seen:
                break
            cursor = str(next_cursor)
            seen.add(cursor)
        return items

    async def _handle_list_changed(self, method: str) -> None:
        if method == "notifications/tools/list_changed":
            await self._discover_tools()
            if self.on_tools_changed:
                result = self.on_tools_changed()
                if inspect.isawaitable(result):
                    await result

    async def _handle_server_request(self, message: MCPMessage) -> None:
        transport = self.transport
        if transport is None:
            return
        try:
            if message.method == "roots/list":
                roots = self.roots_provider() if self.roots_provider else []
                result: Any = {"roots": roots}
            elif message.method == "elicitation/create":
                if self.elicitation_handler is None:
                    result = {"action": "decline"}
                else:
                    result = self.elicitation_handler(message.params or {})
                    if inspect.isawaitable(result):
                        result = await result
            else:
                await transport.send(
                    MCPMessage(
                        id=message.id,
                        error={
                            "code": -32601,
                            "message": f"Method not supported: {message.method}",
                        },
                    )
                )
                return
            await transport.send(MCPMessage(id=message.id, result=result))
        except Exception as exc:
            await transport.send(
                MCPMessage(
                    id=message.id,
                    error={"code": -32603, "message": str(exc)},
                )
            )

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

    async def list_resources(self) -> list[MCPResource]:
        """List resources advertised by the MCP server."""
        if self.state != MCPConnectionState.CONNECTED:
            raise RuntimeError("Not connected to MCP server")

        resources_data = await self._list_paginated("resources/list", "resources")
        resources = []
        for resource_data in resources_data:
            resources.append(
                MCPResource(
                    uri=resource_data.get("uri", ""),
                    name=resource_data.get("name", ""),
                    description=resource_data.get("description", ""),
                    mime_type=resource_data.get("mimeType", resource_data.get("mime_type", "")),
                    server_name=self.config.name,
                    metadata={
                        key: value
                        for key, value in resource_data.items()
                        if key not in {"uri", "name", "description", "mimeType", "mime_type"}
                    },
                )
            )
        return resources

    async def list_resource_templates(self) -> list[dict[str, Any]]:
        """List every resource template advertised by the server."""
        return await self._list_paginated("resources/templates/list", "resourceTemplates")

    async def list_prompts(self) -> list[dict[str, Any]]:
        """List every prompt advertised by the server."""
        return await self._list_paginated("prompts/list", "prompts")

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Render a named server prompt."""
        result = await self._send_request(
            "prompts/get",
            {"name": name, "arguments": arguments or {}},
            timeout=self.config.timeout,
        )
        return result if isinstance(result, dict) else {}

    async def read_resource(self, uri: str) -> MCPResourceContent:
        """Read a resource by URI from the MCP server."""
        if self.state != MCPConnectionState.CONNECTED:
            raise RuntimeError("Not connected to MCP server")

        try:
            result = await self._send_request(
                "resources/read",
                {"uri": uri},
                timeout=self.config.timeout,
            )
            contents = result.get("contents", [])
            rendered_parts: list[str] = []
            metadata: dict[str, Any] = {"server_name": self.config.name, "uri": uri}

            if isinstance(contents, list):
                for block in contents:
                    if not isinstance(block, dict):
                        rendered_parts.append(str(block))
                        continue
                    if block.get("text") is not None:
                        rendered_parts.append(str(block.get("text", "")))
                    elif block.get("blob") is not None:
                        rendered_parts.append(str(block.get("blob", "")))
                    if block.get("mimeType") or block.get("mime_type"):
                        metadata["mime_type"] = block.get("mimeType", block.get("mime_type"))
            else:
                rendered_parts.append(str(contents))

            return MCPResourceContent(
                success=True,
                content="\n".join(rendered_parts),
                metadata=metadata,
            )
        except Exception as e:
            return MCPResourceContent(
                success=False,
                content="",
                error=str(e),
                metadata={"server_name": self.config.name, "uri": uri},
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
        """Execute the MCP tool in synchronous contexts only."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_execute(**kwargs))
        raise RuntimeError("MCP tools must be executed via async_execute inside the runtime loop")

    def get_security_context(self) -> dict[str, Any]:
        """Return guardrails context for this MCP-provided tool."""
        config = self.connector.config
        return {
            "kind": "mcp",
            "server": config.name,
            "tool": self.mcp_tool.name,
            "trusted": config.trusted,
            "allowed_tools": list(config.allowed_tools),
            "denied_tools": list(config.denied_tools),
            "require_confirmation": config.require_confirmation,
        }

    async def async_execute(self, **kwargs: Any) -> ToolResult:
        """Async execution."""
        from opennova.runtime.events import current_tool_context

        context = current_tool_context()
        if context and context.abort_signal:
            context.abort_signal.raise_if_cancelled()
        original_name = self.mcp_tool.name
        result = await self.connector.call_tool(original_name, kwargs)

        return ToolResult(
            success=result.success,
            output=result.content,
            error=result.error,
            metadata={
                **result.metadata,
                "mcp_server": self.connector.config.name,
                "mcp_tool": self.mcp_tool.name,
                "mcp_trusted": self.connector.config.trusted,
            },
        )


class MCPManager:
    """
    Manager for multiple MCP server connections.

    Features:
    - Connect to multiple MCP servers
    - Discover and register tools
    - Execute tools on appropriate servers
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        roots: list[dict[str, Any]] | None = None,
        elicitation_handler: Callable[[dict[str, Any]], Any] | None = None,
    ):
        """Initialize MCP manager."""
        self.tool_registry = tool_registry
        self.connectors: dict[str, MCPConnector] = {}
        self._registered_tools_by_server: dict[str, list[str]] = {}
        self.connection_errors: dict[str, str] = {}
        self.roots = list(roots or [])
        self.elicitation_handler = elicitation_handler

    def set_roots(self, roots: list[dict[str, Any]]) -> None:
        """Replace the workspace roots exposed to connected MCP servers."""
        self.roots = list(roots)

    def set_elicitation_handler(
        self,
        handler: Callable[[dict[str, Any]], Any] | None,
    ) -> None:
        """Set the user-interaction handler for MCP elicitation requests."""
        self.elicitation_handler = handler

    def _connector_roots(self) -> list[dict[str, Any]]:
        return list(self.roots)

    async def _sync_server_tools(self, server_name: str) -> None:
        """Atomically refresh registry wrappers for one connected server."""
        connector = self.connectors.get(server_name)
        if connector is None:
            return

        previous_names = self._registered_tools_by_server.get(server_name, [])
        for tool_name in previous_names:
            self.tool_registry.unregister(tool_name)

        registered_tools: list[str] = []
        for mcp_tool in connector.get_tools():
            wrapper = MCPToolWrapper(mcp_tool, connector)
            self.tool_registry.register(wrapper)
            registered_tools.append(wrapper.name)
        self._registered_tools_by_server[server_name] = registered_tools

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
            self.connection_errors.pop(config.name, None)
            return False

        connector = MCPConnector(config)
        connector.roots_provider = self._connector_roots
        connector.elicitation_handler = lambda params: (
            self.elicitation_handler(params)
            if self.elicitation_handler is not None
            else {"action": "decline"}
        )
        connector.on_tools_changed = lambda: self._sync_server_tools(config.name)

        try:
            await connector.connect()
            self.connectors[config.name] = connector
            self.connection_errors.pop(config.name, None)
            await self._sync_server_tools(config.name)

            return True

        except Exception as e:
            self.connection_errors[config.name] = str(e)
            return False

    async def remove_server(self, name: str) -> None:
        """Disconnect and remove an MCP server."""
        for tool_name in self._registered_tools_by_server.pop(name, []):
            self.tool_registry.unregister(tool_name)

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

    async def list_resources(self, server_name: str | None = None) -> list[MCPResource]:
        """List resources from one or all connected MCP servers."""
        resources: list[MCPResource] = []
        connectors = (
            [self.connectors[server_name]]
            if server_name and server_name in self.connectors
            else list(self.connectors.values())
        )
        for connector in connectors:
            resources.extend(await connector.list_resources())
        return resources

    async def list_resource_templates(
        self,
        server_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """List resource templates with their server provenance."""
        templates: list[dict[str, Any]] = []
        connectors = self._selected_connectors(server_name)
        for connector in connectors:
            for template in await connector.list_resource_templates():
                templates.append({**template, "server_name": connector.config.name})
        return templates

    async def list_prompts(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """List prompts with their server provenance."""
        prompts: list[dict[str, Any]] = []
        connectors = self._selected_connectors(server_name)
        for connector in connectors:
            for prompt in await connector.list_prompts():
                prompts.append({**prompt, "server_name": connector.config.name})
        return prompts

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str] | None = None,
        server_name: str | None = None,
    ) -> dict[str, Any]:
        """Render a prompt on a selected or first connected server."""
        connectors = self._selected_connectors(server_name)
        if not connectors:
            raise RuntimeError(
                f"MCP server not connected: {server_name}"
                if server_name
                else "No MCP servers connected"
            )
        return await connectors[0].get_prompt(name, arguments)

    def _selected_connectors(self, server_name: str | None) -> list[MCPConnector]:
        if server_name is None:
            return list(self.connectors.values())
        connector = self.connectors.get(server_name)
        return [connector] if connector is not None else []

    async def read_resource(
        self,
        uri: str,
        server_name: str | None = None,
    ) -> MCPResourceContent:
        """Read a resource from a specific server or the first server that can provide it."""
        if server_name:
            connector = self.connectors.get(server_name)
            if not connector:
                return MCPResourceContent(
                    False, "", error=f"MCP server not connected: {server_name}"
                )
            return await connector.read_resource(uri)

        last_error = ""
        for connector in self.connectors.values():
            result = await connector.read_resource(uri)
            if result.success:
                return result
            last_error = result.error or last_error
        return MCPResourceContent(False, "", error=last_error or f"MCP resource not found: {uri}")

    def get_server_for_tool(self, tool_name: str) -> MCPConnector | None:
        """Get the connector that provides a tool."""
        for connector in self.connectors.values():
            if tool_name in connector.tools:
                return connector
        return None
