"""Basic tests for OpenNova."""

import asyncio
import subprocess
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from opennova.tools.base import ToolRegistry, ToolResult, BaseTool
from opennova.tools.agent_tools import AgentTool, SendMessageTool
from opennova.tools.ask_question_tool import AskUserQuestionTool
from opennova.tools.git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)
from opennova.tools.task_tools import (
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    set_global_task_manager,
)
from opennova.tools.web_tools import WebFetchTool, WebSearchTool
from opennova.utils.task_output import read_task_output
from opennova.providers.base import Message, ToolSchema
from opennova.memory.context import ContextManager
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.state import AgentState, PlanApprovalStatus
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
    from opennova.runtime.state import Plan, PlanStep

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
        assert "1. **step_1** — Write plan to disk" in saved_content
    finally:
        os.chdir(previous_cwd)


def test_agent_runtime_execute_approved_plan_runs_steps():
    """Approved plans should execute only after explicit approval."""
    from opennova.runtime.state import Plan, PlanStep, StepStatus

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
    runtime._emit = lambda *args, **kwargs: None

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




def test_repl_skills_command_lists_runtime_skills():
    """REPL /skills should render loaded skills from the runtime."""
    from opennova.cli.repl import REPL

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.skill_registry = type(
        "SkillRegistryStub",
        (),
        {
            "list_skills": lambda self: ["code_review", "git_helper"],
            "get_skill_info": lambda self, name: {
                "name": name,
                "enabled": name != "git_helper",
                "source_type": "builtin",
                "source": f"/tmp/{name}/SKILL.md",
                "model_invocable": name == "code_review",
                "user_invocable": True,
                "description": f"desc:{name}",
            }
        },
    )()

    repl = REPL(runtime, config=None)
    captured = {}
    repl.renderer.print_skills = lambda skills: captured.setdefault("skills", skills)

    asyncio.run(repl._cmd_skills(""))

    assert [skill["name"] for skill in captured["skills"]] == ["code_review", "git_helper"]
    assert captured["skills"][1]["enabled"] is False
    assert captured["skills"][0]["model_invocable"] is True
    assert captured["skills"][0]["user_invocable"] is True
    assert captured["skills"][0]["source_type"] == "builtin: /tmp/code_review/SKILL.md"


def test_repl_reload_skills_command_reports_count():
    """REPL /reload-skills should report the runtime reload count."""
    from opennova.cli.repl import REPL

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.reload_skills = lambda: 4

    repl = REPL(runtime, config=None)
    messages = []
    repl.renderer.print_success = lambda message: messages.append(message)

    asyncio.run(repl._cmd_reload_skills(""))

    assert messages == ["Reloaded 4 skills."]


def test_repl_config_command_handles_missing_config():
    """REPL /config should show a helpful error when config is unavailable."""
    from opennova.cli.repl import REPL

    runtime = AgentRuntime.__new__(AgentRuntime)
    repl = REPL(runtime, config=None)
    errors = []
    repl.renderer.print_error = lambda message: errors.append(message)

    asyncio.run(repl._cmd_config(""))

    assert errors == ["No configuration object available."]


def test_repl_history_command_shows_empty_state():
    """REPL /history should show an empty-state message when there is no history."""
    from opennova.cli.repl import REPL

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.context_manager = ContextManager(model="gpt-4o")

    repl = REPL(runtime, config=None)
    printed = []
    repl.renderer.print = lambda message, **kwargs: printed.append(message)

    asyncio.run(repl._cmd_history(""))

    assert printed == ["No conversation history."]


def test_repl_clear_command_calls_runtime_clear():
    """REPL /clear should delegate to the runtime clear helper."""
    from opennova.cli.repl import REPL

    runtime = AgentRuntime.__new__(AgentRuntime)
    cleared = []
    runtime.clear_conversation = lambda: cleared.append(True)

    repl = REPL(runtime, config=None)
    messages = []
    repl.renderer.print_success = lambda message: messages.append(message)

    asyncio.run(repl._cmd_clear(""))

    assert cleared == [True]
    assert messages == ["Conversation cleared."]


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


@pytest.mark.asyncio
async def test_repl_plan_command_executes_after_approval():
    """REPL /plan should approve and execute without resetting away the saved plan."""
    from opennova.cli.repl import REPL

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.state = AgentState()
    registered_events = []
    runtime.register_callback = lambda event, callback: registered_events.append(event)

    async def fake_run(task: str, mode: str = "act", stream: bool = True, progress_callback=None):
        from opennova.runtime.state import Plan, PlanStep

        runtime.state.reset(task)
        runtime.state.set_mode(mode)
        runtime.state.set_plan(Plan(task="Approved plan", steps=[PlanStep(id="step_1", description="Do thing")]))
        runtime.state.mark_plan_awaiting_approval()
        return "Plan ready for approval"

    executed = []

    async def fake_execute(stream: bool = True):
        executed.append(True)
        return "executed"

    runtime.run = fake_run
    runtime.execute_approved_plan = fake_execute

    repl = REPL(runtime, config=None)
    repl.renderer.print = lambda *args, **kwargs: None
    repl.renderer.print_plan = lambda *args, **kwargs: None
    repl.renderer.print_markdown = lambda *args, **kwargs: None
    repl._prompt_plan_execution = lambda: asyncio.sleep(0, result=True)

    await repl._cmd_plan("Do thing")

    assert executed == [True]
    assert runtime.state.plan_approval_status == PlanApprovalStatus.APPROVED
    assert {"plan", "thought", "action", "result", "stream"}.issubset(set(registered_events))


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




class Completed:
    def __init__(self, stdout='', stderr='', returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_ask_user_question_rejects_invalid_option_counts():
    tool = AskUserQuestionTool()

    too_few = tool.execute(question='Pick one?', options=[{'label': 'Only', 'description': 'One'}])
    too_many = tool.execute(
        question='Pick one?',
        options=[
            {'label': '1', 'description': 'one'},
            {'label': '2', 'description': 'two'},
            {'label': '3', 'description': 'three'},
            {'label': '4', 'description': 'four'},
            {'label': '5', 'description': 'five'},
        ],
    )

    assert too_few.success is False
    assert too_few.error == 'Questions must have 2-4 options.'
    assert too_many.success is False
    assert too_many.error == 'Questions must have 2-4 options.'


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

    with patch.object(tool, '_generate_commit_message', return_value='Auto message') as mock_generate:
        with patch('opennova.tools.git_tools.subprocess.run') as mock_run:
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
