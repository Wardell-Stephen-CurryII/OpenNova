"""Tests for MCP resource discovery and reading."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from opennova.mcp.connector import MCPConnector
from opennova.mcp.types import MCPConnectionState, MCPResource
from opennova.tools.base import ToolRegistry


class RequestRecorder:
    def __init__(self):
        self.requests: list[tuple[str, dict | None]] = []

    async def send_request(self, method: str, params: dict | None = None, timeout: float = 30.0):
        self.requests.append((method, params))
        if method == "resources/list":
            return {
                "resources": [
                    {
                        "uri": "file://README.md",
                        "name": "README",
                        "description": "Project readme",
                        "mimeType": "text/markdown",
                    }
                ]
            }
        if method == "resources/read":
            return {
                "contents": [
                    {
                        "uri": params["uri"],
                        "mimeType": "text/markdown",
                        "text": "# README\n",
                    }
                ]
            }
        raise AssertionError(f"Unexpected method: {method}")


@pytest.mark.asyncio
async def test_mcp_connector_lists_and_reads_resources():
    connector = MCPConnector.__new__(MCPConnector)
    connector.config = type("Config", (), {"name": "docs", "timeout": 30.0})()
    connector.state = MCPConnectionState.CONNECTED
    recorder = RequestRecorder()
    connector._send_request = recorder.send_request

    resources = await connector.list_resources()
    content = await connector.read_resource("file://README.md")

    assert resources == [
        MCPResource(
            uri="file://README.md",
            name="README",
            description="Project readme",
            mime_type="text/markdown",
            server_name="docs",
        )
    ]
    assert content.success is True
    assert content.content == "# README\n"
    assert recorder.requests[-1] == ("resources/read", {"uri": "file://README.md"})


@pytest.mark.asyncio
async def test_mcp_resource_tools_use_manager_and_return_structured_metadata():
    from opennova.tools.mcp_resource_tools import ListMCPResourcesTool, ReadMCPResourceTool

    class FakeManager:
        async def list_resources(self, server_name: str | None = None):
            return [
                MCPResource(
                    uri="file://README.md",
                    name="README",
                    description="Project readme",
                    mime_type="text/markdown",
                    server_name="docs",
                )
            ]

        async def read_resource(self, uri: str, server_name: str | None = None):
            assert uri == "file://README.md"
            return type(
                "ResourceContent",
                (),
                {
                    "success": True,
                    "content": "# README\n",
                    "error": None,
                    "metadata": {"server_name": "docs", "uri": uri, "mime_type": "text/markdown"},
                },
            )()

    manager = FakeManager()
    list_result = await ListMCPResourcesTool(config={"mcp_manager": manager}).async_execute()
    read_result = await ReadMCPResourceTool(config={"mcp_manager": manager}).async_execute(
        "file://README.md"
    )

    assert list_result.success is True
    assert "docs: README" in list_result.output
    assert list_result.metadata["resources"][0]["uri"] == "file://README.md"
    assert read_result.success is True
    assert read_result.output == "# README\n"
    assert read_result.metadata["server_name"] == "docs"


def test_runtime_registers_mcp_resource_tools_when_mcp_disabled(tmp_path: Path, monkeypatch):
    from opennova.runtime.agent import AgentRuntime

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    runtime = AgentRuntime(
        {
            "default_provider": "deepseek",
            "providers": {"deepseek": {"api_key": "test-key", "default_model": "deepseek-v4-pro"}},
            "mcp": {"enabled": False, "servers": []},
            "skills": {"enabled": False, "dirs": []},
        },
        enable_mcp=False,
        enable_skills=False,
    )

    registry = ToolRegistry()
    runtime_tools = set(runtime.get_tools())
    assert "list_mcp_resources" in runtime_tools
    assert "read_mcp_resource" in runtime_tools
    assert "list_mcp_resources" not in registry.list_names()
    asyncio.run(runtime.aclose())
