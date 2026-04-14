"""Tests for Phase 3 modules (MCP and Skills)."""

import asyncio
import pytest
import tempfile
from pathlib import Path
import os

from opennova.mcp.connector import MCPConnector, MCPManager, MCPToolWrapper
from opennova.mcp.types import (
    MCPConnectionState,
    MCPServerConfig,
    MCPTool,
    MCPToolResult,
    MCPMessage,
    TransportType,
)
from opennova.providers.base import FinishReason, LLMResponse
from opennova.skills.base import BaseSkill, SkillMetadata, SkillLoader
from opennova.skills.examples import get_builtin_skill_classes
from opennova.skills.registry import SkillRegistry
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.loop import ReActLoop
from opennova.runtime.state import AgentState
from opennova.tools.base import ToolRegistry, ToolResult


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

    def test_skill_registry_load_all_includes_builtins_and_exclusions(self):
        """Canonical registry loading should include builtins and respect exclusions."""
        registry = SkillRegistry()

        loaded = registry.load_all(
            builtins=get_builtin_skill_classes(),
            excluded=["git_helper"],
        )

        assert "code_review" in loaded
        assert "git_helper" in loaded
        assert registry.get_skill_info("code_review")["source_type"] == "builtin"
        assert registry.get_skill_info("git_helper")["source_type"] == "builtin"
        assert registry.is_enabled("code_review") is True
        assert registry.is_enabled("git_helper") is False

    def test_skill_registry_load_all_marks_discovered_skills_disabled_when_excluded(self):
        """Canonical loading should keep excluded discovered skills registered but disabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / "custom_skill.py"
            skill_file.write_text('''
from opennova.skills.base import BaseSkill
from opennova.tools.base import ToolResult

class CustomSkill(BaseSkill):
    name = "custom"
    description = "Custom"

    def execute(self) -> ToolResult:
        return ToolResult(success=True, output="custom")
''')

            registry = SkillRegistry()
            loaded = registry.load_all(directories=[tmpdir], builtins=[], excluded=["custom"])

            assert "custom" in loaded
            assert "custom" in registry.list_skills()
            assert registry.get_skill_info("custom")["source_type"] == "discovered"
            assert registry.is_enabled("custom") is False

    def test_skill_registry_load_all_replaces_previous_discovered_skills(self):
        """Reloading through the canonical path should replace prior discovered skills."""
        with tempfile.TemporaryDirectory() as tmpdir:
            first_file = Path(tmpdir) / "first_skill.py"
            first_file.write_text('''
from opennova.skills.base import BaseSkill
from opennova.tools.base import ToolResult

class FirstSkill(BaseSkill):
    name = "first"
    description = "First"

    def execute(self) -> ToolResult:
        return ToolResult(success=True, output="first")
''')

            registry = SkillRegistry()
            registry.load_all(directories=[tmpdir], builtins=[])
            assert "first" in registry.list_skills()

            first_file.unlink()

            second_file = Path(tmpdir) / "second_skill.py"
            second_file.write_text('''
from opennova.skills.base import BaseSkill
from opennova.tools.base import ToolResult

class SecondSkill(BaseSkill):
    name = "second"
    description = "Second"

    def execute(self) -> ToolResult:
        return ToolResult(success=True, output="second")
''')

            registry.load_all(directories=[tmpdir], builtins=[])

            assert "first" not in registry.list_skills()
            assert "second" in registry.list_skills()
            assert registry.get_skill_info("second")["source_type"] == "discovered"




class TestMCPRuntimeIntegration:
    """Tests for MCP runtime integration."""

    @pytest.mark.asyncio
    async def test_runtime_act_mode_lazily_connects_mcp_servers(self):
        class DummyProvider:
            model = "gpt-4o"

            async def chat(self, messages, tools=None, **kwargs):
                return LLMResponse(content="done", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

            def get_model_info(self):
                return {"model": self.model}

        async def connect_all(configs):
            runtime._connect_called_with = [config.name for config in configs]
            return {config.name: True for config in configs}

        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.state = AgentState()
        runtime.tool_registry = ToolRegistry()
        runtime.max_iterations = 5
        runtime.show_thinking = False
        runtime._callbacks = {}
        runtime.llm = DummyProvider()
        from opennova.memory.context import ContextManager
        from opennova.memory.project import ProjectMemory
        from opennova.memory.working import WorkingMemory
        runtime.context_manager = ContextManager(model="gpt-4o")
        runtime.working_memory = WorkingMemory()
        runtime.project_memory = ProjectMemory(project_path=tempfile.mkdtemp())
        runtime._emit = lambda *args, **kwargs: None
        runtime.mcp_manager = type(
            "Manager",
            (),
            {
                "get_server_names": lambda self: [],
                "connect_all": lambda self, configs: connect_all(configs),
            },
        )()
        runtime._mcp_server_configs = [MCPServerConfig(name="filesystem")]

        result = await AgentRuntime._run_act_mode(runtime, "Use MCP", stream=False)

        assert result == "done"
        assert runtime._connect_called_with == ["filesystem"]

    @pytest.mark.asyncio
    async def test_react_loop_awaits_async_tool_execution(self):
        class AsyncTool(BaseSkill):
            name = "async_tool"
            description = "Async test tool"

            def execute(self, **kwargs) -> ToolResult:
                raise RuntimeError("sync path should not be used")

            async def async_execute(self, value: str) -> ToolResult:
                await asyncio.sleep(0)
                return ToolResult(success=True, output=f"async:{value}")

        class ToolCallingProvider:
            model = "dummy"

            def __init__(self):
                self.calls = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    from opennova.providers.base import ToolCall, Usage
                    return LLMResponse(
                        content="run async tool",
                        tool_calls=[ToolCall(id="call_1", name="async_tool", arguments={"value": "ok"})],
                        finish_reason=FinishReason.TOOL_CALL,
                        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                    )
                return LLMResponse(content="finished", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

        registry = ToolRegistry()
        registry.clear()
        registry.register(AsyncTool())
        loop = ReActLoop(ToolCallingProvider(), registry, AgentState(), stream=False)

        result = await loop.run("Call async tool")

        assert result == "finished"

    def test_mcp_manager_unregisters_tools_on_remove_server(self):
        registry = ToolRegistry()
        registry.clear()
        manager = MCPManager(registry)
        manager._registered_tools_by_server["filesystem"] = ["filesystem_read_file"]

        class DummyConnector:
            async def disconnect(self):
                return None

        manager.connectors["filesystem"] = DummyConnector()
        registry.register(type("Tool", (), {"name": "filesystem_read_file", "description": "", "get_schema": lambda self: None})())

        asyncio.run(manager.remove_server("filesystem"))

        assert not registry.has_tool("filesystem_read_file")

    @pytest.mark.asyncio
    async def test_connector_disconnect_fails_pending_requests(self):
        connector = MCPConnector(MCPServerConfig(name="filesystem"))
        future = asyncio.get_running_loop().create_future()
        connector._pending_requests[1] = future

        await connector.disconnect()

        assert future.done()
        assert isinstance(future.exception(), RuntimeError)

    def test_runtime_reload_skills_matches_startup_configuration(self):
        """Runtime reload should use the same built-in and exclusion rules as startup."""
        config = {
            "default_provider": "openai",
            "providers": {
                "openai": {
                    "api_key": "test-key",
                    "model": "gpt-4o-mini",
                }
            },
            "skills": {
                "enabled": True,
                "exclude": ["git_helper"],
            },
            "mcp": {"enabled": False},
        }

        runtime = AgentRuntime(config=config, register_default_tools=True, enable_mcp=False, enable_skills=True)

        assert runtime.skill_registry is not None
        startup_skills = set(runtime.skill_registry.list_skills())
        startup_enabled = set(runtime.skill_registry.list_enabled_skills())

        reloaded_count = runtime.reload_skills()

        assert reloaded_count == len(runtime.skill_registry)
        assert set(runtime.skill_registry.list_skills()) == startup_skills
        assert set(runtime.skill_registry.list_enabled_skills()) == startup_enabled
        assert "git_helper" in runtime.skill_registry.list_skills()
        assert "git_helper" not in runtime.skill_registry.list_enabled_skills()

    def test_runtime_reload_skills_respects_disabled_config(self):
        """Reloading skills should respect disabled skills configuration."""
        config = {
            "default_provider": "openai",
            "providers": {
                "openai": {
                    "api_key": "test-key",
                    "model": "gpt-4o-mini",
                }
            },
            "skills": {
                "enabled": False,
            },
            "mcp": {"enabled": False},
        }

        runtime = AgentRuntime(config=config, register_default_tools=True, enable_mcp=False, enable_skills=False)
        runtime.skill_registry = SkillRegistry(runtime.tool_registry)
        runtime.skill_registry.load_all(builtins=get_builtin_skill_classes())

        reloaded_count = runtime.reload_skills()

        assert reloaded_count == 0
        assert len(runtime.skill_registry) == 0

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
