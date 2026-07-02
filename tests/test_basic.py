"""Basic tests for OpenNova."""

import asyncio
import subprocess
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from opennova.config import DEFAULT_CONFIG
from opennova.memory.context import ContextManager
from opennova.providers.base import Message
from opennova.providers.factory import ProviderFactory
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.loop import ReActLoop
from opennova.runtime.state import AgentState, PlanApprovalStatus
from opennova.security.guardrails import RiskLevel
from opennova.tasks import TaskManager, TaskStatus, TaskType
from opennova.tools.agent_tools import AgentTool, SendMessageTool
from opennova.tools.ask_question_tool import AskUserQuestionTool
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)
from opennova.tools.plan_mode_tools import EnterPlanModeTool, ExitPlanModeTool
from opennova.tools.shell_tools import ExecuteCommandTool
from opennova.tools.task_tools import (
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    set_global_task_manager,
)
from opennova.tools.web_tools import WebFetchTool, WebSearchTool
from opennova.utils.task_output import read_task_output


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


def test_execute_command_schema_exposes_timeout_as_integer():
    """Optional integer params should remain integers in tool schema."""
    schema = ExecuteCommandTool().get_schema()

    assert schema.parameters["properties"]["timeout"]["type"] == "integer"


def test_execute_command_schema_documents_arguments_for_model_use():
    """Shell tool schema should tell models exactly how to call it."""
    schema = ExecuteCommandTool().get_schema()
    properties = schema.parameters["properties"]

    assert schema.parameters["additionalProperties"] is False
    assert schema.parameters["required"] == ["command"]
    assert "single string" in properties["command"]["description"].lower()
    assert "not an array" in properties["command"]["description"].lower()
    assert "working directory" in properties["working_dir"]["description"].lower()
    assert "seconds" in properties["timeout"]["description"].lower()


def test_default_config_uses_deepseek_v4_pro():
    """Default configuration should prefer DeepSeek v4 Pro."""
    assert DEFAULT_CONFIG["default_provider"] == "deepseek"
    assert DEFAULT_CONFIG["providers"]["deepseek"]["default_model"] == "deepseek-v4-pro"


def test_provider_factory_falls_back_to_deepseek_v4_pro_when_model_missing():
    """ProviderFactory should use DeepSeek v4 Pro as the default DeepSeek fallback model."""
    provider = ProviderFactory.create_provider(
        {
            "default_provider": "deepseek",
            "providers": {
                "deepseek": {
                    "api_key": "test-key",
                    "base_url": "https://api.deepseek.com/v1",
                }
            },
        }
    )

    assert provider.model == "deepseek-v4-pro"


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


def test_agent_tool_sync_execution_works_inside_running_event_loop():
    """Synchronous agent execution should still work when an event loop is already running."""

    class SyncCompatibleRuntime:
        def create_child_runtime(self):
            runtime = type("ChildRuntime", (), {})()
            runtime.state = AgentState()
            runtime.thread_names = []

            def register_callback(event, callback):
                return None

            async def run(prompt, mode="act", stream=False, progress_callback=None):
                runtime.thread_names.append(threading.current_thread().name)
                if progress_callback:
                    progress_callback(
                        {
                            "activity": "Completed tool: mock_tool",
                            "token_count": 4,
                            "tool_use_increment": 1,
                            "last_tool_name": "mock_tool",
                            "is_complete": True,
                        }
                    )
                return "sync success"

            runtime.register_callback = register_callback
            runtime.run = run
            return runtime

    async def invoke_tool():
        manager = TaskManager()
        set_global_task_manager(manager)
        tool = AgentTool(config={"runtime": SyncCompatibleRuntime()})
        return tool.execute(description="sync child", prompt="Run sync child")

    result = asyncio.run(invoke_tool())

    assert result.success is True
    assert result.output == "sync success"
    assert result.metadata["totalTokens"] == 4
    assert result.metadata["totalToolUseCount"] == 1


@pytest.mark.asyncio
async def test_agent_tool_sync_execution_uses_worker_thread_when_loop_running():
    """Nested loop execution should move synchronous agent runs off the active event loop."""

    class WorkerThreadRuntime:
        def __init__(self):
            self.child_runtime = None

        def create_child_runtime(self):
            runtime = type("ChildRuntime", (), {})()
            runtime.state = AgentState()
            runtime.thread_names = []

            def register_callback(event, callback):
                return None

            async def run(prompt, mode="act", stream=False, progress_callback=None):
                runtime.thread_names.append(threading.current_thread().name)
                return "worker thread success"

            runtime.register_callback = register_callback
            runtime.run = run
            self.child_runtime = runtime
            return runtime

    manager = TaskManager()
    set_global_task_manager(manager)
    runtime = WorkerThreadRuntime()
    tool = AgentTool(config={"runtime": runtime})

    result = tool.execute(description="nested sync", prompt="Run while loop active")

    assert result.success is True
    assert result.output == "worker thread success"
    assert runtime.child_runtime is not None
    assert runtime.child_runtime.thread_names
    assert all(name != threading.current_thread().name for name in runtime.child_runtime.thread_names)


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


@pytest.mark.asyncio
async def test_agent_tool_applies_follow_up_messages_during_run():
    """Queued follow-up messages should be injected into an active child agent."""

    class RecordingProvider(DummyProvider):
        def __init__(self):
            self.calls = 0
            self.snapshots = []

        async def chat(self, messages, tools=None, **kwargs):
            from opennova.providers.base import FinishReason, LLMResponse

            self.calls += 1
            self.snapshots.append([message.content for message in messages])
            if self.calls == 1:
                await asyncio.sleep(0.05)
                return LLMResponse(content="still working", finish_reason=FinishReason.LENGTH)
            return LLMResponse(content="done", finish_reason=FinishReason.STOP)

    class RecordingRuntime:
        def __init__(self):
            self.child_runtime = None

        def create_child_runtime(self):
            runtime = type("ChildRuntime", (), {})()
            runtime.state = AgentState()
            runtime.callback = None
            runtime.llm = RecordingProvider()
            runtime.tool_registry = ToolRegistry()
            runtime.enable_mcp = False
            runtime.enable_skills = False
            runtime.register_default_tools = True

            def register_callback(event, callback):
                if event == "iteration_start":
                    runtime.callback = callback

            async def run(prompt, mode="act", stream=False, progress_callback=None):
                loop = ReActLoop(
                    llm=runtime.llm,
                    tool_registry=runtime.tool_registry,
                    state=runtime.state,
                    stream=stream,
                    progress_callback=progress_callback,
                    iteration_start_callback=runtime.callback,
                    max_iterations=3,
                )
                return await loop.run(prompt)

            runtime.register_callback = register_callback
            runtime.run = run
            self.child_runtime = runtime
            return runtime

    manager = TaskManager()
    set_global_task_manager(manager)
    runtime = RecordingRuntime()
    tool = AgentTool(config={"runtime": runtime})

    launch = tool.execute(
        description="background recorder",
        prompt="Record follow-ups",
        run_in_background=True,
    )

    agent_id = launch.metadata["agentId"]
    await asyncio.sleep(0.01)

    send_result = SendMessageTool().execute(to=agent_id, message="Please include the follow-up")

    assert send_result.success is True
    assert send_result.metadata["pending_messages"] == 1
    assert send_result.metadata["message_id"].startswith("msg_")
    assert send_result.metadata["delivery_state"] == "queued"

    await asyncio.sleep(0.12)

    task = manager.get_task(agent_id)
    snapshots = runtime.child_runtime.llm.snapshots

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert task.session_state["last_user_message"] == "Please include the follow-up"
    assert task.session_state["pending_messages"] == 0
    assert task.session_state["delivered_messages"] == 1
    assert task.session_state["delivered_follow_up_batches"] == 1
    assert task.session_state["last_follow_up_batch"] == "Additional instruction from the parent conversation:\nPlease include the follow-up"
    assert task.session_state["last_follow_up_batch_id"].startswith("batch_")
    assert task.session_state["last_delivered_message_ids"] == [send_result.metadata["message_id"]]
    assert len(task.delivered_messages) == 1
    assert task.delivered_messages[0]["content"] == "Please include the follow-up"
    assert task.delivered_messages[0]["message_id"] == send_result.metadata["message_id"]
    assert task.delivered_messages[0]["delivery_state"] == "delivered"
    assert len(task.follow_up_batches) == 1
    assert task.follow_up_batches[0]["batch_id"].startswith("batch_")
    assert task.follow_up_batches[0]["message_count"] == 1
    assert task.follow_up_batches[0]["message_ids"] == [send_result.metadata["message_id"]]
    assert task.follow_up_batches[0]["rendered_content"] == "Additional instruction from the parent conversation:\nPlease include the follow-up"
    assert any("Additional instruction from the parent conversation:\nPlease include the follow-up" in snapshot for snapshot in snapshots)


def test_send_message_reports_pending_queue_length():
    """send_message should track queued follow-ups for running agents."""
    manager = TaskManager()
    set_global_task_manager(manager)
    task = manager.create_task(TaskType.LOCAL_AGENT, "Agent: queued")
    manager.update_task_status(task.id, TaskStatus.RUNNING)

    result = SendMessageTool().execute(to=task.id, message="hello")

    assert result.success is True
    assert result.metadata["pending_messages"] == 1
    assert result.metadata["delivered_messages"] == 0
    assert result.metadata["delivered_follow_up_batches"] == 0
    assert result.metadata["message_id"].startswith("msg_")
    assert result.metadata["delivery_state"] == "queued"
    assert task.message_queue[0]["content"] == "hello"
    assert task.message_queue[0]["message_id"] == result.metadata["message_id"]
    assert task.message_queue[0]["delivery_state"] == "queued"


def test_send_message_rejects_non_running_agents():
    """send_message should reject completed agents."""
    manager = TaskManager()
    set_global_task_manager(manager)
    task = manager.create_task(TaskType.LOCAL_AGENT, "Agent: finished")
    manager.update_task_status(task.id, TaskStatus.COMPLETED)

    result = SendMessageTool().execute(to=task.id, message="late update")

    assert result.success is False
    assert "is not running" in (result.error or "")


@pytest.mark.asyncio
async def test_background_agent_completion_notification_includes_usage():
    """Background agent notifications should include actual usage and final session state."""

    class SuccessfulRuntime:
        def create_child_runtime(self):
            runtime = type("ChildRuntime", (), {})()
            runtime.state = AgentState()

            def register_callback(event, callback):
                return None

            async def run(prompt, mode="act", stream=False, progress_callback=None):
                if progress_callback:
                    progress_callback(
                        {
                            "activity": "Completed tool: mock_tool",
                            "token_count": 9,
                            "tool_use_increment": 2,
                            "last_tool_name": "mock_tool",
                            "is_complete": True,
                        }
                    )
                return "background success"

            runtime.register_callback = register_callback
            runtime.run = run
            return runtime

    manager = TaskManager()
    set_global_task_manager(manager)
    tool = AgentTool(config={"runtime": SuccessfulRuntime()})

    launch = tool.execute(
        description="background success",
        prompt="Do the work",
        run_in_background=True,
    )

    agent_id = launch.metadata["agentId"]
    await asyncio.sleep(0.05)

    task = manager.get_task(agent_id)
    output, _ = read_task_output(agent_id)

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert task.usage.total_tokens == 9
    assert task.usage.tool_uses == 2
    assert task.usage.duration_ms >= 0
    assert task.session_state["last_agent_result"] == "background success"
    assert "<total_tokens>9</total_tokens>" in output
    assert "<tool_uses>2</tool_uses>" in output
    assert "<pending_messages>0</pending_messages>" in output
    assert "<delivered_messages>0</delivered_messages>" in output
    assert "<delivered_follow_up_batches>0</delivered_follow_up_batches>" in output


@pytest.mark.asyncio
async def test_background_agent_failure_notification_includes_duration():
    """Background agent failures should record duration and error state consistently."""

    class FailingRuntime:
        def create_child_runtime(self):
            runtime = type("ChildRuntime", (), {})()
            runtime.state = AgentState()

            def register_callback(event, callback):
                return None

            async def run(prompt, mode="act", stream=False, progress_callback=None):
                raise RuntimeError("boom")

            runtime.register_callback = register_callback
            runtime.run = run
            return runtime

    manager = TaskManager()
    set_global_task_manager(manager)
    tool = AgentTool(config={"runtime": FailingRuntime()})

    launch = tool.execute(
        description="background failure",
        prompt="Fail the work",
        run_in_background=True,
    )

    agent_id = launch.metadata["agentId"]
    await asyncio.sleep(0.05)

    task = manager.get_task(agent_id)
    output, _ = read_task_output(agent_id)

    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert task.session_state["last_error"] == "boom"
    assert task.usage.duration_ms >= 0
    assert "<status>failed</status>" in output
    assert "<error>boom</error>" in output
    assert "<duration_ms>" in output
    assert "<delivered_messages>0</delivered_messages>" in output
    assert "<delivered_follow_up_batches>0</delivered_follow_up_batches>" in output


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


def test_task_dependency_fields_round_trip_through_serialization():
    """Task dependency fields should survive serialization and deserialization."""
    manager = TaskManager()
    prerequisite = manager.create_task(TaskType.LOCAL_WORKFLOW, "Prepare work")
    dependent = manager.create_task(TaskType.LOCAL_WORKFLOW, "Ship work")

    success, error = manager.add_dependency(prerequisite.id, dependent.id)

    assert success is True
    assert error is None

    restored = type(prerequisite).from_dict(prerequisite.to_dict())
    restored_dependent = type(dependent).from_dict(dependent.to_dict())

    assert restored.blocks == [dependent.id]
    assert restored.blocked_by == []
    assert restored_dependent.blocks == []
    assert restored_dependent.blocked_by == [prerequisite.id]


def test_task_update_tool_applies_dependency_graph_and_reports_outputs():
    """task_update should wire dependencies into manager state and task list/get output."""
    manager = TaskManager()
    set_global_task_manager(manager)
    prerequisite = manager.create_task(TaskType.LOCAL_WORKFLOW, "Prepare work")
    dependent = manager.create_task(TaskType.LOCAL_WORKFLOW, "Ship work")

    result = TaskUpdateTool().execute(task_id=prerequisite.id, add_blocks=[dependent.id])

    assert result.success is True
    assert result.metadata["updated_dependencies"] == [dependent.id]
    assert prerequisite.blocks == [dependent.id]
    assert dependent.blocked_by == [prerequisite.id]
    assert manager.is_task_blocked(dependent.id) is True
    assert manager.get_open_blocker_ids(dependent.id) == [prerequisite.id]

    list_result = TaskListTool().execute()
    assert list_result.success is True
    assert f"blocked_by: {prerequisite.id} (open: {prerequisite.id})" in list_result.output
    assert f"blocks: {dependent.id}" in list_result.output
    assert "is_blocked: True" in list_result.output

    get_result = TaskGetTool().execute(task_id=dependent.id)
    assert get_result.success is True
    assert "Dependencies:" in get_result.output
    assert f"blocked_by: {prerequisite.id} (open: {prerequisite.id})" in get_result.output
    assert "is_blocked: True" in get_result.output
    assert get_result.metadata["task"]["blocked_by"] == [prerequisite.id]

    manager.update_task_status(prerequisite.id, TaskStatus.COMPLETED)

    unblocked_result = TaskGetTool().execute(task_id=dependent.id)
    assert manager.is_task_blocked(dependent.id) is False
    assert manager.get_open_blocker_ids(dependent.id) == []
    assert f"blocked_by: {prerequisite.id} (open: none)" in unblocked_result.output
    assert "is_blocked: False" in unblocked_result.output


def test_task_update_tool_rejects_invalid_dependencies():
    """task_update should reject missing tasks, self-dependencies, and cycles."""
    manager = TaskManager()
    set_global_task_manager(manager)
    first = manager.create_task(TaskType.LOCAL_WORKFLOW, "First")
    second = manager.create_task(TaskType.LOCAL_WORKFLOW, "Second")
    third = manager.create_task(TaskType.LOCAL_WORKFLOW, "Third")

    missing = TaskUpdateTool().execute(task_id=first.id, add_blocks=["wmissing"])
    assert missing.success is False
    assert missing.error == "Task 'wmissing' not found"

    self_dependency = TaskUpdateTool().execute(task_id=first.id, add_blocks=[first.id])
    assert self_dependency.success is False
    assert self_dependency.error == "A task cannot depend on itself"

    initial = TaskUpdateTool().execute(task_id=first.id, add_blocks=[second.id])
    assert initial.success is True

    cycle = TaskUpdateTool().execute(task_id=second.id, add_blocks=[first.id])
    assert cycle.success is False
    assert cycle.error == "Dependency cycle detected"

    reverse_form = TaskUpdateTool().execute(task_id=third.id, add_blocked_by=[second.id])
    assert reverse_form.success is True
    assert third.blocked_by == [second.id]
    assert second.blocks == [third.id]

def test_planner_prefers_llm_plan_for_broad_development_requests():
    """Broad coding tasks should use LLM planning before generic templates."""
    from opennova.planning.planner import Planner

    class LLMProvider(DummyProvider):
        async def chat(self, messages, tools=None, **kwargs):
            class Response:
                content = (
                    '{"task_summary": "Implement logout", '
                    '"steps": ['
                    '{"id": "step_1", "description": "Inspect the existing auth flow"}, '
                    '{"id": "step_2", "description": "Add the logout action and UI entry point"}'
                    ']}'
                )

            return Response()

    planner = Planner(LLMProvider())

    plan = asyncio.run(planner.create_plan("Implement logout flow in the CLI"))

    assert plan.task == "Implement logout"
    assert [step.description for step in plan.steps] == [
        "Inspect the existing auth flow",
        "Add the logout action and UI entry point",
    ]


def test_plan_mode_saves_plan_to_project_directory(tmp_path: Path):
    """Plan mode should persist generated plans to .opennova/plan with a timestamped filename."""

    class PlanSavingRuntime(AgentRuntime):
        def __init__(self):
            self.state = AgentState()
            self.llm = DummyProvider()
            self._callbacks = {}
            self.auto_confirm = False
            self.planner = None

        async def _create_plan(self, task: str):
            from opennova.runtime.state import Plan, PlanStep

            return Plan(
                task="Persist plan",
                steps=[PlanStep(id="step_1", description="Write plan to disk", tool_hint="write_file")],
            )

    previous_cwd = Path.cwd()
    try:
        import os

        os.chdir(tmp_path)
        runtime = PlanSavingRuntime()
        captured = {}

        def on_plan(plan, plan_file_path=None):
            captured["plan"] = plan
            captured["plan_file_path"] = Path(plan_file_path) if plan_file_path else None

        runtime.register_callback("plan", on_plan)
        result = asyncio.run(runtime.run("Persist this plan", mode="plan", stream=False))

        assert result == "Plan ready for approval"
        assert captured["plan_file_path"] is not None
        assert runtime.state.plan_file_path == captured["plan_file_path"]
        assert runtime.state.current_plan is not None
        assert runtime.state.plan_approval_status == PlanApprovalStatus.AWAITING_APPROVAL
        assert runtime.state.requires_confirmation is True
        assert runtime.state.mode == "plan"
        assert captured["plan_file_path"].parent == Path(".opennova") / "plan"
        assert captured["plan_file_path"].name.startswith("plan_")
        assert captured["plan_file_path"].suffix == ".md"
        assert len(captured["plan_file_path"].stem.replace("plan_", "")) == 15
        assert captured["plan_file_path"].exists()

        saved_content = captured["plan_file_path"].read_text(encoding="utf-8")
        assert "# Saved Plan: Persist plan" in saved_content
        assert "- Task: Persist this plan" in saved_content
        assert "- Saved path: .opennova/plan/" in saved_content
        assert "### step_1" in saved_content
        assert "- Description: Write plan to disk" in saved_content
        assert "- Status: `pending`" in saved_content
    finally:
        os.chdir(previous_cwd)


def test_agent_runtime_loads_legacy_saved_plan_format():
    from opennova.runtime.agent import AgentRuntime

    runtime = AgentRuntime.__new__(AgentRuntime)
    content = """# Saved Plan: Persist plan

- Task: Persist this plan
- Generated at: 2026-06-23T10:00:00
- Saved path: .opennova/plan/plan_20260623_100000.md

## Summary

Summary text

## Steps

1. **step_1** — Write plan to disk
   - Tool hint: `write_file`
   - Status: `done`
   - Result: wrote file

2. **step_2** — Review results
   - Status: `pending`
"""

    plan = AgentRuntime._load_plan_from_markdown(runtime, content)

    assert plan.task == "Persist plan"
    assert [step.id for step in plan.steps] == ["step_1", "step_2"]
    assert plan.steps[0].tool_hint == "write_file"
    assert plan.steps[0].status.value == "done"
    assert plan.steps[0].result_summary == "wrote file"
    assert plan.steps[1].status.value == "pending"


def test_agent_runtime_plan_markdown_round_trip_preserves_status_fields(tmp_path: Path):
    from opennova.runtime.state import Plan, PlanStep, StepStatus

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    plan = Plan(
        task="Round trip plan",
        steps=[
            PlanStep(
                id="step_1",
                description="Inspect plan state",
                status=StepStatus.RUNNING,
                tool_hint="read_file",
                result_summary="looked at file",
            ),
            PlanStep(
                id="step_2",
                description="Handle failure",
                status=StepStatus.FAILED,
                error="boom",
            ),
        ],
    )
    plan_path = tmp_path / "plan.md"

    content = AgentRuntime._render_saved_plan(runtime, plan, plan_path)
    loaded = AgentRuntime._load_plan_from_markdown(runtime, content)

    assert loaded.task == "Round trip plan"
    assert loaded.steps[0].status.value == "running"
    assert loaded.steps[0].tool_hint == "read_file"
    assert loaded.steps[0].result_summary == "looked at file"
    assert loaded.steps[1].status.value == "failed"
    assert loaded.steps[1].error == "boom"


def test_agent_runtime_load_plan_ignores_non_step_markdown_headings():
    from opennova.runtime.agent import AgentRuntime
    from opennova.tools.todo_tools import TodoWriteTool

    runtime = AgentRuntime.__new__(AgentRuntime)
    content = """# Saved Plan: Food animation

- Task: Plan the development

## Summary

### What the document covers

This is documentation content, not a plan step.

## Steps

### step_1
- Description: Define food animation requirements
- Status: `done`
- Result: Requirements documented.
### What the document covers
This nested result heading should not become a step.

### step_2
- Description: Implement the animation controller
- Status: `pending`
"""

    plan = AgentRuntime._load_plan_from_markdown(runtime, content)
    runtime._sync_plan_progress = AgentRuntime._sync_plan_progress.__get__(runtime, AgentRuntime)

    assert [(step.id, step.description) for step in plan.steps] == [
        ("step_1", "Define food animation requirements"),
        ("step_2", "Implement the animation controller"),
    ]

    runtime._sync_plan_progress(plan)

    assert TodoWriteTool.current_todos() == [
        {"id": "step_1", "content": "Define food animation requirements", "status": "done"},
        {"id": "step_2", "content": "Implement the animation controller", "status": "pending"},
    ]


def test_agent_runtime_sync_plan_progress_falls_back_for_empty_descriptions():
    from opennova.runtime.state import Plan, PlanStep
    from opennova.tools.todo_tools import TodoWriteTool

    runtime = AgentRuntime.__new__(AgentRuntime)
    plan = Plan(task="Recover todos", steps=[PlanStep(id="step_1", description="")])

    AgentRuntime._sync_plan_progress(runtime, plan)

    assert TodoWriteTool.current_todos() == [
        {"id": "step_1", "content": "step_1", "status": "pending"},
    ]


def test_agent_runtime_execute_approved_plan_runs_steps():
    """Approved plans should execute only after explicit approval."""
    from opennova.runtime.state import Plan, PlanStep

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.show_thinking = True
    runtime.state.set_plan(Plan(task="Execute plan", steps=[PlanStep(id="step_1", description="Do thing")]))
    runtime.state.set_plan_file_path("saved-plan.md")
    runtime.state.mark_plan_awaiting_approval()
    runtime.state.mark_plan_approved()

    captured_tasks = []
    emitted_thoughts = []
    runtime._emit = lambda event, *args: emitted_thoughts.append(args[0]) if event == "thought" else None
    runtime._sync_plan_progress = lambda plan, active_step_id=None: None
    runtime._persist_current_plan = lambda: None
    runtime._refresh_plan_from_file = lambda: runtime.state.current_plan

    async def fake_run_act_mode(task: str, stream: bool = True, progress_callback=None, preserve_plan_state: bool = False):
        captured_tasks.append((task, preserve_plan_state))
        runtime.state.last_result = f"done: {task}"
        return f"done: {task}"

    runtime._run_act_mode = fake_run_act_mode
    runtime._should_continue_on_failure = lambda: False

    result = asyncio.run(AgentRuntime.execute_approved_plan(runtime, stream=False))

    assert "Current step (step_1): Do thing" in result
    assert captured_tasks
    assert captured_tasks[0][1] is True
    assert "Overall plan: Execute plan" in captured_tasks[0][0]
    assert "Current step (step_1): Do thing" in captured_tasks[0][0]
    assert "Plan file: saved-plan.md" in captured_tasks[0][0]
    assert "Complete plan snapshot:" in captured_tasks[0][0]
    assert runtime.state.plan_approval_status == PlanApprovalStatus.NONE
    assert runtime.state.mode == "act"
    assert runtime.state.current_plan is None
    assert runtime.state.plan_file_path is None
    assert emitted_thoughts == ["Executing plan step step_1: Do thing"]


def test_agent_runtime_execute_approved_plan_skips_completed_steps():
    """Approved plan execution should resume from the next pending step."""
    from opennova.runtime.state import Plan, PlanStep, StepStatus

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.show_thinking = True
    plan = Plan(
        task="Execute plan",
        steps=[
            PlanStep(id="step_1", description="Already done", status=StepStatus.DONE),
            PlanStep(id="step_2", description="Do next"),
        ],
    )
    runtime.state.set_plan(plan)
    runtime.state.set_plan_file_path("saved-plan.md")
    runtime.state.mark_plan_awaiting_approval()
    runtime.state.mark_plan_approved()

    captured_tasks = []
    emitted_thoughts = []
    runtime._emit = lambda event, *args: emitted_thoughts.append(args[0]) if event == "thought" else None
    runtime._sync_plan_progress = lambda plan, active_step_id=None: None
    runtime._persist_current_plan = lambda: None
    runtime._refresh_plan_from_file = lambda: runtime.state.current_plan

    async def fake_run_act_mode(task: str, stream: bool = True, progress_callback=None, preserve_plan_state: bool = False):
        captured_tasks.append((task, preserve_plan_state))
        runtime.state.last_result = f"done: {task}"
        return f"done: {task}"

    runtime._run_act_mode = fake_run_act_mode
    runtime._should_continue_on_failure = lambda: False

    result = asyncio.run(AgentRuntime.execute_approved_plan(runtime, stream=False))

    assert "Current step (step_2): Do next" in result
    assert len(captured_tasks) == 1
    assert "Current step (step_2): Do next" in captured_tasks[0][0]
    assert emitted_thoughts == ["Executing plan step step_2: Do next"]
    assert runtime.state.current_plan is None
    assert runtime.state.plan_approval_status == PlanApprovalStatus.NONE


def test_agent_runtime_execute_approved_plan_marks_failures_for_inspection():
    """Failed approved plan execution should preserve the failed plan for inspection."""
    from opennova.runtime.state import Plan, PlanStatus, PlanStep, StepStatus

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.show_thinking = True
    runtime.state.set_plan(Plan(task="Execute plan", steps=[PlanStep(id="step_1", description="Do thing")]))
    runtime.state.set_plan_file_path("saved-plan.md")
    runtime.state.mark_plan_awaiting_approval()
    runtime.state.mark_plan_approved()
    emitted_plan_statuses = []

    def fake_emit(event: str, plan_obj=None, plan_file_path=None):
        if event == "plan" and plan_obj is not None:
            emitted_plan_statuses.append([step.status.value for step in plan_obj.steps])

    runtime._emit = fake_emit
    runtime._sync_plan_progress = lambda plan, active_step_id=None: None
    runtime._persist_current_plan = lambda: None
    runtime._refresh_plan_from_file = lambda: runtime.state.current_plan

    async def fake_run_act_mode(task: str, stream: bool = True, progress_callback=None, preserve_plan_state: bool = False):
        runtime.state.last_result = f"Task failed: {task}"
        return runtime.state.last_result

    runtime._run_act_mode = fake_run_act_mode
    runtime._should_continue_on_failure = lambda: False

    result = asyncio.run(AgentRuntime.execute_approved_plan(runtime, stream=False))

    assert result.startswith("Task failed:")
    assert runtime.state.current_plan is not None
    assert runtime.state.plan_file_path is not None
    assert runtime.state.plan_approval_status == PlanApprovalStatus.FAILED
    assert runtime.state.current_plan.status == PlanStatus.FAILED
    assert runtime.state.current_plan.steps[0].status == StepStatus.FAILED
    assert runtime.state.current_plan.steps[0].error == result


def test_agent_runtime_execute_approved_plan_refreshes_plan_from_file_and_updates_progress():
    from opennova.runtime.state import Plan, PlanStep, StepStatus
    from opennova.tools.todo_tools import TodoWriteTool

    TodoWriteTool.replace_todos([])
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.show_thinking = True
    runtime.state.set_plan(
        Plan(
            task="Execute plan",
            steps=[
                PlanStep(id="step_1", description="Stale description"),
                PlanStep(id="step_2", description="Already done", status=StepStatus.DONE),
            ],
        )
    )
    runtime.state.set_plan_file_path("saved-plan.md")
    runtime.state.mark_plan_awaiting_approval()
    runtime.state.mark_plan_approved()
    emitted_plan_statuses = []

    def fake_emit(event: str, plan_obj=None, plan_file_path=None):
        if event == "plan" and plan_obj is not None:
            emitted_plan_statuses.append([step.status.value for step in plan_obj.steps])

    runtime._emit = fake_emit

    refreshed_plan = Plan(
        task="Execute plan",
        steps=[
            PlanStep(id="step_1", description="Fresh description from file"),
            PlanStep(id="step_2", description="Already done", status=StepStatus.DONE),
        ],
    )
    persisted_statuses = []

    def fake_refresh():
        runtime.state.current_plan = refreshed_plan
        return refreshed_plan

    def fake_persist():
        persisted_statuses.append(
            [(step.id, step.status.value, step.result_summary, step.error) for step in runtime.state.current_plan.steps]
        )

    runtime._refresh_plan_from_file = fake_refresh
    runtime._persist_current_plan = fake_persist
    runtime._sync_plan_progress = AgentRuntime._sync_plan_progress.__get__(runtime, AgentRuntime)

    captured_tasks = []

    async def fake_run_act_mode(task: str, stream: bool = True, progress_callback=None, preserve_plan_state: bool = False):
        captured_tasks.append(task)
        runtime.state.last_result = "done from execution"
        return "done from execution"

    runtime._run_act_mode = fake_run_act_mode
    runtime._should_continue_on_failure = lambda: False

    result = asyncio.run(AgentRuntime.execute_approved_plan(runtime, stream=False))

    assert result == "done from execution"
    assert captured_tasks == [captured_tasks[0]]
    assert "Fresh description from file" in captured_tasks[0]
    assert persisted_statuses[0][0][1] == "pending"
    assert persisted_statuses[1][0][1] == "running"
    assert persisted_statuses[-1][0][1] == "done"
    assert ["running", "done"] in emitted_plan_statuses
    assert ["done", "done"] in emitted_plan_statuses
    todos = TodoWriteTool.current_todos()
    assert todos == [
        {"id": "step_1", "content": "Fresh description from file", "status": "done"},
        {"id": "step_2", "content": "Already done", "status": "done"},
    ]


def test_agent_runtime_execute_approved_plan_continues_after_each_step_completion():
    """Per-step act completion should not stop the outer approved-plan loop."""
    from opennova.runtime.state import Plan, PlanStep
    from opennova.tools.todo_tools import TodoWriteTool

    TodoWriteTool.replace_todos([])
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.show_thinking = True
    runtime.state.set_plan(
        Plan(
            task="Execute multi-step plan",
            steps=[
                PlanStep(id="step_1", description="First step"),
                PlanStep(id="step_2", description="Second step"),
                PlanStep(id="step_3", description="Third step"),
            ],
        )
    )
    runtime.state.set_plan_file_path("saved-plan.md")
    runtime.state.mark_plan_awaiting_approval()
    runtime.state.mark_plan_approved()
    runtime._emit = lambda *args, **kwargs: None
    runtime._refresh_plan_from_file = lambda: runtime.state.current_plan
    runtime._persist_current_plan = lambda: None
    runtime._sync_plan_progress = AgentRuntime._sync_plan_progress.__get__(runtime, AgentRuntime)

    captured_tasks: list[str] = []

    async def fake_run_act_mode(task: str, stream: bool = True, progress_callback=None, preserve_plan_state: bool = False):
        captured_tasks.append(task)
        runtime.state.is_complete = True
        runtime.state.last_result = f"done {len(captured_tasks)}"
        return runtime.state.last_result

    runtime._run_act_mode = fake_run_act_mode
    runtime._should_continue_on_failure = lambda: False

    result = asyncio.run(AgentRuntime.execute_approved_plan(runtime, stream=False))

    assert result == "done 3"
    assert len(captured_tasks) == 3
    assert TodoWriteTool.current_todos() == [
        {"id": "step_1", "content": "First step", "status": "done"},
        {"id": "step_2", "content": "Second step", "status": "done"},
        {"id": "step_3", "content": "Third step", "status": "done"},
    ]


def test_agent_runtime_execute_approved_plan_resumes_failed_and_running_steps():
    """Interrupted plans should requeue incomplete steps instead of falling out of plan execution."""
    from opennova.runtime.state import Plan, PlanStatus, PlanStep, StepStatus
    from opennova.tools.todo_tools import TodoWriteTool

    TodoWriteTool.replace_todos([])
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.show_thinking = True
    plan = Plan(
        task="Resume interrupted plan",
        steps=[
            PlanStep(id="step_1", description="Already done", status=StepStatus.DONE),
            PlanStep(id="step_2", description="Retry failed", status=StepStatus.FAILED, error="old failure"),
            PlanStep(id="step_3", description="Retry running", status=StepStatus.RUNNING),
            PlanStep(id="step_4", description="Do pending"),
        ],
        status=PlanStatus.FAILED,
    )
    runtime.state.set_plan(plan)
    runtime.state.set_plan_file_path("saved-plan.md")
    runtime.state.mark_plan_failed()
    runtime._emit = lambda *args, **kwargs: None
    runtime._refresh_plan_from_file = lambda: runtime.state.current_plan
    runtime._persist_current_plan = lambda: None
    runtime._sync_plan_progress = AgentRuntime._sync_plan_progress.__get__(runtime, AgentRuntime)

    captured_steps: list[str] = []

    async def fake_run_act_mode(task: str, stream: bool = True, progress_callback=None, preserve_plan_state: bool = False):
        if "Current step (step_2): Retry failed" in task:
            captured_steps.append("step_2")
        elif "Current step (step_3): Retry running" in task:
            captured_steps.append("step_3")
        elif "Current step (step_4): Do pending" in task:
            captured_steps.append("step_4")
        runtime.state.last_result = f"done {captured_steps[-1]}"
        return runtime.state.last_result

    runtime._run_act_mode = fake_run_act_mode
    runtime._should_continue_on_failure = lambda: False

    result = asyncio.run(AgentRuntime.execute_approved_plan(runtime, stream=False))

    assert result == "done step_4"
    assert captured_steps == ["step_2", "step_3", "step_4"]
    assert runtime.state.current_plan is None
    assert runtime.state.plan_approval_status == PlanApprovalStatus.NONE
    assert TodoWriteTool.current_todos() == [
        {"id": "step_1", "content": "Already done", "status": "done"},
        {"id": "step_2", "content": "Retry failed", "status": "done"},
        {"id": "step_3", "content": "Retry running", "status": "done"},
        {"id": "step_4", "content": "Do pending", "status": "done"},
    ]


def test_agent_runtime_execute_approved_plan_requires_approval():
    """Plan execution should refuse to start before approval."""
    from opennova.runtime.state import Plan, PlanStep

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.state.set_plan(Plan(task="Execute plan", steps=[PlanStep(id="step_1", description="Do thing")]))
    runtime.state.mark_plan_awaiting_approval()

    result = asyncio.run(AgentRuntime.execute_approved_plan(runtime, stream=False))

    assert result == "Plan approval required before execution"
    assert runtime.state.plan_approval_status == PlanApprovalStatus.AWAITING_APPROVAL


def test_agent_runtime_create_plan_uses_shared_planner():
    """Runtime plan creation should delegate to the shared Planner instance."""

    class PlannerStub:
        def __init__(self):
            self.create_calls = []
            self.optimize_calls = []

        async def create_plan(self, task: str):
            from opennova.runtime.state import Plan, PlanStep

            self.create_calls.append(task)
            return Plan(task=task, steps=[PlanStep(id="step_1", description="stub step")])

        def optimize_plan(self, plan):
            self.optimize_calls.append(plan.task)
            return plan

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.planner = PlannerStub()

    plan = asyncio.run(AgentRuntime._create_plan(runtime, "Unify planning"))

    assert plan.task == "Unify planning"
    assert runtime.planner.create_calls == ["Unify planning"]
    assert runtime.planner.optimize_calls == ["Unify planning"]

def test_agent_runtime_clear_conversation_resets_context_and_state():
    """Clearing the runtime conversation should empty context and reset state."""
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.context_manager = ContextManager(model="gpt-4o")
    runtime.state = AgentState()
    runtime.context_manager.add_user_message("hello")
    runtime.state.reset("Do work")

    AgentRuntime.clear_conversation(runtime)

    assert len(runtime.context_manager) == 0
    assert runtime.state.current_task == ""


def test_run_single_task_plan_mode_executes_after_confirmation():
    """CLI plan mode should approve and execute on the same runtime instance."""
    from opennova.main import _run_single_task

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.register_callback = lambda event, callback: None

    async def fake_run(task: str, mode: str = "act", stream: bool = True, progress_callback=None):
        from opennova.runtime.state import Plan, PlanStep

        runtime.state.reset(task)
        runtime.state.set_mode(mode)
        runtime.state.set_plan(Plan(task="Approved plan", steps=[PlanStep(id="step_1", description="Ship it")]))
        runtime.state.mark_plan_awaiting_approval()
        return "Plan ready for approval"

    async def fake_execute(stream: bool = True):
        return "executed approved plan"

    runtime.run = fake_run
    runtime.execute_approved_plan = fake_execute

    with patch("opennova.main.AgentRuntime", return_value=runtime), patch(
        "opennova.main.click.confirm", return_value=True
    ):
        asyncio.run(_run_single_task(config=None, task="Do work", plan_mode=True, stream=False))

    assert runtime.state.plan_approval_status == PlanApprovalStatus.APPROVED


def test_run_single_task_plan_mode_decline_keeps_plan_waiting():
    """CLI plan mode should keep the saved plan awaiting approval when execution is declined."""
    from opennova.main import _run_single_task

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.register_callback = lambda event, callback: None
    executed = []

    async def fake_run(task: str, mode: str = "act", stream: bool = True, progress_callback=None):
        from opennova.runtime.state import Plan, PlanStep

        runtime.state.reset(task)
        runtime.state.set_mode(mode)
        runtime.state.set_plan(Plan(task="Approved plan", steps=[PlanStep(id="step_1", description="Ship it")]))
        runtime.state.mark_plan_awaiting_approval()
        return "Plan ready for approval"

    async def fake_execute(stream: bool = True):
        executed.append(True)
        return "executed approved plan"

    runtime.run = fake_run
    runtime.execute_approved_plan = fake_execute

    with patch("opennova.main.AgentRuntime", return_value=runtime), patch(
        "opennova.main.click.confirm", return_value=False
    ):
        asyncio.run(_run_single_task(config=None, task="Do work", plan_mode=True, stream=False))

    assert executed == []
    assert runtime.state.plan_approval_status == PlanApprovalStatus.AWAITING_APPROVAL


def test_exit_plan_mode_tool_without_runtime_state_uses_safe_default_metadata():
    """ExitPlanModeTool should expose stable fallback metadata without shared runtime state."""
    tool = ExitPlanModeTool(config={})

    result = tool.execute()

    assert result.success is True
    assert result.metadata["mode"] == "plan"
    assert result.metadata["current_mode"] == "plan"
    assert result.metadata["has_plan"] is False
    assert result.metadata["plan_file_path"] is None
    assert result.metadata["requires_confirmation"] is True
    assert result.metadata["plan_approval_status"] == "awaiting_approval"
    assert result.metadata["status"] == "awaiting_approval"

def test_enter_plan_mode_tool_updates_runtime_state():
    """Entering plan mode via the tool should update the shared runtime state."""
    state = AgentState()
    tool = EnterPlanModeTool(config={"state": state})

    result = tool.execute()

    assert result.success is True
    assert state.mode == "plan"
    assert result.metadata["mode"] == "plan"
    assert result.metadata["current_mode"] == "plan"
    assert result.metadata["has_plan"] is False
    assert result.metadata["plan_approval_status"] == "none"


def test_exit_plan_mode_tool_reports_runtime_plan_state(tmp_path: Path):
    """Exiting plan mode should expose the runtime plan state and saved plan path."""
    from opennova.runtime.state import Plan, PlanStep

    state = AgentState()
    state.set_mode("plan")
    state.set_plan(Plan(task="Saved plan", steps=[PlanStep(id="step_1", description="review")]))
    state.set_plan_file_path(tmp_path / "plan.md")
    tool = ExitPlanModeTool(config={"state": state})

    result = tool.execute()

    assert result.success is True
    assert state.requires_confirmation is True
    assert state.plan_approval_status == PlanApprovalStatus.AWAITING_APPROVAL
    assert result.metadata["status"] == "awaiting_approval"
    assert result.metadata["mode"] == "plan"
    assert result.metadata["has_plan"] is True
    assert result.metadata["plan_file_path"].endswith("plan.md")
    assert result.metadata["requires_confirmation"] is True
    assert result.metadata["plan_approval_status"] == "awaiting_approval"


def test_exit_plan_mode_tool_materializes_markdown_plan_into_runtime_state():
    """ExitPlanModeTool should turn a written markdown plan into executable runtime state."""
    from opennova.runtime.state import StepStatus
    from opennova.tools.todo_tools import TodoWriteTool

    TodoWriteTool.replace_todos([])
    state = AgentState()
    state.set_mode("plan")
    tool = ExitPlanModeTool(config={"state": state})

    result = tool.execute(
        task="Improve plan mode",
        plan="1. Inspect current plan flow\n2. Fix plan approval\n3. Verify todos",
    )

    assert result.success is True
    assert state.current_plan is not None
    assert state.current_plan.task == "Improve plan mode"
    assert [step.description for step in state.current_plan.steps] == [
        "Inspect current plan flow",
        "Fix plan approval",
        "Verify todos",
    ]
    assert all(step.status == StepStatus.PENDING for step in state.current_plan.steps)
    assert state.plan_approval_status == PlanApprovalStatus.AWAITING_APPROVAL
    assert TodoWriteTool.current_todos() == [
        {"id": "step_1", "content": "Inspect current plan flow", "status": "pending"},
        {"id": "step_2", "content": "Fix plan approval", "status": "pending"},
        {"id": "step_3", "content": "Verify todos", "status": "pending"},
    ]


def test_exit_plan_mode_tool_replaces_existing_plan_when_revision_is_provided(tmp_path: Path):
    """A revised plan submitted after continue-conversation should replace the old plan."""
    from opennova.runtime.state import Plan, PlanStep
    from opennova.tools.todo_tools import TodoWriteTool

    TodoWriteTool.replace_todos([])
    state = AgentState()
    state.set_plan(Plan(task="Old plan", steps=[PlanStep(id="step_1", description="Old step")]))
    state.set_plan_file_path(tmp_path / "plan.md")
    persisted: list[bool] = []
    emitted: list[str] = []

    runtime = type(
        "Runtime",
        (),
        {
            "_persist_current_plan": lambda self: persisted.append(True),
            "_emit": lambda self, event, plan, path: emitted.append(plan.task),
        },
    )()
    tool = ExitPlanModeTool(config={"state": state, "runtime": runtime})

    result = tool.execute(
        task="Revised plan",
        steps=[{"description": "New first step"}, {"description": "New second step"}],
    )

    assert result.success is True
    assert state.current_plan is not None
    assert state.current_plan.task == "Revised plan"
    assert [step.description for step in state.current_plan.steps] == [
        "New first step",
        "New second step",
    ]
    assert persisted == [True]
    assert emitted[-1] == "Revised plan"
    assert TodoWriteTool.current_todos() == [
        {"id": "step_1", "content": "New first step", "status": "pending"},
        {"id": "step_2", "content": "New second step", "status": "pending"},
    ]


def test_enter_plan_mode_tool_mentions_reusing_existing_plan(tmp_path: Path):
    state = AgentState()
    state.set_plan_file_path(tmp_path / "plan.md")
    tool = EnterPlanModeTool(config={"state": state})

    result = tool.execute()

    assert result.success is True
    assert "read the existing saved plan first" in result.metadata["instructions"].lower()


def test_react_loop_system_prompt_requires_enter_plan_mode_when_user_requests_plan_first():
    from opennova.memory.context import ContextManager
    from opennova.runtime.loop import ReActLoop
    from opennova.runtime.state import AgentState
    from opennova.tools.base import ToolRegistry

    class LLM:
        model = "test-model"

    loop = ReActLoop(
        llm=LLM(),
        tool_registry=ToolRegistry(),
        state=AgentState(),
        context_manager=ContextManager(),
    )

    prompt = loop._build_system_prompt()

    assert "If the user asks you to plan before coding" in prompt
    assert "call enter_plan_mode before any implementation or file modification tool" in prompt
    assert "Do not modify files before exit_plan_mode has requested user approval" in prompt


def test_react_loop_blocks_implementation_tools_while_in_plan_mode():
    from opennova.runtime.loop import ParsedAction

    state = AgentState()
    state.set_mode("plan")
    loop = ReActLoop(
        llm=object(),
        tool_registry=ToolRegistry(),
        state=state,
        context_manager=ContextManager(),
        guardrails=None,
    )

    result = loop._check_tool_guard(
        ParsedAction(tool_name="edit_file", arguments={"file_path": "snake_game.py"})
    )

    assert result.allowed is False
    assert result.risk_level == RiskLevel.BLOCK
    assert "plan mode" in result.reason.lower()
    assert "exit_plan_mode" in result.reason


def test_react_loop_allows_research_tools_while_in_plan_mode_without_guardrails():
    from opennova.runtime.loop import ParsedAction

    state = AgentState()
    state.set_mode("plan")
    loop = ReActLoop(
        llm=object(),
        tool_registry=ToolRegistry(),
        state=state,
        context_manager=ContextManager(),
        guardrails=None,
    )

    result = loop._check_tool_guard(
        ParsedAction(tool_name="read_file", arguments={"file_path": "snake_game.py"})
    )

    assert result.allowed is True


def test_react_loop_does_not_execute_edit_tool_after_entering_plan_mode():
    from opennova.runtime.loop import ParsedAction
    from opennova.tools.plan_mode_tools import EnterPlanModeTool

    class EditTool(BaseTool):
        name = "edit_file"
        description = "Edit a file"

        def __init__(self):
            super().__init__()
            self.called = False

        def execute(self, **kwargs):
            self.called = True
            return ToolResult(success=True, output="edited")

    state = AgentState()
    registry = ToolRegistry()
    registry.register(EnterPlanModeTool(config={"state": state}))
    edit_tool = EditTool()
    registry.register(edit_tool)
    loop = ReActLoop(
        llm=object(),
        tool_registry=registry,
        state=state,
        context_manager=ContextManager(),
        guardrails=None,
    )

    enter_result = asyncio.run(loop._act(ParsedAction(tool_name="enter_plan_mode", arguments={})))
    edit_result = asyncio.run(
        loop._act(ParsedAction(tool_name="edit_file", arguments={"file_path": "snake_game.py"}))
    )

    assert enter_result.success is True
    assert state.mode == "plan"
    assert edit_result.success is False
    assert edit_result.metadata["guard_blocked"] is True
    assert edit_result.metadata["plan_mode_blocked"] is True
    assert edit_tool.called is False


def test_enter_plan_mode_tool_requires_respecting_plan_first_requests():
    tool = EnterPlanModeTool(config={"state": AgentState()})

    result = tool.execute()

    assert result.success is True
    assert "If the user explicitly asked to plan before implementation" in result.metadata["instructions"]


def test_exit_plan_mode_tool_requires_existing_plan():
    state = AgentState()
    state.set_mode("plan")
    tool = ExitPlanModeTool(config={"state": state})

    result = tool.execute()

    assert result.success is False
    assert "no plan is available" in result.error.lower()


def test_agent_runtime_run_approval_text_executes_awaiting_plan_without_resetting_state():
    """A follow-up like 'start coding' should approve and execute the existing plan."""
    from opennova.runtime.state import Plan, PlanStep

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.state.set_plan(Plan(task="Pending plan", steps=[PlanStep(id="step_1", description="Do it")]))
    runtime.state.mark_plan_awaiting_approval()
    executed: list[bool] = []

    async def fake_execute(stream: bool = True):
        executed.append(True)
        return "executed existing plan"

    runtime.execute_approved_plan = fake_execute

    result = asyncio.run(AgentRuntime.run(runtime, "开始写代码", mode="act", stream=False))

    assert result == "executed existing plan"
    assert executed == [True]
    assert runtime.state.current_plan is not None
    assert runtime.state.current_plan.task == "Pending plan"
    assert runtime.state.plan_approval_status == PlanApprovalStatus.APPROVED


def test_agent_runtime_run_development_approval_text_executes_awaiting_plan():
    """Chinese follow-ups like '开始开发' should approve and execute the existing plan."""
    from opennova.runtime.state import Plan, PlanStep

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    runtime.state.set_plan(Plan(task="Pending plan", steps=[PlanStep(id="step_1", description="Do it")]))
    runtime.state.mark_plan_awaiting_approval()
    executed: list[bool] = []

    async def fake_execute(stream: bool = True):
        executed.append(True)
        return "executed existing plan"

    runtime.execute_approved_plan = fake_execute

    result = asyncio.run(AgentRuntime.run(runtime, "开始开发", mode="act", stream=False))

    assert result == "executed existing plan"
    assert executed == [True]
    assert runtime.state.plan_approval_status == PlanApprovalStatus.APPROVED


def test_react_loop_exit_plan_mode_observation_marks_turn_complete():
    """A successful exit_plan_mode tool call should stop the current planning turn."""
    from opennova.runtime.loop import ParsedAction

    class Context:
        def __init__(self):
            self.messages = []

        def add_message(self, message):
            self.messages.append(message)

        async def add_message_and_compress(self, message):
            self.messages.append(message)

    state = AgentState()
    loop = ReActLoop.__new__(ReActLoop)
    loop.state = state
    loop.context_manager = Context()
    loop.skill_registry = None
    loop.hook_manager = None

    action = ParsedAction(tool_name="exit_plan_mode", arguments={}, thought="Plan is ready")
    result = ToolResult(
        success=True,
        output="Plan mode exited. Awaiting user approval of the plan.",
        metadata={"status": "awaiting_approval"},
    )

    asyncio.run(ReActLoop._observe(loop, action, result))

    assert state.is_complete is True
    assert state.last_action == "exit_plan_mode"




class Completed:
    def __init__(self, stdout='', stderr='', returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_ask_user_question_supports_free_text_and_choice_modes():
    tool = AskUserQuestionTool()

    # 0 options → free-text mode
    no_options = tool.execute(question="What's your name?")
    assert no_options.success is True
    assert no_options.metadata["prompt_payload"]["free_text"] is True

    # 1 option → free-text mode
    one_option = tool.execute(question='Pick one?', options=[{'label': 'Only', 'description': 'One'}])
    assert one_option.success is True
    assert one_option.metadata["prompt_payload"]["free_text"] is True

    # 2+ options → choice mode
    two_options = tool.execute(
        question='Pick one?',
        options=[
            {'label': 'A', 'description': 'First'},
            {'label': 'B', 'description': 'Second'},
        ],
    )
    assert two_options.success is True
    assert two_options.metadata["prompt_payload"]["free_text"] is False

    # 5+ options → still works (no upper limit)
    five_options = tool.execute(
        question='Pick one?',
        options=[
            {'label': '1', 'description': 'one'},
            {'label': '2', 'description': 'two'},
            {'label': '3', 'description': 'three'},
            {'label': '4', 'description': 'four'},
            {'label': '5', 'description': 'five'},
        ],
    )
    assert five_options.success is True
    assert five_options.metadata["prompt_payload"]["free_text"] is False


def test_ask_user_question_formats_single_select_header_and_preview():
    tool = AskUserQuestionTool()
    preview = 'x' * 105

    result = tool.execute(
        question='Which approach should we use?',
        header='Approach',
        options=[
            {
                'label': 'Option A',
                'description': 'Safer path',
                'preview': preview,
            },
            {
                'label': 'Option B',
                'description': 'Faster path',
            },
        ],
    )

    assert result.success is True
    assert 'Question: Which approach should we use?' in result.output
    assert '[Approach]' in result.output
    assert '(Select one option)' in result.output
    assert 'Preview: ' + ('x' * 100) + '...' in result.output
    assert result.metadata['questions'][0]['header'] == 'Approach'
    assert result.metadata['questions'][0]['multiSelect'] is False
    assert result.metadata['interaction_required'] is True
    assert result.metadata['prompt_payload']['header'] == 'Approach'


def test_ask_user_question_formats_multi_select_prompt():
    tool = AskUserQuestionTool()

    result = tool.execute(
        question='Which features do you want?',
        multi_select=True,
        options=[
            {'label': 'A', 'description': 'Alpha'},
            {'label': 'B', 'description': 'Beta'},
        ],
    )

    assert result.success is True
    assert '(Select multiple options, comma-separated)' in result.output
    assert result.metadata['questions'][0]['multiSelect'] is True


def test_web_search_returns_explicit_unconfigured_error_with_metadata():
    result = WebSearchTool().execute(query='latest docs', num_results=10)

    assert result.success is False
    assert result.error == 'Web search is not configured in this runtime.'
    assert result.metadata['query'] == 'latest docs'
    assert result.metadata['count'] == 0
    assert result.metadata['requested_count'] == 10
    assert result.metadata['current_year'] >= 2026


def test_web_fetch_rejects_invalid_url():
    result = WebFetchTool().execute(url='not-a-url')

    assert result.success is False
    assert result.error == 'Invalid URL: not-a-url'


def test_web_fetch_extracts_plain_text_from_html():
    tool = WebFetchTool()

    result = tool._extract_content('<html><body><h1>Docs</h1><p>Hello <b>world</b></p></body></html>', 'text/html')

    assert 'Docs' in result
    assert 'Hello world' in result


def test_web_fetch_handles_urlparse_failure():
    tool = WebFetchTool()

    with patch('opennova.tools.web_tools.urlparse', side_effect=ValueError('parse failed')):
        result = tool.execute(url='https://example.com')

    assert result.success is False
    assert result.error == 'parse failed'


def test_git_status_parse_and_execute_reports_sections():
    tool = GitStatusTool()
    status_output = 'MM staged_and_modified.py\nM  unstaged.py\nA  added.py\nD  deleted.py\n?? new_file.py\n'

    with patch('opennova.tools.git_tools.subprocess.run') as mock_run:
        mock_run.side_effect = [
            Completed(stdout=status_output),
            Completed(stdout='feature/test\n'),
        ]

        result = tool.execute()

    assert result.success is True
    assert result.metadata['branch'] == 'feature/test'
    assert result.metadata['staged'] == ['staged_and_modified.py', 'added.py']
    assert result.metadata['unstaged'] == ['unstaged.py', 'deleted.py']
    assert result.metadata['untracked'] == ['new_file.py']
    assert 'Staged changes:' in result.output
    assert 'Unstaged changes:' in result.output
    assert 'Untracked files:' in result.output


def test_git_status_handles_clean_repo_and_unknown_branch():
    tool = GitStatusTool()

    with patch('opennova.tools.git_tools.subprocess.run') as mock_run:
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, ['git', 'branch']),
        ]

        parsed = tool._parse_git_status('')

    assert parsed.branch == 'unknown'
    assert parsed.has_changes is False

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout='')):
        result = tool.execute()

    assert result.success is True
    assert '(No changes)' in result.output
    assert result.metadata['has_changes'] is False


def test_git_diff_supports_cached_empty_and_truncation():
    tool = GitDiffTool()
    long_diff = 'a' * 10001

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout='')) as mock_run:
        empty_result = tool.execute()
        assert empty_result.success is True
        assert empty_result.output == 'No changes to show.'
        assert mock_run.call_args.args[0] == ['git', 'diff']

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout=long_diff)) as mock_run:
        cached_result = tool.execute(cached=True)
        assert cached_result.success is True
        assert cached_result.metadata['cached'] is True
        assert cached_result.output.endswith('... (diff truncated)')
        assert mock_run.call_args.args[0] == ['git', 'diff', '--cached']


def test_git_diff_handles_subprocess_failure():
    tool = GitDiffTool()

    with patch('opennova.tools.git_tools.subprocess.run', side_effect=RuntimeError('diff failed')):
        result = tool.execute()

    assert result.success is False
    assert result.error == 'diff failed'


def test_git_log_handles_normal_empty_and_failure_cases():
    tool = GitLogTool()

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout='abc123 first\ndef456 second\n')):
        result = tool.execute(max_count=2)
    assert result.success is True
    assert result.metadata['count'] == 2
    assert result.metadata['commits'] == ['abc123 first', 'def456 second']
    assert 'Recent commits:' in result.output

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout='   ')):
        empty = tool.execute()
    assert empty.success is True
    assert empty.output == 'No commits in history.'
    assert empty.metadata['commits'] == []

    with patch('opennova.tools.git_tools.subprocess.run', side_effect=RuntimeError('log failed')):
        failed = tool.execute()
    assert failed.success is False
    assert failed.error == 'log failed'


def test_git_commit_uses_explicit_message_and_amend():
    tool = GitCommitTool()

    with patch('opennova.tools.git_tools.subprocess.run') as mock_run:
        mock_run.side_effect = [
            Completed(stdout='', returncode=0),
            Completed(stdout='12345678abcdef\n'),
        ]

        result = tool.execute(message='Fix bug', amend=True)

    assert result.success is True
    assert result.metadata['commit_hash'] == '12345678'
    assert result.metadata['message'] == 'Fix bug'
    assert mock_run.call_args_list[0].args[0] == ['git', 'commit', '-m', 'Fix bug', '--amend']


def test_git_commit_generates_message_and_handles_failures():
    tool = GitCommitTool()

    with (
        patch.object(tool, '_generate_commit_message', return_value='Auto message') as mock_generate,
        patch('opennova.tools.git_tools.subprocess.run') as mock_run,
    ):
        mock_run.side_effect = [
            Completed(stdout='', returncode=0),
            Completed(stdout='abcdef123456\n'),
        ]
        result = tool.execute()

    assert result.success is True
    assert result.metadata['message'] == 'Auto message'
    mock_generate.assert_called_once()

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stderr='commit failed', returncode=1)):
        failed_commit = tool.execute(message='Broken')
    assert failed_commit.success is False
    assert failed_commit.error == 'commit failed'

    with patch('opennova.tools.git_tools.subprocess.run') as mock_run:
        mock_run.side_effect = [
            Completed(stdout='', returncode=0),
            RuntimeError('rev parse failed'),
        ]
        failed_hash = tool.execute(message='Broken hash')
    assert failed_hash.success is False
    assert failed_hash.error == 'rev parse failed'


def test_git_commit_message_generation_fallbacks():
    tool = GitCommitTool()

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout='file.py | 3 ++-\n')):
        message = tool._generate_commit_message()
    assert message == 'Update changes'

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout='src/a.py | 1 | +\nsrc/b.py | 2 | ++\n')):
        parsed_message = tool._generate_commit_message()
    assert parsed_message == 'Update code with changes to: src/a.py (+), src/b.py (++)'

    with patch('opennova.tools.git_tools.subprocess.run', side_effect=RuntimeError('stat failed')):
        fallback = tool._generate_commit_message()
    assert fallback == 'Update changes'


def test_git_branch_reports_current_branch_and_empty_state():
    tool = GitBranchTool()

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout='* master\n  feature/test\n  remotes/origin/master\n')):
        result = tool.execute()
    assert result.success is True
    assert result.metadata['current'] == 'master'
    assert result.metadata['branches'] == ['master', 'feature/test', 'remotes/origin/master']
    assert '* master (current)' in result.output

    with patch('opennova.tools.git_tools.subprocess.run', return_value=Completed(stdout='')):
        empty = tool.execute()
    assert empty.success is True
    assert empty.output == 'No branches found.'
    assert empty.metadata['branches'] == []

    with patch('opennova.tools.git_tools.subprocess.run', side_effect=RuntimeError('branch failed')):
        failed = tool.execute()
    assert failed.success is False
    assert failed.error == 'branch failed'


@pytest.mark.asyncio
async def test_react_loop_resolves_interactive_tool_results_via_callback():
    class InteractiveTool(BaseTool):
        name = 'interactive_tool'
        description = 'Interactive test tool'

        def execute(self, **kwargs):
            return ToolResult(
                success=True,
                output='Question: Choose one',
                metadata={
                    'interaction_required': True,
                    'prompt_payload': {
                        'question': 'Choose one',
                        'options': [
                            {'index': 1, 'label': 'Alpha', 'description': 'A'},
                            {'index': 2, 'label': 'Beta', 'description': 'B'},
                        ],
                        'multi_select': False,
                    },
                },
            )

    class Provider(DummyProvider):
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, **kwargs):
            from opennova.providers.base import FinishReason, LLMResponse, ToolCall
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    content='ask user',
                    tool_calls=[ToolCall(id='call_1', name='interactive_tool', arguments={})],
                    finish_reason=FinishReason.TOOL_CALL,
                )
            return LLMResponse(content='done', finish_reason=FinishReason.STOP)

    registry = ToolRegistry()
    registry.clear()
    registry.register(InteractiveTool())
    loop = ReActLoop(
        llm=Provider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        interaction_callback=lambda metadata: {
            'answer': 'Alpha',
            'answers': {'Choose one': 'Alpha'},
            'selected_options': [{'index': 1, 'label': 'Alpha'}],
            'display': 'Alpha',
        },
    )

    result = await loop.run('choose')

    assert result == 'done'
    assert any(message.role == 'tool' and message.content == 'Answer to: Choose one\nAlpha' for message in loop.messages)


def test_ask_user_question_exposes_interaction_metadata_contract():
    tool = AskUserQuestionTool()

    result = tool.execute(
        question='Which approach should we use?',
        header='Approach',
        options=[
            {'label': 'Option A', 'description': 'Safer path'},
            {'label': 'Option B', 'description': 'Faster path'},
        ],
    )

    assert result.success is True
    assert result.metadata['interaction_required'] is True
    assert result.metadata['interaction_type'] == 'ask_user_question'
    assert result.metadata['prompt_payload']['question'] == 'Which approach should we use?'
    assert result.metadata['prompt_payload']['options'][0]['index'] == 1
    assert result.metadata['questions'][0]['multiSelect'] is False


def test_react_loop_marks_unresolved_interaction_without_callback():
    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=ToolRegistry(),
        state=AgentState(),
        stream=False,
    )

    result = asyncio.run(loop._resolve_interaction(ToolResult(
        success=True,
        output='Question: choose',
        metadata={'interaction_required': True, 'prompt_payload': {'question': 'choose'}},
    )))

    assert result.success is False
    assert result.metadata['interaction_unresolved'] is True
    assert 'Interactive response required' in result.error


@pytest.mark.asyncio
async def test_web_fetch_async_execute_returns_real_metadata_and_extracted_text():
    tool = WebFetchTool()

    response = type('Response', (), {})()
    response.text = '<html><body><h1>Title</h1><p>Hello <b>world</b></p></body></html>'
    response.status_code = 200
    response.headers = {'content-type': 'text/html; charset=utf-8'}
    response.url = 'https://example.com/final'
    response.raise_for_status = lambda: None

    client = AsyncMock()
    client.get.return_value = response
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None

    with patch('opennova.tools.web_tools.httpx.AsyncClient', return_value=client):
        result = await tool.async_execute(url='https://example.com/start')

    assert result.success is True
    assert 'Title' in result.output
    assert 'Hello world' in result.output
    assert result.metadata['url'] == 'https://example.com/start'
    assert result.metadata['final_url'] == 'https://example.com/final'
    assert result.metadata['status_code'] == 200


@pytest.mark.asyncio
async def test_web_fetch_async_execute_truncates_and_handles_failures():
    tool = WebFetchTool()
    tool._max_output_chars = 10

    response = type('Response', (), {})()
    response.text = 'x' * 20
    response.status_code = 200
    response.headers = {'content-type': 'text/plain'}
    response.url = 'https://example.com/final'
    response.raise_for_status = lambda: None

    client = AsyncMock()
    client.get.return_value = response
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None

    with patch('opennova.tools.web_tools.httpx.AsyncClient', return_value=client):
        truncated = await tool.async_execute(url='https://example.com/start')
    assert truncated.success is True
    assert truncated.output.endswith('... [truncated]')

    failing_client = AsyncMock()
    failing_client.get.side_effect = RuntimeError('network failed')
    failing_client.__aenter__.return_value = failing_client
    failing_client.__aexit__.return_value = None

    with patch('opennova.tools.web_tools.httpx.AsyncClient', return_value=failing_client):
        failed = await tool.async_execute(url='https://example.com/start')
    assert failed.success is False
    assert failed.error == 'network failed'


def test_web_search_returns_explicit_unconfigured_error():
    result = WebSearchTool().execute(
        query='latest docs',
        allowed_domains=['docs.python.org'],
        blocked_domains=['example.com'],
        num_results=5,
    )

    assert result.success is False
    assert result.error == 'Web search is not configured in this runtime.'
    assert result.metadata['query'] == 'latest docs'
    assert result.metadata['allowed_domains'] == ['docs.python.org']
    assert result.metadata['blocked_domains'] == ['example.com']
    assert result.metadata['count'] == 0
