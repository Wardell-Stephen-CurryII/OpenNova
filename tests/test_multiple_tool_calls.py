"""Regression tests for multiple tool calls in one model response."""

from __future__ import annotations

import pytest

from opennova.hooks import HookManager
from opennova.providers.base import FinishReason, LLMResponse, ToolCall
from opennova.runtime.loop import ReActLoop
from opennova.runtime.state import AgentState
from opennova.security.guardrails import GuardResult, RiskLevel
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.plan_mode_tools import EnterPlanModeTool


class RecordingTool(BaseTool):
    description = "Record calls"

    def __init__(
        self,
        name: str,
        *,
        fail: bool = False,
        execution_order=None,
        result_metadata=None,
    ):
        super().__init__()
        self.name = name
        self.fail = fail
        self.call_count = 0
        self.execution_order = execution_order
        self.result_metadata = result_metadata or {}
        self.arguments_seen = []

    def execute(self, **kwargs):
        self.call_count += 1
        self.arguments_seen.append(dict(kwargs))
        if self.execution_order is not None:
            self.execution_order.append(self.name)
        if self.fail:
            return ToolResult(success=False, output="", error=f"{self.name} failed")
        return ToolResult(
            success=True,
            output=f"{self.name} executed",
            metadata=dict(self.result_metadata),
        )


class BatchedProvider:
    model = "mock-model"

    def __init__(self, tool_count: int):
        self.request_count = 0
        self.tool_count = tool_count

    async def chat(self, messages, tools=None, **kwargs):
        self.request_count += 1
        if self.request_count == 1:
            return LLMResponse(
                content="Calling tools",
                tool_calls=[
                    ToolCall(id=f"call-{index}", name=f"tool_{index}", arguments={})
                    for index in range(1, self.tool_count + 1)
                ],
                finish_reason=FinishReason.TOOL_CALL,
            )
        return LLMResponse(content="Finished", finish_reason=FinishReason.STOP)


class ScriptedProvider:
    model = "mock-model"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.request_count = 0
        self.messages_seen = []
        self.tools_seen = []

    async def chat(self, messages, tools=None, **kwargs):
        self.request_count += 1
        self.messages_seen.append(list(messages))
        self.tools_seen.append(list(tools or []))
        return self.responses.pop(0)


def tool_response(*names):
    return LLMResponse(
        content="Calling tools",
        tool_calls=[
            ToolCall(id=f"call-{index}", name=name, arguments={})
            for index, name in enumerate(names, start=1)
        ],
        finish_reason=FinishReason.TOOL_CALL,
    )


def final_response(content="Finished"):
    return LLMResponse(content=content, finish_reason=FinishReason.STOP)


def make_loop(provider, *tools):
    registry = ToolRegistry(list(tools))
    return ReActLoop(provider, registry, AgentState(), stream=False)


@pytest.mark.parametrize("tool_count", [1, 2, 3, 4])
@pytest.mark.asyncio
async def test_executes_and_observes_every_tool_call_from_one_response(tool_count):
    provider = BatchedProvider(tool_count)
    execution_order = []
    tools = [
        RecordingTool(f"tool_{index}", execution_order=execution_order)
        for index in range(1, tool_count + 1)
    ]
    loop = make_loop(provider, *tools)

    result = await loop.run("Call both tools")

    assert result == "Finished"
    assert provider.request_count == 2
    assert [tool.call_count for tool in tools] == [1] * tool_count
    assert execution_order == [f"tool_{index}" for index in range(1, tool_count + 1)]

    assistant_messages = [message for message in loop.messages if message.role == "assistant"]
    tool_messages = [message for message in loop.messages if message.role == "tool"]
    expected_ids = [f"call-{index}" for index in range(1, tool_count + 1)]
    expected_names = [f"tool_{index}" for index in range(1, tool_count + 1)]
    assert [call.id for call in assistant_messages[0].tool_calls or []] == expected_ids
    assert [message.tool_call_id for message in tool_messages] == expected_ids
    assert [message.name for message in tool_messages] == expected_names


@pytest.mark.asyncio
async def test_one_failed_call_does_not_discard_later_calls():
    provider = BatchedProvider(3)
    tool_1 = RecordingTool("tool_1")
    tool_2 = RecordingTool("tool_2", fail=True)
    tool_3 = RecordingTool("tool_3")
    loop = make_loop(provider, tool_1, tool_2, tool_3)

    assert await loop.run("Call both tools") == "Finished"
    assert [tool_1.call_count, tool_2.call_count, tool_3.call_count] == [1, 1, 1]
    tool_messages = [message for message in loop.messages if message.role == "tool"]
    assert tool_messages[0].content == "tool_1 executed"
    assert "tool_2 failed" in tool_messages[1].content
    assert tool_messages[2].content == "tool_3 executed"


@pytest.mark.asyncio
async def test_multi_call_tool_events_have_unique_sequential_ids():
    provider = BatchedProvider(4)
    loop = make_loop(
        provider,
        *[RecordingTool(f"tool_{index}") for index in range(1, 5)],
    )
    events = []

    await loop.run("Call both tools", on_tool_event=events.append)

    start_events = [event for event in events if event.type == "tool_start"]
    tool_ids = [event.tool_id for event in start_events]
    assert len(set(tool_ids)) == 4
    assert [tool_id.rsplit("_", 1)[-1] for tool_id in tool_ids] == [
        "0001",
        "0002",
        "0003",
        "0004",
    ]


@pytest.mark.asyncio
async def test_tool_event_ids_do_not_repeat_across_runs():
    first_loop = make_loop(BatchedProvider(1), RecordingTool("tool_1"))
    second_loop = make_loop(BatchedProvider(1), RecordingTool("tool_1"))
    first_events = []
    second_events = []

    await first_loop.run("First turn", on_tool_event=first_events.append)
    await second_loop.run("Second turn", on_tool_event=second_events.append)

    first_id = next(event.tool_id for event in first_events if event.type == "tool_start")
    second_id = next(event.tool_id for event in second_events if event.type == "tool_start")
    assert first_id != second_id


@pytest.mark.asyncio
async def test_empty_tool_call_response_remains_a_final_answer():
    class FinalProvider:
        model = "mock-model"

        async def chat(self, messages, tools=None, **kwargs):
            return LLMResponse(
                content="No tools needed",
                tool_calls=[],
                finish_reason=FinishReason.STOP,
            )

    loop = make_loop(FinalProvider())

    assert await loop.run("Answer directly") == "No tools needed"


@pytest.mark.parametrize(
    ("tool_names", "expected_barrier"),
    [
        (("skill", "tool_1"), "skill"),
        (("tool_1", "skill"), "skill"),
        (("tool_1", "skill", "tool_2"), "skill"),
        (("ask_user_question", "skill", "tool_1"), "ask_user_question"),
    ],
)
@pytest.mark.asyncio
async def test_first_batch_barrier_executes_alone_in_any_position(
    tool_names,
    expected_barrier,
):
    provider = ScriptedProvider(tool_response(*tool_names), final_response())
    tools = {name: RecordingTool(name) for name in tool_names}
    loop = make_loop(provider, *tools.values())

    assert await loop.run("Run a mixed batch") == "Finished"
    assert tools[expected_barrier].call_count == 1
    assert all(
        tool.call_count == (1 if name == expected_barrier else 0) for name, tool in tools.items()
    )

    assistant = next(
        message
        for message in loop.messages
        if message.role == "assistant" and len(message.tool_calls or []) == len(tool_names)
    )
    expected_ids = {call.id for call in assistant.tool_calls or []}
    tool_messages = [
        message
        for message in loop.messages
        if message.role == "tool" and message.tool_call_id in expected_ids
    ]
    assert [message.tool_call_id for message in tool_messages] == [
        f"call-{index}" for index in range(1, len(tool_names) + 1)
    ]
    assert (
        sum("was not executed" in message.content for message in tool_messages)
        == len(tool_names) - 1
    )


@pytest.mark.asyncio
async def test_skill_barrier_applies_allowed_tools_and_hooks_before_next_model_turn():
    skill = RecordingTool(
        "skill",
        result_metadata={
            "skill_prompt": "Use only the allowed tool.",
            "resolved_skill": "read-only-skill",
            "allowed_tools": ["allowed_tool"],
            "hooks": {
                "pre_tool_use": [
                    {
                        "matcher": "allowed_tool",
                        "hooks": [{"set_arguments": {"hooked": True}}],
                    }
                ]
            },
        },
    )
    forbidden = RecordingTool("forbidden_tool")
    allowed = RecordingTool("allowed_tool")
    provider = ScriptedProvider(
        tool_response("skill", "forbidden_tool"),
        tool_response("allowed_tool"),
        final_response(),
    )
    loop = ReActLoop(
        provider,
        ToolRegistry([skill, forbidden, allowed]),
        AgentState(),
        stream=False,
        hook_manager=HookManager(),
    )

    assert await loop.run("Use the read-only skill") == "Finished"
    assert skill.call_count == 1
    assert forbidden.call_count == 0
    assert allowed.call_count == 1
    assert allowed.arguments_seen == [{"hooked": True}]
    assert [schema.name for schema in provider.tools_seen[1]] == ["allowed_tool"]

    first_assistant_index = next(
        index
        for index, message in enumerate(loop.messages)
        if message.role == "assistant" and len(message.tool_calls or []) == 2
    )
    observed_group = loop.messages[first_assistant_index : first_assistant_index + 4]
    assert [message.role for message in observed_group] == ["assistant", "tool", "tool", "user"]
    assert "Invoked skill 'read-only-skill'" in observed_group[-1].content


@pytest.mark.asyncio
async def test_question_barrier_reconsiders_after_user_answer():
    question = RecordingTool(
        "ask_user_question",
        result_metadata={
            "interaction_required": True,
            "interaction_type": "ask_user_question",
            "prompt_payload": {"question": "Choose A or B"},
        },
    )
    write = RecordingTool("write_file")
    provider = ScriptedProvider(
        tool_response("ask_user_question", "write_file"),
        final_response(),
    )

    async def answer_question(metadata):
        return {"answer": "B", "display": "Choice B", "answers": {"choice": "B"}}

    loop = ReActLoop(
        provider,
        ToolRegistry([question, write]),
        AgentState(),
        stream=False,
        interaction_callback=answer_question,
    )

    assert await loop.run("Ask before writing") == "Finished"
    assert question.call_count == 1
    assert write.call_count == 0
    second_request_tools = [
        message for message in provider.messages_seen[1] if message.role == "tool"
    ]
    assert any("Choice B" in message.content for message in second_request_tools)
    assert any("was not executed" in message.content for message in second_request_tools)


@pytest.mark.asyncio
async def test_enter_plan_mode_barrier_blocks_same_batch_write():
    state = AgentState()
    enter_plan = EnterPlanModeTool({"state": state})
    write = RecordingTool("write_file")
    provider = ScriptedProvider(tool_response("enter_plan_mode", "write_file"))
    loop = ReActLoop(
        provider,
        ToolRegistry([enter_plan, write]),
        state,
        max_iterations=1,
        stream=False,
    )
    original_guard = loop._check_tool_guard

    def allow_enter_plan_mode(action):
        if action.tool_name == "enter_plan_mode":
            return GuardResult(
                allowed=True,
                risk_level=RiskLevel.SAFE,
                reason="Allowed for isolated batch-barrier testing",
                requires_confirmation=False,
            )
        return original_guard(action)

    loop._check_tool_guard = allow_enter_plan_mode

    result = await loop.run("Plan before writing")

    assert result == "Task incomplete: reached maximum iterations (1)"
    assert state.mode == "plan"
    assert write.call_count == 0


@pytest.mark.asyncio
async def test_exit_plan_mode_barrier_stops_later_calls_for_approval():
    exit_plan = RecordingTool(
        "exit_plan_mode",
        result_metadata={"status": "awaiting_approval"},
    )
    later_tool = RecordingTool("tool_1")
    state = AgentState()
    provider = ScriptedProvider(tool_response("exit_plan_mode", "tool_1"))
    loop = ReActLoop(
        provider,
        ToolRegistry([exit_plan, later_tool]),
        state,
        stream=False,
    )

    assert await loop.run("Submit the plan") == "exit_plan_mode executed"
    assert state.is_complete
    assert exit_plan.call_count == 1
    assert later_tool.call_count == 0
    assert provider.request_count == 1
