"""Tests for local hook loading and tool execution hooks."""

from __future__ import annotations

import pytest

from opennova.runtime.loop import ParsedAction, ReActLoop
from opennova.runtime.state import AgentState
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult


class DummyProvider:
    model = "dummy"


class TrackingTool(BaseTool):
    name = "tracking"
    description = "tracking"

    def __init__(self):
        super().__init__()
        self.calls = 0

    def execute(self, value: str = "ok") -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, output=value)


def test_hook_manager_loads_project_hook_file(tmp_path):
    from opennova.hooks import HookManager

    hook_dir = tmp_path / ".opennova" / "hooks"
    hook_dir.mkdir(parents=True)
    (hook_dir / "audit.py").write_text(
        "def pre_tool_use(event):\n"
        "    event['metadata']['loaded'] = True\n"
        "    return event\n",
        encoding="utf-8",
    )

    manager = HookManager(project_path=tmp_path)
    manager.load_project_hooks()
    event = manager.run_pre_tool_use({"tool_name": "tracking", "metadata": {}})

    assert event["metadata"]["loaded"] is True


@pytest.mark.asyncio
async def test_react_loop_runs_pre_and_post_tool_hooks():
    from opennova.hooks import HookManager

    registry = ToolRegistry()
    tool = TrackingTool()
    registry.register(tool)
    manager = HookManager()
    calls: list[str] = []

    def pre(event):
        calls.append(f"pre:{event['tool_name']}")
        event["arguments"]["value"] = "changed"
        return event

    def post(event):
        calls.append(f"post:{event['tool_name']}:{event['result'].output}")
        return event

    manager.register("pre_tool_use", pre)
    manager.register("post_tool_use", post)
    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        hook_manager=manager,
    )

    result = await loop._act(ParsedAction(tool_name="tracking", arguments={"value": "original"}))

    assert result.success is True
    assert result.output == "changed"
    assert calls == ["pre:tracking", "post:tracking:changed"]
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_pre_tool_hook_can_block_tool_execution():
    from opennova.hooks import HookManager

    registry = ToolRegistry()
    tool = TrackingTool()
    registry.register(tool)
    manager = HookManager()

    def pre(event):
        return ToolResult(success=False, output="", error="blocked by hook")

    manager.register("pre_tool_use", pre)
    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        hook_manager=manager,
    )

    result = await loop._act(ParsedAction(tool_name="tracking", arguments={}))

    assert result.success is False
    assert result.error == "blocked by hook"
    assert tool.calls == 0
