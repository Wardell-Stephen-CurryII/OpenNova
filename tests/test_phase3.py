"""Tests for Phase 3 modules (MCP and Skills)."""

import pytest
import tempfile
from pathlib import Path
import os

from opennova.mcp.types import (
    MCPServerConfig,
    MCPTool,
    MCPToolResult,
    MCPMessage,
    TransportType,
)
from opennova.skills.base import BaseSkill, SkillMetadata, SkillLoader
from opennova.skills.registry import SkillRegistry
from opennova.tools.base import ToolResult


class TestMCPTypes:
    """Tests for MCP type definitions."""

    def test_server_config_from_dict(self):
        """Test creating server config from dict."""
        data = {
            "name": "test_server",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "test-mcp-server"],
        }

        config = MCPServerConfig.from_dict(data)

        assert config.name == "test_server"
        assert config.transport == TransportType.STDIO
        assert config.command == "npx"
        assert config.args == ["-y", "test-mcp-server"]

    def test_server_config_to_dict(self):
        """Test serializing server config."""
        config = MCPServerConfig(
            name="test",
            transport=TransportType.SSE,
            url="http://localhost:3000/sse",
        )

        data = config.to_dict()

        assert data["name"] == "test"
        assert data["transport"] == "sse"
        assert data["url"] == "http://localhost:3000/sse"

    def test_mcp_tool(self):
        """Test MCP tool definition."""
        tool = MCPTool(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            server_name="filesystem",
        )

        full_name = tool.get_full_name()
        assert full_name == "filesystem_read_file"

        schema = tool.to_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "filesystem_read_file"

    def test_mcp_tool_result(self):
        """Test MCP tool result."""
        result = MCPToolResult(
            success=True,
            content="File contents here",
        )

        assert result.to_string() == "File contents here"

        error_result = MCPToolResult(
            success=False,
            content="",
            error="File not found",
        )

        assert "Error: File not found" in error_result.to_string()

    def test_mcp_message(self):
        """Test MCP message."""
        msg = MCPMessage(
            id=1,
            method="tools/list",
            params={},
        )

        data = msg.to_dict()

        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert data["method"] == "tools/list"

        parsed = MCPMessage.from_dict(data)
        assert parsed.id == msg.id
        assert parsed.method == msg.method


class TestSkills:
    """Tests for Skills system."""

    def test_skill_metadata(self):
        """Test skill metadata."""
        metadata = SkillMetadata(
            name="test_skill",
            version="1.0.0",
            description="A test skill",
            author="Test Author",
            tags=["test"],
        )

        data = metadata.to_dict()
        assert data["name"] == "test_skill"
        assert data["version"] == "1.0.0"

        parsed = SkillMetadata.from_dict(data)
        assert parsed.name == metadata.name

    def test_base_skill(self):
        """Test base skill execution."""

        class TestSkill(BaseSkill):
            name = "test_skill"
            description = "Test skill"

            def execute(self, message: str) -> ToolResult:
                return ToolResult(success=True, output=f"Processed: {message}")

        skill = TestSkill()
        result = skill.execute(message="hello")

        assert result.success
        assert result.output == "Processed: hello"

    def test_skill_registry(self):
        """Test skill registry."""

        class DummySkill(BaseSkill):
            name = "dummy"
            description = "Dummy skill"

            def execute(self) -> ToolResult:
                return ToolResult(success=True, output="done")

        registry = SkillRegistry()
        skill = DummySkill()

        registry.register(skill)

        assert "dummy" in registry
        assert registry.is_enabled("dummy")

        registry.disable_skill("dummy")
        assert not registry.is_enabled("dummy")

        registry.enable_skill("dummy")
        assert registry.is_enabled("dummy")

    def test_skill_loader_discovery(self):
        """Test skill file discovery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "skills"
            skill_dir.mkdir()

            skill_file = skill_dir / "test_skill.py"
            skill_file.write_text('''
from opennova.skills.base import BaseSkill, SkillMetadata
from opennova.tools.base import ToolResult

class TestSkill(BaseSkill):
    name = "test"
    description = "Test"

    def execute(self) -> ToolResult:
        return ToolResult(success=True, output="test")
''')

            skills = SkillLoader.load_skill_file(skill_file)

            assert len(skills) == 1
            assert skills[0].metadata is not None or skills[0].skill_class is not None

    def test_skill_loader_create_template(self):
        """Test creating skill template."""
        with tempfile.TemporaryDirectory() as tmpdir:
            template_path = SkillLoader.create_skill_template("my_cool_skill", tmpdir)

            assert template_path.exists()
            assert template_path.name == "my_cool_skill.py"

            content = template_path.read_text()
            assert "MyCoolSkill" in content
            assert "my_cool_skill" in content


class TestSkillIntegration:
    """Tests for skill integration with agent."""

    def test_skill_in_registry(self):
        """Test that skills can be added to tool registry."""
        from opennova.tools.base import ToolRegistry

        class HelloSkill(BaseSkill):
            name = "hello"
            description = "Say hello"

            def execute(self, name: str = "World") -> ToolResult:
                return ToolResult(success=True, output=f"Hello, {name}!")

        registry = ToolRegistry()
        skill = HelloSkill()

        registry.register(skill)

        assert registry.has_tool("hello")

        tool = registry.get("hello")
        result = tool.execute(name="OpenNova")

        assert result.success
        assert result.output == "Hello, OpenNova!"
