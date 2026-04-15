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
from opennova.providers.base import FinishReason, LLMResponse, ToolCall
from opennova.skills.base import SkillLoader, LoadedSkill, SkillMetadata
from opennova.skills.examples import get_builtin_skill_dirs
from opennova.skills.registry import SkillRegistry
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.loop import ReActLoop
from opennova.runtime.state import AgentState
from opennova.tools.base import ToolRegistry, ToolResult, BaseTool


class TestMCPTypes:
    """Tests for MCP type definitions."""

    def test_server_config_from_dict(self):
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
        config = MCPServerConfig(
            name="test",
            transport=TransportType.SSE,
            url="http://localhost:3000/sse",
        )
        data = config.to_dict()
        assert data["name"] == "test"
        assert data["transport"] == "sse"
        assert data["url"] == "http://localhost:3000/sse"

    def test_server_config_requires_stdio_command(self):
        with pytest.raises(ValueError, match="command is required for stdio transport"):
            MCPServerConfig.from_dict({"name": "stdio_server", "transport": "stdio"})

    def test_server_config_requires_sse_url(self):
        with pytest.raises(ValueError, match="url is required for sse transport"):
            MCPServerConfig.from_dict({"name": "sse_server", "transport": "sse"})

    def test_server_config_rejects_unsupported_websocket_transport(self):
        with pytest.raises(ValueError, match="websocket transport is not yet supported"):
            MCPServerConfig.from_dict({"name": "ws_server", "transport": "websocket", "url": "ws://localhost:3000"})

    def test_mcp_tool(self):
        tool = MCPTool(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            server_name="filesystem",
        )
        assert tool.get_full_name() == "filesystem_read_file"
        schema = tool.to_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "filesystem_read_file"

    def test_mcp_tool_result(self):
        result = MCPToolResult(success=True, content="File contents here")
        assert result.to_string() == "File contents here"
        error_result = MCPToolResult(success=False, content="", error="File not found")
        assert "Error: File not found" in error_result.to_string()

    def test_mcp_message(self):
        msg = MCPMessage(id=1, method="tools/list", params={})
        data = msg.to_dict()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert data["method"] == "tools/list"
        parsed = MCPMessage.from_dict(data)
        assert parsed.id == msg.id
        assert parsed.method == msg.method


class TestSkills:
    """Tests for markdown skills system."""

    def test_skill_loader_discovers_skill_md_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "demo").mkdir()
            (root / "demo" / "SKILL.md").write_text("# Demo\n")
            discovered = SkillLoader.discover_skills([root])
            assert discovered == [root / "demo" / "SKILL.md"]

    def test_skill_loader_parses_frontmatter_and_description_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / "review" / "SKILL.md"
            skill_file.parent.mkdir()
            skill_file.write_text(
                "---\nname: review\nwhen_to_use: Use for reviews\nallowed-tools: read_file, list_directory\narguments: [target]\n---\nFirst body line\nMore details\n"
            )
            loaded = SkillLoader.load_skill_file(skill_file)
            assert loaded is not None
            assert loaded.name == "review"
            assert loaded.metadata.description == "First body line"
            assert loaded.metadata.when_to_use == "Use for reviews"
            assert loaded.metadata.allowed_tools == ["read_file", "list_directory"]
            assert loaded.metadata.arguments == ["target"]

    def test_skill_loader_missing_file_returns_none(self):
        assert SkillLoader.load_skill_file("/tmp/definitely-missing-skill/SKILL.md") is None

    def test_skill_loader_invalid_frontmatter_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / "broken" / "SKILL.md"
            skill_file.parent.mkdir()
            skill_file.write_text("---\nname: [oops\n---\nFallback description\n")
            loaded = SkillLoader.load_skill_file(skill_file)
            assert loaded is not None
            assert loaded.name == "broken"
            assert loaded.metadata.description == "Fallback description"

    def test_skill_registry_load_all_and_exclusions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "one").mkdir()
            (root / "one" / "SKILL.md").write_text("---\nname: one\ndescription: One\n---\nBody\n")
            (root / "two").mkdir()
            (root / "two" / "SKILL.md").write_text("---\nname: two\ndescription: Two\n---\nBody\n")

            registry = SkillRegistry()
            loaded = registry.load_all(directories=[root], excluded=["two"])

            assert sorted(loaded.keys()) == ["one", "two"]
            assert registry.is_enabled("one") is True
            assert registry.is_enabled("two") is False
            assert registry.get_skill_info("two")["description"] == "Two"

    def test_skill_registry_reload_replaces_removed_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "first").mkdir()
            (root / "first" / "SKILL.md").write_text("---\nname: first\ndescription: First\n---\nBody\n")

            registry = SkillRegistry()
            registry.load_all(directories=[root])
            assert "first" in registry.list_skills()

            (root / "first" / "SKILL.md").unlink()
            (root / "first").rmdir()
            (root / "second").mkdir()
            (root / "second" / "SKILL.md").write_text("---\nname: second\ndescription: Second\n---\nBody\n")

            registry.load_all(directories=[root], replace_existing=True)
            assert "first" not in registry.list_skills()
            assert "second" in registry.list_skills()

    def test_skill_registry_materializes_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "demo").mkdir()
            (root / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\nTarget: $ARGUMENTS\n")
            registry = SkillRegistry()
            registry.load_all(directories=[root])
            prompt = registry.materialize_skill_prompt("demo", "src/main.py")
            assert prompt is not None
            assert "Base directory for this skill" in prompt
            assert "src/main.py" in prompt

    def test_builtin_skill_dirs_exist(self):
        dirs = get_builtin_skill_dirs()
        assert dirs
        assert dirs[0].exists()


class TestMCPRuntimeIntegration:
    """Tests for MCP and runtime integration."""

    @pytest.mark.asyncio
    async def test_runtime_init_mcp_preserves_invalid_config_errors(self):
        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.tool_registry = ToolRegistry()
        runtime.config = {
            "mcp": {
                "enabled": True,
                "servers": [
                    {"name": "valid_stdio", "transport": "stdio", "command": "python"},
                    {"name": "invalid_sse", "transport": "sse"},
                ],
            }
        }

        AgentRuntime._init_mcp(runtime)

        assert [config.name for config in runtime._mcp_server_configs] == ["valid_stdio"]
        assert "invalid_sse" in runtime._mcp_config_errors
        assert "url is required for sse transport" in runtime._mcp_config_errors["invalid_sse"]

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
        runtime.skill_registry = None
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
        class AsyncTool(BaseTool):
            name = "async_tool"
            description = "Async test tool"

            def execute(self, **kwargs) -> ToolResult:
                raise RuntimeError("sync path should not be used")

            async def async_execute(self, value: str) -> ToolResult:
                await asyncio.sleep(0)
                return ToolResult(success=True, output=f"async:{value}")

        class ToolCallingProvider:
            model = "dummy"

            async def chat(self, messages, tools=None, **kwargs):
                if not any(message.role == "tool" for message in messages):
                    return LLMResponse(
                        content="using tool",
                        tool_calls=[ToolCall(id="call_1", name="async_tool", arguments={"value": "hello"})],
                        finish_reason=FinishReason.TOOL_CALL,
                    )
                return LLMResponse(content="done", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

            def get_model_info(self):
                return {"model": self.model}

        registry = ToolRegistry()
        registry.clear()
        registry.register(AsyncTool())
        loop = ReActLoop(llm=ToolCallingProvider(), tool_registry=registry, state=AgentState(), stream=False)

        result = await loop.run("Run async tool")

        assert result == "done"
        assert any(message.role == "tool" and "async:hello" in message.content for message in loop.messages)

    @pytest.mark.asyncio
    async def test_react_loop_invokes_markdown_skill_from_response_text(self):
        class DummyProvider:
            model = "dummy"

            async def chat(self, messages, tools=None, **kwargs):
                if not any(message.role == "tool" for message in messages):
                    return LLMResponse(content="/skill demo src/app.py", finish_reason=FinishReason.STOP)
                return LLMResponse(content="done", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

            def get_model_info(self):
                return {"model": self.model}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "demo").mkdir()
            (root / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\nInspect $ARGUMENTS\n")
            skills = SkillRegistry()
            skills.load_all(directories=[root])
            loop = ReActLoop(
                llm=DummyProvider(),
                tool_registry=ToolRegistry(),
                state=AgentState(),
                stream=False,
                skill_registry=skills,
            )

            result = await loop.run("Use a skill")

            assert result == "done"
            assert any(message.role == "user" and "Invoked skill 'demo'" in message.content for message in loop.messages)
