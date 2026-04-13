"""Basic tests for OpenNova."""

import asyncio
import threading
from pathlib import Path
import pytest

from opennova.tools.base import ToolRegistry, ToolResult, BaseTool
from opennova.tools.agent_tools import AgentTool, SendMessageTool
from opennova.tools.task_tools import set_global_task_manager
from opennova.utils.task_output import read_task_output
from opennova.providers.base import Message, ToolSchema
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.state import AgentState
from opennova.tools.plan_mode_tools import EnterPlanModeTool, ExitPlanModeTool
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

        async def _confirm_plan(self, plan):
            return False

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

        assert result == "Plan cancelled by user"
        assert captured["plan_file_path"] is not None
        assert runtime.state.plan_file_path == captured["plan_file_path"]
        assert captured["plan_file_path"].parent == Path(".opennova") / "plan"
        assert captured["plan_file_path"].name.startswith("plan_")
        assert captured["plan_file_path"].suffix == ".md"
        assert len(captured["plan_file_path"].stem.replace("plan_", "")) == 15
        assert captured["plan_file_path"].exists()

        saved_content = captured["plan_file_path"].read_text(encoding="utf-8")
        assert "# Saved Plan: Persist plan" in saved_content
        assert "- Task: Persist this plan" in saved_content
        assert "- Saved path: .opennova/plan/" in saved_content
        assert "1. **step_1** — Write plan to disk" in saved_content
    finally:
        os.chdir(previous_cwd)


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
    assert result.metadata["status"] == "awaiting_approval"
    assert result.metadata["mode"] == "plan"
    assert result.metadata["has_plan"] is True
    assert result.metadata["plan_file_path"].endswith("plan.md")
    assert result.metadata["requires_confirmation"] is True

