"""Tests for 04 plan execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from opennova.providers.base import BaseLLMProvider, FinishReason, LLMResponse, Message, ToolCall
from opennova.runtime.state import AgentState
from opennova.tools.base import ToolRegistry
from opennova.tools.file_tools import WriteFileTool


class ToolCallingLLM(BaseLLMProvider):
    """LLM double that emits one configured tool call and then stops."""

    def __init__(self, tool_name: str, arguments: dict[str, Any]):
        super().__init__(api_key="test", model="fake")
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls = 0

    async def chat(self, messages: list[Message], tools=None, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="I will use a tool.",
                tool_calls=[ToolCall(id="call-1", name=self.tool_name, arguments=self.arguments)],
                finish_reason=FinishReason.TOOL_CALL,
            )
        return LLMResponse(content="done", finish_reason=FinishReason.STOP)

    async def stream_chat(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    def get_model_info(self) -> dict[str, Any]:
        return {"model": self.model}


@pytest.mark.asyncio
async def test_react_loop_auto_checkpoints_file_write_and_can_restore(tmp_path: Path):
    from opennova.checkpoints import CheckpointManager
    from opennova.runtime.loop import ReActLoop

    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(WriteFileTool(config={"working_dir": str(tmp_path)}))
    events: list[Any] = []

    loop = ReActLoop(
        llm=ToolCallingLLM("write_file", {"file_path": str(target), "content": "after"}),
        tool_registry=registry,
        state=AgentState(),
        max_iterations=3,
        stream=False,
        working_dir=str(tmp_path),
    )

    await loop.run("overwrite file", on_tool_event=events.append)

    assert target.read_text(encoding="utf-8") == "after"
    result_event = [event for event in events if event.type == "tool_result"][0]
    checkpoint_id = result_event.metadata["checkpoint_id"]
    CheckpointManager(tmp_path).restore(checkpoint_id)
    assert target.read_text(encoding="utf-8") == "before"


def test_transcript_exporter_can_export_runtime_state(tmp_path: Path):
    from opennova.runtime.events import ToolEvent
    from opennova.transcript import TranscriptExporter

    runtime = type("Runtime", (), {})()
    runtime.session_manager = type("Session", (), {"session_id": "session-42"})()
    runtime.context_manager = type(
        "Context",
        (),
        {"messages": [Message(role="user", content="hello")]},
    )()
    runtime.tool_events = [
        ToolEvent(type="tool_start", tool_id="tool_1", tool_name="read_file").to_dict()
    ]

    path = TranscriptExporter(tmp_path).export_runtime(runtime)

    text = path.read_text(encoding="utf-8")
    assert "session-42" in text
    assert "hello" in text
    assert "tool_start" in text


def test_automation_command_handler_supports_create_pause_resume_delete_run_now(tmp_path: Path):
    from opennova.automation import LocalAutomationScheduler
    from opennova.cli.automation_commands import handle_automation_command

    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: 100.0)
    created = handle_automation_command(
        scheduler,
        'once docs 120 "Review docs"',
        runner=lambda task: f"ran {task.name}",
    )
    task_id = created.metadata["task_id"]

    assert "Scheduled once" in created.output
    assert handle_automation_command(scheduler, f"pause {task_id}").success is True
    assert scheduler.get(task_id).enabled is False
    assert handle_automation_command(scheduler, f"resume {task_id}").success is True
    run = handle_automation_command(scheduler, f"run-now {task_id}", runner=lambda task: "ok")
    assert run.metadata["run"].output == "ok"
    assert handle_automation_command(scheduler, f"delete {task_id}").success is True
    assert task_id not in scheduler.tasks


def test_trusted_plugin_manifest_registers_command_backed_tool(tmp_path: Path):
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
name: demo
enabled: true
tools:
  - name: demo_echo
    description: Echo from plugin
    command: python
    args: ["-c", "print('plugin ok')"]
    read_only: true
""".strip(),
        encoding="utf-8",
    )

    manager = PluginManager(project_path=tmp_path)
    manager.load_enabled_plugins(config={})
    assert manager.build_tools(config={"working_dir": str(tmp_path)}) == []

    manager.trust_plugin("demo")
    manager.load_enabled_plugins(config={})
    tools = manager.build_tools(config={"working_dir": str(tmp_path)})

    assert [tool.name for tool in tools] == ["demo_echo"]
    result = tools[0].execute()
    assert result.success is True
    assert "plugin ok" in result.output


def test_python_definition_resolves_import_alias_across_files(tmp_path: Path):
    from opennova.tools.diagnostics_tools import PythonDefinitionTool, PythonSymbolsTool

    (tmp_path / "lib.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from lib import target as alias\nvalue = alias()\n", encoding="utf-8")
    config = {"working_dir": str(tmp_path)}

    symbols = PythonSymbolsTool(config=config).execute(path=str(tmp_path))
    imports = symbols.metadata["imports"]
    assert imports[0]["alias"] == "alias"

    definition = PythonDefinitionTool(config=config).execute(symbol="alias", path=str(tmp_path))
    assert definition.success is True
    assert definition.metadata["definition"]["file"].endswith("lib.py")
    assert definition.metadata["definition"]["qualified_name"] == "target"


def test_slash_registry_includes_export_command():
    from opennova.cli.commands import SlashCommandRegistry

    registry = SlashCommandRegistry.default()

    assert "/export" in registry.names()
    assert registry.get("/export").handler == "_cmd_export"
