"""Tests for Phase 3 modules (MCP and Skills)."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from opennova.hooks import HookManager
from opennova.mcp.types import (
    MCPMessage,
    MCPServerConfig,
    MCPTool,
    MCPToolResult,
    TransportType,
)
from opennova.providers.base import FinishReason, LLMResponse, ToolCall
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.loop import ReActLoop
from opennova.runtime.state import AgentState
from opennova.skills.arguments import (
    generate_progressive_argument_hint,
    parse_argument_names,
    parse_arguments,
    substitute_arguments,
)
from opennova.skills.base import MaterializedSkill, SkillLoader
from opennova.skills.examples import get_builtin_skill_dirs
from opennova.skills.registry import SkillRegistry
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.skill_tool import SkillTool


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

    def test_argument_substitution_supports_indexed_and_named_placeholders(self):
        content = "All=$ARGUMENTS first=$0 second=$ARGUMENTS[1] target=$target path=$path"

        rendered = substitute_arguments(
            content,
            'src/app.py "docs/spec file.md"',
            argument_names=["target", "path"],
        )

        assert rendered == (
            "All=src/app.py \"docs/spec file.md\" first=src/app.py "
            "second=docs/spec file.md target=src/app.py path=docs/spec file.md"
        )

    def test_argument_substitution_appends_raw_arguments_when_no_placeholder_exists(self):
        rendered = substitute_arguments("Review carefully.", "src/main.py")

        assert rendered.endswith("\n\nARGUMENTS: src/main.py")

    def test_argument_helpers_parse_shell_arguments_and_progressive_hints(self):
        assert parse_arguments('foo "bar baz"') == ["foo", "bar baz"]
        assert parse_argument_names(["target", "1", "path"]) == ["target", "path"]
        assert generate_progressive_argument_hint(["target", "path"], ["src"]) == "[path]"

    def test_skill_loader_discovers_claude_style_default_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project_root = root / "project"
            home_root = root / "home"
            project_skill = project_root / ".claude" / "skills" / "project-demo"
            home_skill = home_root / ".claude" / "skills" / "home-demo"
            project_skill.mkdir(parents=True)
            home_skill.mkdir(parents=True)
            (project_skill / "SKILL.md").write_text("# Project demo\n")
            (home_skill / "SKILL.md").write_text("# Home demo\n")

            original_cwd = Path.cwd()
            original_dirs = SkillLoader.DEFAULT_SKILL_DIRS
            try:
                os.chdir(project_root)
                SkillLoader.DEFAULT_SKILL_DIRS = [
                    Path(".claude") / "skills",
                    home_root / ".claude" / "skills",
                ]
                discovered = SkillLoader.discover_skills()
            finally:
                SkillLoader.DEFAULT_SKILL_DIRS = original_dirs
                os.chdir(original_cwd)

            assert (project_skill / "SKILL.md").resolve() in [path.resolve() for path in discovered]
            assert (home_skill / "SKILL.md").resolve() in [path.resolve() for path in discovered]

    def test_skill_loader_discovers_nested_directories_and_single_file_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested_dir = root / "frontend" / "review"
            nested_dir.mkdir(parents=True)
            (nested_dir / "SKILL.md").write_text("---\ndescription: Review UI\n---\nBody\n")
            single_file = root / "ops.md"
            single_file.write_text("---\ndescription: Ops helper\n---\nBody\n")
            direct_root = root / "bundle"
            direct_root.mkdir()
            (direct_root / "SKILL.md").write_text("---\ndescription: Root skill\n---\nBody\n")
            deeper = direct_root / "ignored-child"
            deeper.mkdir()
            (deeper / "SKILL.md").write_text("---\ndescription: Ignored\n---\nBody\n")

            discovered = SkillLoader.discover_skills([root])
            loaded = {path.name if path.name != "SKILL.md" else path.parent.name: path for path in discovered}

            resolved = {path.resolve() for path in discovered}
            assert (nested_dir / "SKILL.md").resolve() in resolved
            assert single_file.resolve() in resolved
            assert (direct_root / "SKILL.md").resolve() in resolved
            assert (deeper / "SKILL.md").resolve() not in resolved
            assert "review" in loaded
            assert "ops.md" in loaded

    def test_skill_loader_assigns_namespaces_and_deduplicates_realpaths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "frontend" / "review").mkdir(parents=True)
            skill_file = root / "frontend" / "review" / "SKILL.md"
            skill_file.write_text("---\ndescription: Review UI\n---\nBody\n")
            alias_root = root / "alias-root"
            alias_root.symlink_to(root / "frontend", target_is_directory=True)

            loaded = SkillLoader.load_all_skills([root, alias_root])

            assert list(loaded) == ["frontend:review"]
            assert loaded["frontend:review"].metadata.namespace == "frontend"
            assert loaded["frontend:review"].metadata.canonical_name == "frontend:review"

    def test_skill_loader_parses_frontmatter_and_description_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / "review" / "SKILL.md"
            skill_file.parent.mkdir()
            skill_file.write_text(
                "---\nname: review\nwhen_to_use: Use for reviews\nallowed-tools: read_file, list_directory\narguments: [target]\nhooks:\n  pre_tool_use:\n    - matcher: read_file\n      hooks:\n        - add_metadata:\n            source: skill\npaths: src/**\n---\nFirst body line\nMore details\n"
            )
            loaded = SkillLoader.load_skill_file(skill_file)
            assert loaded is not None
            assert loaded.name == "review"
            assert loaded.metadata.description == "First body line"
            assert loaded.metadata.when_to_use == "Use for reviews"
            assert loaded.metadata.allowed_tools == ["read_file", "list_directory"]
            assert loaded.metadata.arguments == ["target"]
            assert loaded.metadata.paths == ["src/**"]
            assert loaded.metadata.hooks["pre_tool_use"][0]["matcher"] == "read_file"

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

    def test_skill_loader_ignores_invalid_hook_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / "broken-hooks" / "SKILL.md"
            skill_file.parent.mkdir()
            skill_file.write_text("---\ndescription: Broken hooks\nhooks: not-a-dict\n---\nBody\n")

            loaded = SkillLoader.load_skill_file(skill_file)

            assert loaded is not None
            assert loaded.metadata.hooks == {}

    def test_skill_registry_load_all_and_exclusions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "one").mkdir()
            (root / "one" / "SKILL.md").write_text("---\nname: one\ndescription: One\n---\nBody\n")
            (root / "two").mkdir()
            (root / "two" / "SKILL.md").write_text("---\nname: two\ndescription: Two\n---\nBody\n")

            registry = SkillRegistry()
            loaded = registry.load_all(directories=[root], excluded=["two"])

            assert "one" in loaded
            assert "two" in loaded
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

    def test_skill_registry_reports_invocation_visibility(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "model-only").mkdir()
            (root / "model-only" / "SKILL.md").write_text(
                "---\nname: model-only\ndescription: Model only\nuser-invocable: false\n---\nBody\n"
            )
            (root / "user-only").mkdir()
            (root / "user-only" / "SKILL.md").write_text(
                "---\nname: user-only\ndescription: User only\ndisable-model-invocation: true\n---\nBody\n"
            )

            registry = SkillRegistry()
            registry.load_all(directories=[root])

            assert "model-only" in registry.list_model_invocable_skills()
            assert "user-only" in registry.list_user_invocable_skills()
            assert registry.can_model_invoke("model-only") is True
            assert registry.can_user_invoke("model-only") is False
            assert registry.can_model_invoke("user-only") is False
            assert registry.can_user_invoke("user-only") is True

    def test_skill_registry_resolves_unique_bare_name_and_rejects_ambiguous_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "frontend" / "review").mkdir(parents=True)
            (root / "frontend" / "review" / "SKILL.md").write_text("---\ndescription: Frontend review\n---\nBody\n")
            (root / "backend" / "review").mkdir(parents=True)
            (root / "backend" / "review" / "SKILL.md").write_text("---\ndescription: Backend review\n---\nBody\n")
            (root / "lint").mkdir()
            (root / "lint" / "SKILL.md").write_text("---\ndescription: Lint\n---\nBody\n")

            registry = SkillRegistry()
            registry.load_all(directories=[root])

            assert registry.resolve_skill_name("lint").resolved_name == "lint"
            resolution = registry.resolve_skill_name("review")
            assert resolution.resolved_name is None
            assert resolution.matches == ["backend:review", "frontend:review"]

    def test_skill_registry_materializes_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "demo").mkdir()
            (root / "demo" / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo\nallowed-tools: read_file, list_directory\nmodel: gpt-4o-mini\narguments: [target]\n---\nTarget: $target\n"
            )
            registry = SkillRegistry()
            registry.load_all(directories=[root])
            materialized = registry.materialize_skill_prompt("demo", "src/main.py")
            assert isinstance(materialized, MaterializedSkill)
            assert "Base directory for this skill" in materialized.prompt
            assert "src/main.py" in materialized.prompt
            assert materialized.allowed_tools == ["read_file", "list_directory"]
            assert materialized.model == "gpt-4o-mini"
            assert materialized.argument_names == ["target"]

    def test_skill_registry_keeps_path_filtered_skills_pending_until_activated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "always").mkdir()
            (root / "always" / "SKILL.md").write_text("---\ndescription: Always\n---\nBody\n")
            (root / "python-review").mkdir()
            (root / "python-review" / "SKILL.md").write_text(
                "---\ndescription: Python review\npaths: src/**/*.py\n---\nBody\n"
            )

            registry = SkillRegistry()
            registry.load_all(directories=[root])

            assert registry.list_model_invocable_skills() == ["always"]
            assert registry.list_pending_conditional_skills() == ["python-review"]

            activated = registry.activate_for_paths([str(root / "src" / "app.py")], str(root))

            assert activated == ["python-review"]
            assert registry.list_pending_conditional_skills() == []
            assert registry.list_model_invocable_skills()[0] == "python-review"

    def test_skill_registry_discovers_nested_skill_roots_for_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested_skills = root / "src" / "feature" / ".claude" / "skills" / "nearby"
            nested_skills.mkdir(parents=True)
            (nested_skills / "SKILL.md").write_text("---\ndescription: Nearby\n---\nBody\n")

            registry = SkillRegistry()
            registry.load_all(directories=[])

            discovered = registry.discover_for_paths([str(root / "src" / "feature" / "module.py")], str(root))

            assert discovered == ["nearby"]
            assert registry.list_model_invocable_skills()[0] == "nearby"

    def test_skill_registry_ranks_dynamic_and_recently_used_skills_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "alpha").mkdir()
            (root / "alpha" / "SKILL.md").write_text("---\ndescription: Alpha\n---\nBody\n")
            (root / "beta").mkdir()
            (root / "beta" / "SKILL.md").write_text("---\ndescription: Beta\npaths: src/**\n---\nBody\n")

            registry = SkillRegistry()
            registry.load_all(directories=[root])
            registry.activate_for_paths([str(root / "src" / "x.py")], str(root))
            registry.record_skill_usage("alpha")

            ordered = registry.list_model_invocable_skills()

            assert ordered == ["beta", "alpha"]

    def test_skill_tool_invokes_runtime_helper(self):
        class RuntimeStub:
            def __init__(self):
                self.calls = []

            def invoke_skill(self, skill_name: str, skill_args: str = "", caller: str = "user") -> ToolResult:
                self.calls.append((skill_name, skill_args, caller))
                return ToolResult(success=True, output="invoked")

        runtime = RuntimeStub()
        tool = SkillTool(config={"runtime": runtime})

        result = tool.execute(skill="demo", args="src/app.py")

        assert result.success is True
        assert runtime.calls == [("demo", "src/app.py", "model")]

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


class TestSkillRuntimeIntegration:
    def test_agent_runtime_invoke_skill_returns_materialized_metadata(self):
        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.skill_registry = SkillRegistry()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "review").mkdir()
            (root / "review" / "SKILL.md").write_text(
                "---\ndescription: Review\nallowed-tools: read_file\nmodel: gpt-4o-mini\narguments: [target]\nhooks:\n  pre_tool_use:\n    - matcher: read_file\n      hooks:\n        - add_metadata:\n            source: skill\n---\nInspect $target\n"
            )
            runtime.skill_registry.load_all(directories=[root])

            result = AgentRuntime.invoke_skill(runtime, "review", "src/main.py", caller="user")

        assert result.success is True
        assert result.metadata["skill"] == "review"
        assert result.metadata["resolved_skill"] == "review"
        assert result.metadata["allowed_tools"] == ["read_file"]
        assert result.metadata["model"] == "gpt-4o-mini"
        assert result.metadata["argument_names"] == ["target"]
        assert result.metadata["hooks"]["pre_tool_use"][0]["matcher"] == "read_file"
        assert "Inspect src/main.py" in result.metadata["skill_prompt"]

    def test_agent_runtime_invoke_skill_rejects_ambiguous_bare_name(self):
        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.skill_registry = SkillRegistry()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "frontend" / "review").mkdir(parents=True)
            (root / "frontend" / "review" / "SKILL.md").write_text("---\ndescription: Frontend\n---\nBody\n")
            (root / "backend" / "review").mkdir(parents=True)
            (root / "backend" / "review" / "SKILL.md").write_text("---\ndescription: Backend\n---\nBody\n")
            runtime.skill_registry.load_all(directories=[root])

            result = AgentRuntime.invoke_skill(runtime, "review", caller="user")

        assert result.success is False
        assert "Ambiguous skill" in (result.error or "")

    @pytest.mark.asyncio
    async def test_react_loop_applies_skill_tool_constraints_temporarily(self):
        class RecordingProvider:
            def __init__(self):
                self.model = "base-model"
                self.tools_seen = None

            async def chat(self, messages, tools=None, **kwargs):
                self.tools_seen = [tool.name for tool in tools or []]
                return LLMResponse(content="done", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

        class ReadTool(BaseTool):
            name = "read_file"
            description = "Read"

            def execute(self, **kwargs):
                return ToolResult(success=True, output="ok")

        class WriteTool(BaseTool):
            name = "write_file"
            description = "Write"

            def execute(self, **kwargs):
                return ToolResult(success=True, output="ok")

        provider = RecordingProvider()
        registry = ToolRegistry([ReadTool(), WriteTool()])
        loop = ReActLoop(
            llm=provider,
            tool_registry=registry,
            state=AgentState(),
            stream=False,
        )

        action = type(
            "Action",
            (),
            {"tool_name": "skill", "thought": "", "arguments": {"skill": "review", "args": ""}},
        )()
        result = ToolResult(
            success=True,
            output="Invoked skill: review",
            metadata={
                "skill": "review",
                "skill_prompt": "Prompt",
                "allowed_tools": ["read_file"],
                "model": "gpt-4o-mini",
            },
        )

        await loop._observe(action, result, None)
        await loop._think()

        assert provider.tools_seen == ["read_file"]
        assert provider.model == "gpt-4o-mini"

        loop._clear_skill_execution_context()

        assert provider.model == "base-model"

    @pytest.mark.asyncio
    async def test_react_loop_registers_declarative_skill_hooks_after_invocation(self):
        class ReadTool(BaseTool):
            name = "read_file"
            description = "Read"

            def execute(self, **kwargs):
                return ToolResult(success=True, output="ok")

        registry = ToolRegistry([ReadTool()])
        hook_manager = HookManager()
        loop = ReActLoop(
            llm=type("Provider", (), {"model": "dummy"})(),
            tool_registry=registry,
            state=AgentState(),
            hook_manager=hook_manager,
            stream=False,
        )

        action = type(
            "Action",
            (),
            {"tool_name": "skill", "thought": "", "arguments": {"skill": "demo", "args": ""}},
        )()
        result = ToolResult(
            success=True,
            output="Invoked skill: demo",
            metadata={
                "skill": "demo",
                "resolved_skill": "demo",
                "skill_prompt": "Prompt",
                "hooks": {
                    "pre_tool_use": [
                        {
                            "matcher": "read_file",
                            "hooks": [{"add_metadata": {"from_skill": "demo"}}],
                        }
                    ]
                },
                "skill_dir": "/tmp/demo",
            },
        )

        await loop._observe(action, result, None)
        observed = hook_manager.run_pre_tool_use({"tool_name": "read_file", "arguments": {}, "metadata": {}})

        assert observed["metadata"]["from_skill"] == "demo"

    @pytest.mark.asyncio
    async def test_react_loop_registers_once_skill_hook_only_once(self):
        hook_manager = HookManager()
        loop = ReActLoop(
            llm=type("Provider", (), {"model": "dummy"})(),
            tool_registry=ToolRegistry(),
            state=AgentState(),
            hook_manager=hook_manager,
            stream=False,
        )

        action = type(
            "Action",
            (),
            {"tool_name": "skill", "thought": "", "arguments": {"skill": "demo", "args": ""}},
        )()
        result = ToolResult(
            success=True,
            output="Invoked skill: demo",
            metadata={
                "skill": "demo",
                "resolved_skill": "demo",
                "skill_prompt": "Prompt",
                "hooks": {
                    "pre_tool_use": [
                        {
                            "matcher": "read_file",
                            "hooks": [{"once": True, "add_metadata": {"from_skill": "demo"}}],
                        }
                    ]
                },
                "skill_dir": "/tmp/demo",
            },
        )

        await loop._observe(action, result, None)
        first = hook_manager.run_pre_tool_use({"tool_name": "read_file", "arguments": {}, "metadata": {}})
        second = hook_manager.run_pre_tool_use({"tool_name": "read_file", "arguments": {}, "metadata": {}})

        assert first["metadata"]["from_skill"] == "demo"
        assert second["metadata"] == {}

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
            registry = ToolRegistry()
            registry.clear()
            registry.register(SkillTool(config={"runtime": type("RuntimeStub", (), {
                "invoke_skill": lambda self, skill_name, skill_args="", caller="user": ToolResult(
                    success=True,
                    output=f"Invoked skill: {skill_name}",
                    metadata={"skill": skill_name, "args": skill_args, "caller": caller},
                ),
            })()}))
            loop = ReActLoop(
                llm=DummyProvider(),
                tool_registry=registry,
                state=AgentState(),
                stream=False,
                skill_registry=skills,
            )

            result = await loop.run("Use a skill")

            assert result == "done"
            assert any(message.role == "tool" and "Invoked skill: demo" in message.content for message in loop.messages)
