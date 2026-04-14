"""Tests for Phase 2 modules."""

import pytest
from pathlib import Path
import tempfile
import os

from opennova.diff.engine import DiffEngine, ApplyResult
from opennova.diff.parser import DiffParser, FileChange, ChangeType
from opennova.diff.changeset import ChangeSet, ChangeResult
from opennova.memory.context import ContextManager
from opennova.memory.working import WorkingMemory, ActionStatus
from opennova.memory.project import ProjectMemory
from opennova.providers.base import FinishReason, LLMResponse, Message, ToolCall, Usage
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.loop import ReActLoop
from opennova.runtime.state import AgentState
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.security.guardrails import Guardrails, RiskLevel
from opennova.security.sandbox import Sandbox, SandboxConfig


class TestDiffEngine:
    """Tests for DiffEngine."""

    def test_generate_diff(self):
        """Test diff generation."""
        engine = DiffEngine()
        original = "line1\nline2\nline3\n"
        modified = "line1\nline2_modified\nline3\n"

        diff = engine.generate_diff(original, modified, "test.txt")

        assert "--- a/test.txt" in diff
        assert "+++ b/test.txt" in diff
        assert "-line2" in diff
        assert "+line2_modified" in diff

    def test_parse_diff(self):
        """Test diff parsing."""
        engine = DiffEngine()
        diff_text = """--- a/test.txt
+++ b/test.txt
@@ -1,3 +1,3 @@
 line1
-line2
+line2_modified
 line3"""

        hunks = engine.parse_diff(diff_text)

        assert len(hunks) == 1
        assert hunks[0].old_start == 1
        assert hunks[0].new_start == 1

    def test_validate_patch(self):
        """Test patch validation."""
        engine = DiffEngine()

        valid_diff = "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-old\n+new"
        is_valid, error = engine.validate_patch(valid_diff)
        assert is_valid

        invalid_diff = "not a valid diff"
        is_valid, error = engine.validate_patch(invalid_diff)
        assert not is_valid

    def test_preview_diff(self):
        """Test diff preview with colors."""
        engine = DiffEngine()
        diff_text = "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-old\n+new"

        preview = engine.preview_diff(diff_text)

        assert "\033[31m" in preview  # Red for removal
        assert "\033[32m" in preview  # Green for addition


class TestDiffParser:
    """Tests for DiffParser."""

    def test_parse_xml_format(self):
        """Test parsing XML-style file changes."""
        parser = DiffParser()
        xml_text = """<file_change>
<path>test.py</path>
<type>modify</type>
<diff>
--- a/test.py
+++ b/test.py
@@ -1 +1 @@
-old
+new
</diff>
</file_change>"""

        changes = parser.parse(xml_text)

        assert len(changes) == 1
        assert changes[0].file_path == "test.py"
        assert changes[0].change_type == ChangeType.MODIFY

    def test_parse_markdown_format(self):
        """Test parsing markdown diff blocks."""
        parser = DiffParser()

        text = """Here's a change:
```diff
--- a/example.py
+++ b/example.py
@@ -1 +1 @@
-old
+new
```"""

        changes = parser.parse(text)

        assert len(changes) >= 1


class TestChangeSet:
    """Tests for ChangeSet."""

    def test_create_changeset(self):
        """Test creating a change set."""
        changeset = ChangeSet(
            task="Test task",
            changes=[
                FileChange(file_path="test.txt", change_type=ChangeType.CREATE, new_content="content")
            ],
        )

        assert len(changeset) == 1
        assert changeset.task == "Test task"

    def test_get_preview(self):
        """Test getting change preview."""
        changeset = ChangeSet(
            task="Test",
            changes=[
                FileChange(file_path="new.txt", change_type=ChangeType.CREATE, new_content="content"),
                FileChange(file_path="old.txt", change_type=ChangeType.DELETE),
            ],
        )

        preview = changeset.get_preview()

        assert "CREATE" in preview
        assert "DELETE" in preview


class TestContextManager:
    """Tests for ContextManager."""

    def test_add_messages(self):
        """Test adding messages."""
        ctx = ContextManager(model="gpt-4o")

        ctx.add_user_message("Hello")
        ctx.add_assistant_message("Hi there!")

        assert len(ctx) == 2

    def test_token_counting(self):
        """Test token counting."""
        ctx = ContextManager(model="gpt-4o")

        count = ctx.count_tokens("Hello, world!")

        assert count > 0

    def test_context_stats(self):
        """Test context statistics."""
        ctx = ContextManager(model="gpt-4o", context_window=1000)
        ctx.add_user_message("Test message")

        stats = ctx.get_stats()

        assert stats.total_messages == 1
        assert stats.total_tokens > 0


class TestWorkingMemory:
    """Tests for WorkingMemory."""

    def test_record_action(self):
        """Test recording actions."""
        memory = WorkingMemory(task="Test task")

        action = memory.record_action("read_file", {"file_path": "test.txt"})
        memory.update_action(action.id, ActionStatus.SUCCESS, "Content")

        assert len(memory.actions) == 1
        assert memory.actions[0].status == ActionStatus.SUCCESS

    def test_observe_file(self):
        """Test file observation."""
        memory = WorkingMemory(task="Test")

        memory.observe_file("test.txt", "read", "content preview")

        assert len(memory.observations) == 1
        assert "test.txt" in memory.get_files_read()


class TestProjectMemory:
    """Tests for ProjectMemory."""

    def test_add_decision(self):
        """Test adding decisions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(project_path=tmpdir)

            decision = memory.add_decision(
                description="Use Python 3.11",
                reasoning="For better performance",
            )

            assert len(memory.decisions) == 1
            assert decision.description == "Use Python 3.11"

    def test_set_preference(self):
        """Test setting preferences."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = ProjectMemory(project_path=tmpdir)

            memory.set_preference("editor", "vim", category="tools")

            assert memory.get_preference("editor") == "vim"


class TestMemoryRuntimeIntegration:
    """Integration tests for runtime memory wiring."""

    @pytest.mark.asyncio
    async def test_react_loop_uses_context_manager_messages_for_llm(self):
        class RecordingProvider:
            model = "dummy"

            def __init__(self):
                self.seen_messages = None

            async def chat(self, messages, tools=None, **kwargs):
                self.seen_messages = messages
                return LLMResponse(content="done", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

        provider = RecordingProvider()
        registry = ToolRegistry()
        state = AgentState()
        context = ContextManager(model="gpt-4o")
        loop = ReActLoop(provider, registry, state, stream=False, context_manager=context)
        loop.set_context([Message(role="system", content="Memory context")])

        result = await loop.run("Use memory")

        assert result == "done"
        assert provider.seen_messages is not None
        assert provider.seen_messages[0].content == "Memory context"
        assert provider.seen_messages[-1].content == "Task: Use memory"

    @pytest.mark.asyncio
    async def test_react_loop_records_working_memory_actions_and_file_observations(self):
        class ReadFileTool(BaseTool):
            name = "read_file"
            description = "Read a file"

            def execute(self, file_path: str) -> ToolResult:
                return ToolResult(
                    success=True,
                    output="hello world",
                    metadata={"file_path": file_path},
                )

        class ToolCallingProvider:
            model = "dummy"

            def __init__(self):
                self.calls = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(
                        content="Reading file",
                        tool_calls=[ToolCall(id="call_1", name="read_file", arguments={"file_path": "test.txt"})],
                        finish_reason=FinishReason.TOOL_CALL,
                        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                    )
                return LLMResponse(content="done", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

        provider = ToolCallingProvider()
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        state = AgentState()
        working = WorkingMemory(task="Read a file")
        context = ContextManager(model="gpt-4o")
        loop = ReActLoop(
            provider,
            registry,
            state,
            stream=False,
            context_manager=context,
            working_memory=working,
        )
        working.start_task()

        result = await loop.run("Read test.txt")

        assert result == "done"
        assert len(working.actions) == 1
        assert working.actions[0].tool_name == "read_file"
        assert working.actions[0].status == ActionStatus.SUCCESS
        assert "test.txt" in working.get_files_read()

    @pytest.mark.asyncio
    async def test_agent_runtime_records_project_memory_sessions(self):
        class DummyProvider:
            model = "gpt-4o"

            async def chat(self, messages, tools=None, **kwargs):
                return LLMResponse(content="finished", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

            def get_model_info(self):
                return {"model": self.model}

        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.state = AgentState()
        runtime.tool_registry = ToolRegistry()
        runtime.max_iterations = 5
        runtime.show_thinking = False
        runtime._callbacks = {}
        runtime.llm = DummyProvider()
        runtime.context_manager = ContextManager(model="gpt-4o")
        runtime.working_memory = WorkingMemory()
        runtime.project_memory = ProjectMemory(project_path=tempfile.mkdtemp())
        runtime._emit = lambda *args, **kwargs: None

        result = await AgentRuntime._run_act_mode(runtime, "Remember this run", stream=False)

        assert result == "finished"
        assert runtime.project_memory.session_history
        session = runtime.project_memory.session_history[-1]
        assert session["task"] == "Remember this run"
        assert session["success"] is True

    @pytest.mark.asyncio
    async def test_react_loop_records_failed_working_memory_action_when_tool_returns_failure(self):
        class FailingTool(BaseTool):
            name = "failing_tool"
            description = "Fail a task"

            def execute(self, **kwargs) -> ToolResult:
                return ToolResult(success=False, output="", error="read failed")

        class ToolCallingProvider:
            model = "dummy"

            def __init__(self):
                self.calls = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(
                        content="Run failing tool",
                        tool_calls=[ToolCall(id="call_1", name="failing_tool", arguments={})],
                        finish_reason=FinishReason.TOOL_CALL,
                        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                    )
                return LLMResponse(content="done", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

        registry = ToolRegistry()
        registry.register(FailingTool())
        working = WorkingMemory(task="Run failing tool")
        loop = ReActLoop(
            ToolCallingProvider(),
            registry,
            AgentState(),
            stream=False,
            context_manager=ContextManager(model="gpt-4o"),
            working_memory=working,
        )
        working.start_task()

        result = await loop.run("Run failing tool")

        assert result == "done"
        assert len(working.actions) == 1
        assert working.actions[0].status == ActionStatus.FAILED
        assert working.actions[0].error == "read failed"

    @pytest.mark.asyncio
    async def test_react_loop_records_failed_working_memory_action_when_tool_raises(self):
        class ExplodingTool(BaseTool):
            name = "exploding_tool"
            description = "Raise during execution"

            def execute(self, **kwargs) -> ToolResult:
                raise RuntimeError("boom")

        class ToolCallingProvider:
            model = "dummy"

            def __init__(self):
                self.calls = 0

            async def chat(self, messages, tools=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(
                        content="Run exploding tool",
                        tool_calls=[ToolCall(id="call_1", name="exploding_tool", arguments={})],
                        finish_reason=FinishReason.TOOL_CALL,
                        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                    )
                return LLMResponse(content="done", finish_reason=FinishReason.STOP)

            async def stream_chat(self, messages, tools=None, **kwargs):
                if False:
                    yield None

        registry = ToolRegistry()
        registry.register(ExplodingTool())
        working = WorkingMemory(task="Run exploding tool")
        loop = ReActLoop(
            ToolCallingProvider(),
            registry,
            AgentState(),
            stream=False,
            context_manager=ContextManager(model="gpt-4o"),
            working_memory=working,
        )
        working.start_task()

        result = await loop.run("Run exploding tool")

        assert result == "done"
        assert len(working.actions) == 1
        assert working.actions[0].status == ActionStatus.FAILED
        assert working.actions[0].error == "boom"

    def test_agent_runtime_builds_memory_messages_without_relevant_decisions(self):
        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime.project_memory = ProjectMemory(project_path=tempfile.mkdtemp())

        messages = AgentRuntime._build_memory_messages(runtime, "Unrelated task")

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Project:" in messages[0].content
        assert "Relevant prior decisions" not in messages[0].content


class TestGuardrails:
    """Tests for Guardrails."""

    def test_check_safe_command(self):
        """Test checking safe commands."""
        guardrails = Guardrails()

        result = guardrails.check_command("ls -la")

        assert result.allowed
        assert result.risk_level == RiskLevel.SAFE

    def test_check_dangerous_command(self):
        """Test checking dangerous commands."""
        guardrails = Guardrails()

        result = guardrails.check_command("rm -rf /")

        assert not result.allowed
        assert result.risk_level == RiskLevel.BLOCK

    def test_check_protected_path(self):
        """Test checking protected paths."""
        guardrails = Guardrails()

        result = guardrails.check_file_path("/etc/passwd", "read")

        assert not result.allowed
        assert result.risk_level == RiskLevel.BLOCK

    def test_check_http_request(self):
        """Test HTTP request checking."""
        guardrails = Guardrails()

        result = guardrails.check_http_request("https://api.example.com")

        assert result.allowed


class TestSandbox:
    """Tests for Sandbox."""

    def test_is_path_allowed(self):
        """Test path allowance check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SandboxConfig(working_dir=tmpdir)
            sandbox = Sandbox(config)

            is_allowed, reason = sandbox.is_path_allowed(tmpdir)

            assert is_allowed

            is_allowed, reason = sandbox.is_path_allowed("/etc/passwd")

            assert not is_allowed

    def test_safe_read_write(self):
        """Test safe file operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SandboxConfig(working_dir=tmpdir)
            sandbox = Sandbox(config)

            test_file = os.path.join(tmpdir, "test.txt")
            success, msg = sandbox.safe_write(test_file, b"Hello, world!")

            assert success

            success, content = sandbox.safe_read(test_file)

            assert success
            assert content == b"Hello, world!"
