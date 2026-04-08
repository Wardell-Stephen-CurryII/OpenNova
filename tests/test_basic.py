"""Basic tests for OpenNova."""

import asyncio
import pytest

from opennova.tools.base import ToolRegistry, ToolResult, BaseTool
from opennova.providers.base import Message, ToolSchema
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.state import AgentState
from opennova.runtime.loop import ReActLoop
from opennova.tasks import TaskManager, TaskStatus, TaskType


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


def test_task_manager_progress_updates():
    """Task manager should aggregate progress and session state."""
    manager = TaskManager()
    task = manager.create_task(TaskType.LOCAL_AGENT, "Agent: test")

    updated = manager.update_task_progress(
        task.id,
        activity="Running tool: mock_tool",
        token_count=12,
        tool_use_increment=2,
        last_tool_name="mock_tool",
    )
    manager.set_session_state(task.id, last_user_message="hello")

    assert updated is True
    assert task.progress.last_activity == "Running tool: mock_tool"
    assert task.progress.tool_use_count == 2
    assert task.usage.total_tokens == 12
    assert task.usage.tool_uses == 2
    assert task.progress.last_tool_name == "mock_tool"
    assert task.session_state["last_user_message"] == "hello"


class DummyProvider:
    model = "dummy"

    async def chat(self, messages, tools=None, **kwargs):
        raise NotImplementedError

    async def stream_chat(self, messages, tools=None, **kwargs):
        if False:
            yield None

    def get_model_info(self):
        return {"model": self.model}


@pytest.mark.asyncio
async def test_react_loop_reports_progress():
    """ReAct loop should emit progress callbacks during execution."""

    class FinalTool(BaseTool):
        name = "final_tool"
        description = "Complete the task"

        def execute(self, **kwargs):
            return ToolResult(success=True, output="done")

    class Provider(DummyProvider):
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, **kwargs):
            from opennova.providers.base import FinishReason, LLMResponse, ToolCall

            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    content="Using tool",
                    tool_calls=[ToolCall(id="call_1", name="final_tool", arguments={})],
                    finish_reason=FinishReason.TOOL_CALL,
                )
            return LLMResponse(content="Finished", finish_reason=FinishReason.STOP)

    registry = ToolRegistry()
    registry.clear()
    registry.register(FinalTool())
    state = AgentState()
    progress_events = []
    loop = ReActLoop(
        llm=Provider(),
        tool_registry=registry,
        state=state,
        stream=False,
        progress_callback=progress_events.append,
    )

    result = await loop.run("Test progress")

    assert result == "Finished"
    assert any(event["activity"].startswith("Started task:") for event in progress_events)
    assert any(event["activity"] == "Running tool: final_tool" for event in progress_events)
    assert any(event["tool_use_increment"] == 1 for event in progress_events)
    assert progress_events[-1]["is_complete"] is True


def test_create_child_runtime_inherits_flags():
    """Child runtimes should inherit config and feature flags."""
    config = {
        "default_provider": "openai",
        "providers": {
            "openai": {
                "api_key": "test-key",
                "model": "gpt-4o-mini",
            }
        },
        "agent": {"max_iterations": 7},
        "skills": {"enabled": False},
        "mcp": {"enabled": False},
    }
    runtime = AgentRuntime(config=config, register_default_tools=True, enable_mcp=False, enable_skills=False)

    child = runtime.create_child_runtime()

    assert child is not runtime
    assert child.config == runtime.config
    assert child.config is not runtime.config
    assert child.register_default_tools == runtime.register_default_tools
    assert child.enable_mcp == runtime.enable_mcp
    assert child.enable_skills == runtime.enable_skills
