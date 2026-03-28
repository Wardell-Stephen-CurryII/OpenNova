"""Basic tests for OpenNova."""

import pytest

from opennova.tools.base import ToolRegistry, ToolResult, BaseTool
from opennova.providers.base import Message, ToolSchema


class MockTool(BaseTool):
    """Mock tool for testing."""

    name = "mock_tool"
    description = "A mock tool for testing"

    def execute(self, **kwargs):
        return ToolResult(success=True, output="Mock result")


def test_tool_registry():
    """Test tool registration and retrieval."""
    registry = ToolRegistry()
    tool = MockTool()

    registry.register(tool)

    assert registry.has_tool("mock_tool")
    assert registry.get("mock_tool") == tool
    assert "mock_tool" in registry.list_names()


def test_tool_result():
    """Test tool result output."""
    success_result = ToolResult(success=True, output="Done")
    assert success_result.to_string() == "Done"

    error_result = ToolResult(success=False, output="", error="Failed")
    assert "Error: Failed" in error_result.to_string()


def test_message_to_openai_format():
    """Test message conversion to OpenAI format."""
    msg = Message(role="user", content="Hello")
    openai_msg = msg.to_openai_format()

    assert openai_msg["role"] == "user"
    assert openai_msg["content"] == "Hello"


def test_tool_schema():
    """Test tool schema generation."""
    tool = MockTool()
    schema = tool.get_schema()

    assert schema.name == "mock_tool"
    assert schema.description == "A mock tool for testing"
    assert "properties" in schema.parameters
