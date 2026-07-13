from types import SimpleNamespace

import pytest

from opennova.cli.tui import OpenNovaTUI
from opennova.memory.context import ContextManager
from opennova.providers.anthropic import AnthropicProvider
from opennova.providers.base import FinishReason, LLMResponse, Message, ToolCall, ToolSchema
from opennova.runtime.loop import ParsedAction, ReActLoop
from opennova.runtime.state import AgentState, Plan, PlanStep
from opennova.runtime.workflow import WorkflowDecision, WorkflowRouter
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.plan_mode_tools import EnterPlanModeTool, ExitPlanModeTool


class FinalResponseLLM:
    model = "test-model"

    async def chat(self, messages, tools=None, **kwargs):
        return LLMResponse(content="done", finish_reason=FinishReason.STOP)


@pytest.mark.asyncio
async def test_runtime_prompt_is_upserted_ahead_of_project_memory():
    context = ContextManager()
    context.add_message(
        Message(
            role="system",
            content="Project memory",
            name="opennova_project_memory",
        )
    )
    context.add_message(
        Message(
            role="system",
            content=(
                "You are an AI coding assistant that helps users with software engineering tasks.\n"
                "Legacy rules"
            ),
        )
    )
    loop = ReActLoop(
        llm=FinalResponseLLM(),
        tool_registry=ToolRegistry(),
        state=AgentState(),
        stream=False,
        context_manager=context,
    )

    await loop.run("Answer a question", preserve_context=True, route_workflow=False)
    loop._upsert_runtime_system_prompt()

    assert context.messages[0].name == "opennova_runtime"
    assert "You are an AI coding assistant" in context.messages[0].content
    assert [message.name for message in context.messages].count("opennova_runtime") == 1
    assert any(message.name == "opennova_project_memory" for message in context.messages)
    assert not any("Legacy rules" in message.content for message in context.messages)


def test_system_prompt_combines_all_system_messages_for_anthropic():
    messages = [
        Message(role="system", content="Runtime rules", name="opennova_runtime"),
        Message(role="system", content="Project memory", name="opennova_project_memory"),
        Message(role="user", content="Task"),
    ]

    combined = AnthropicProvider._build_system_prompt(None, messages)

    assert combined == "Runtime rules\n\nProject memory"


@pytest.mark.asyncio
async def test_anthropic_required_tool_choice_uses_any_and_combined_system_prompt():
    captured = {}

    class MessagesClient:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                stop_reason="end_turn",
                model="claude-test",
                id="response-1",
            )

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model = "claude-test"
    provider.client = SimpleNamespace(messages=MessagesClient())
    tool = ToolSchema(name="route", description="Route", parameters={"type": "object"})

    await provider.chat(
        [
            Message(role="system", content="Runtime rules"),
            Message(role="system", content="Project memory"),
            Message(role="user", content="Task"),
        ],
        tools=[tool],
        tool_choice="required",
    )

    assert captured["system"] == "Runtime rules\n\nProject memory"
    assert captured["tool_choice"] == {"type": "any"}


@pytest.mark.asyncio
async def test_workflow_router_forces_a_single_structured_control_tool():
    captured = {}

    class RoutingLLM:
        model = "test-model"

        async def chat(self, messages, tools=None, **kwargs):
            captured["messages"] = messages
            captured["tools"] = tools
            captured["kwargs"] = kwargs
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="route-1",
                        name="select_execution_mode",
                        arguments={
                            "mode": "plan",
                            "reason": "The user wants approval before implementation.",
                            "confidence": 0.98,
                        },
                    )
                ],
                finish_reason=FinishReason.TOOL_CALL,
            )

    result = await WorkflowRouter(RoutingLLM()).route(
        [Message(role="user", content="先列一个方案，确认后再开始")],
        "Improve the renderer",
    )

    assert result.decision == WorkflowDecision.PLAN
    assert result.confidence == 0.98
    assert captured["kwargs"]["temperature"] == 0
    assert captured["kwargs"]["tool_choice"] == "required"
    assert [tool.name for tool in captured["tools"]] == ["select_execution_mode"]


@pytest.mark.asyncio
async def test_plan_workflow_emits_enter_plan_mode_before_exit():
    state = AgentState()
    registry = ToolRegistry(
        [
            EnterPlanModeTool(config={"state": state}),
            ExitPlanModeTool(config={"state": state}),
        ]
    )
    public_tools = []

    class PlanningLLM:
        model = "test-model"

        async def chat(self, messages, tools=None, **kwargs):
            if kwargs.get("tool_choice") == "required":
                return LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="route-1",
                            name="select_execution_mode",
                            arguments={
                                "mode": "plan",
                                "reason": "Prepare a reviewable plan first.",
                                "confidence": 1,
                            },
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALL,
                )
            return LLMResponse(
                content="Plan is ready.",
                tool_calls=[
                    ToolCall(
                        id="exit-1",
                        name="exit_plan_mode",
                        arguments={
                            "task": "Optimize rendering",
                            "steps": [{"description": "Inspect and cache rendering surfaces"}],
                        },
                    )
                ],
                finish_reason=FinishReason.TOOL_CALL,
            )

    loop = ReActLoop(
        llm=PlanningLLM(),
        tool_registry=registry,
        state=state,
        stream=False,
        context_manager=ContextManager(),
    )

    result = await loop.run(
        "先做第一个吧，列个计划开始执行",
        on_tool_event=lambda event: public_tools.append((event.type, event.tool_name)),
        route_workflow=True,
    )

    starts = [tool_name for event_type, tool_name in public_tools if event_type == "tool_start"]
    assert starts == ["enter_plan_mode", "exit_plan_mode"]
    assert result == "Plan mode exited. Awaiting user approval of the plan."
    assert state.plan_approval_status.value == "awaiting_approval"


def test_unresolved_workflow_blocks_project_modifications():
    loop = ReActLoop(
        llm=FinalResponseLLM(),
        tool_registry=ToolRegistry(),
        state=AgentState(),
        context_manager=ContextManager(),
    )
    loop._workflow_resolved = False
    loop._workflow_decision = None

    result = loop._check_tool_guard(
        ParsedAction(
            tool_name="edit_file",
            arguments={"file_path": "app.py", "old_text": "a", "new_text": "b"},
        )
    )

    assert result.allowed is False
    assert result.metadata["workflow_unresolved"] is True


@pytest.mark.asyncio
async def test_plan_mode_text_cannot_finish_without_exit_plan_mode():
    state = AgentState()
    state.set_plan(Plan(task="Refactor", steps=[PlanStep(id="step_1", description="Inspect")]))
    registry = ToolRegistry([ExitPlanModeTool(config={"state": state})])

    class TextThenExitLLM:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    content="Here is the plan in prose.",
                    finish_reason=FinishReason.STOP,
                )
            return LLMResponse(
                content="Submitting the plan.",
                tool_calls=[ToolCall(id="exit-1", name="exit_plan_mode", arguments={})],
                finish_reason=FinishReason.TOOL_CALL,
            )

    llm = TextThenExitLLM()
    loop = ReActLoop(
        llm=llm,
        tool_registry=registry,
        state=state,
        stream=False,
        context_manager=ContextManager(),
    )

    await loop.run(
        "Continue planning",
        preserve_plan_state=True,
        route_workflow=False,
    )

    assert llm.calls == 2
    assert state.plan_approval_status.value == "awaiting_approval"
    assert any(
        "Do not finish with plan text alone" in message.content
        for message in loop.messages
        if message.role == "user"
    )


@pytest.mark.asyncio
async def test_failed_workflow_routing_does_not_silently_allow_mutation():
    executions = []

    class EditTool(BaseTool):
        name = "edit_file"
        description = "Edit a file"

        def execute(self, **kwargs):
            executions.append(kwargs)
            return ToolResult(success=True, output="edited")

    class RoutingFailureLLM:
        model = "test-model"

        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content="No routing tool", finish_reason=FinishReason.STOP)
            if self.calls == 2:
                return LLMResponse(
                    content="Trying to edit",
                    tool_calls=[
                        ToolCall(
                            id="edit-1",
                            name="edit_file",
                            arguments={"file_path": "app.py"},
                        )
                    ],
                    finish_reason=FinishReason.TOOL_CALL,
                )
            return LLMResponse(content="Unable to modify safely", finish_reason=FinishReason.STOP)

    loop = ReActLoop(
        llm=RoutingFailureLLM(),
        tool_registry=ToolRegistry([EditTool()]),
        state=AgentState(),
        stream=False,
        context_manager=ContextManager(),
    )
    results = []

    await loop.run(
        "Change app.py",
        on_result=results.append,
        route_workflow=True,
    )

    assert executions == []
    assert results[0].metadata["workflow_unresolved"] is True


@pytest.mark.asyncio
async def test_tui_prompts_for_decision_after_natural_language_plan_turn():
    captured = {}
    decisions = []

    class Agent:
        def __init__(self):
            self.state = AgentState()

        async def _run_act_mode(self, **kwargs):
            captured.update(kwargs)
            self.state.set_plan(
                Plan(task="Reviewable plan", steps=[PlanStep(id="step_1", description="Inspect")])
            )
            self.state.mark_plan_awaiting_approval()
            return "Plan ready"

    class Log:
        def write(self, value):
            captured.setdefault("log", []).append(value)

    async def run_agent_task(coro):
        return await coro

    async def ask_plan_decision(user_message):
        decisions.append(user_message)
        return "revise"

    app = SimpleNamespace(
        agent=Agent(),
        _run_agent_task=run_agent_task,
        _ask_plan_decision_dialog=ask_plan_decision,
        query_one=lambda selector: Log(),
    )

    await OpenNovaTUI._execute_task(app, "先给出方案，确认后再实现")

    assert captured["route_workflow"] is True
    assert decisions == ["先给出方案，确认后再实现"]
    assert app.agent.state.plan_approval_status.value == "draft"


@pytest.mark.asyncio
async def test_tui_act_command_explicitly_disables_workflow_routing():
    captured = {}

    async def execute_task(task, route_workflow=True):
        captured["task"] = task
        captured["route_workflow"] = route_workflow

    app = SimpleNamespace(_execute_task=execute_task)

    await OpenNovaTUI._cmd_act(app, "Implement directly")

    assert captured == {"task": "Implement directly", "route_workflow": False}
