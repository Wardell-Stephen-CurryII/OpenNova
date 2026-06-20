"""Tests for 03 runtime productization work."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from opennova.providers.base import BaseLLMProvider, FinishReason, LLMResponse, Message, ToolCall
from opennova.runtime.state import AgentState
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult


class FakeLLM(BaseLLMProvider):
    """LLM double that emits one tool call and then stops."""

    def __init__(self):
        super().__init__(api_key="test", model="fake")
        self.calls = 0

    async def chat(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="I'll read the file.",
                tool_calls=[ToolCall(id="llm-call-1", name="read_file", arguments={"file_path": "README.md"})],
                finish_reason=FinishReason.TOOL_CALL,
            )
        return LLMResponse(content="done", finish_reason=FinishReason.STOP)

    async def stream_chat(self, *args: Any, **kwargs: Any):
        raise NotImplementedError

    def count_tokens(self, messages: list[Message]) -> int:
        return len(messages)

    def get_model_info(self) -> dict[str, Any]:
        return {"model": self.model}


class FakeReadTool(BaseTool):
    name = "read_file"
    description = "Read a fake file"

    def execute(self, file_path: str) -> ToolResult:
        return ToolResult(success=True, output=f"read {file_path}")

    def is_read_only(self, **kwargs: Any) -> bool:
        return True


@pytest.mark.asyncio
async def test_react_loop_emits_canonical_tool_events_with_shared_tool_id():
    from opennova.runtime.loop import ReActLoop

    registry = ToolRegistry()
    registry.register(FakeReadTool())
    events: list[Any] = []
    loop = ReActLoop(
        llm=FakeLLM(),
        tool_registry=registry,
        state=AgentState(),
        max_iterations=3,
        stream=False,
    )

    await loop.run(
        "read something",
        on_tool_event=events.append,
    )

    event_types = [event.type for event in events]
    assert event_types == ["tool_start", "tool_result"]
    assert events[0].tool_id == events[1].tool_id
    assert events[0].tool_name == "read_file"
    assert events[1].duration_ms >= 0
    assert events[1].success is True


def test_permission_store_persists_session_rules_and_never_allows_hard_blocks(tmp_path: Path):
    from opennova.security.guardrails import Guardrails
    from opennova.security.permissions import PermissionDecision, PermissionStore

    store = PermissionStore(tmp_path / "permissions.json")
    store.record("write_file", PermissionDecision.ALWAYS_ALLOW)
    store.record("delete_file", PermissionDecision.ALWAYS_DENY)

    reloaded = PermissionStore(tmp_path / "permissions.json")
    guardrails = Guardrails(permission_store=reloaded)

    assert guardrails.check_tool_call("write_file", {"file_path": "ok.txt"}).requires_confirmation is False
    assert guardrails.check_tool_call("delete_file", {"file_path": "ok.txt"}).allowed is False
    blocked = guardrails.check_tool_call("execute_command", {"command": "rm -rf /"})
    assert blocked.allowed is False
    assert "Delete root directory" in blocked.reason


def test_plugin_manager_requires_trust_before_applying_active_contributions(tmp_path: Path):
    from opennova.hooks import HookManager
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "hooks.py").write_text(
        "def pre_tool_use(event):\n"
        "    event['metadata']['trusted_hook'] = True\n"
        "    return event\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.yaml").write_text(
        """
name: demo
enabled: true
commands:
  - name: demo
skills:
  - skills
mcp_servers:
  - name: demo_mcp
    transport: stdio
    command: python
hooks:
  - hooks.py
""".strip(),
        encoding="utf-8",
    )

    config = {"skills": {"dirs": []}, "mcp": {"servers": []}}
    hooks = HookManager(project_path=tmp_path)
    manager = PluginManager(project_path=tmp_path)
    loaded = manager.load_enabled_plugins(config=config, hook_manager=hooks)

    assert [plugin.name for plugin in loaded] == ["demo"]
    assert config["skills"]["dirs"] == []
    assert config["mcp"]["servers"] == []
    assert hooks.run_pre_tool_use({"tool_name": "read_file", "metadata": {}})["metadata"] == {}
    assert manager.commands == []

    manager.trust_plugin("demo")
    loaded = manager.load_enabled_plugins(config=config, hook_manager=hooks)

    assert [plugin.name for plugin in loaded] == ["demo"]
    assert str(plugin_dir / "skills") in config["skills"]["dirs"]
    assert config["mcp"]["servers"][0]["name"] == "demo_mcp"
    assert manager.commands[0]["plugin"] == "demo"
    assert hooks.run_pre_tool_use({"tool_name": "read_file", "metadata": {}})["metadata"]["trusted_hook"] is True


def test_automation_scheduler_records_history_and_supports_pause_resume_delete_run_now(tmp_path: Path):
    from opennova.automation import LocalAutomationScheduler

    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: 100.0)
    task_id = scheduler.schedule_once("docs", "Review docs", run_at=500.0)

    scheduler.pause(task_id)
    assert scheduler.get(task_id).enabled is False
    scheduler.resume(task_id)
    scheduler.run_now(task_id, lambda task: "ok")

    assert scheduler.get(task_id).enabled is False
    assert scheduler.history[-1].task_id == task_id
    assert scheduler.history[-1].success is True
    assert scheduler.history[-1].output == "ok"

    scheduler.delete(task_id)
    assert task_id not in scheduler.tasks


def test_python_symbols_include_qualified_names_and_references_are_disambiguated(tmp_path: Path):
    from opennova.tools.diagnostics_tools import (
        PythonDefinitionTool,
        PythonReferencesTool,
        PythonSymbolsTool,
    )

    source = tmp_path / "sample.py"
    source.write_text(
        """
class Alpha:
    def target(self):
        return 1

def target():
    return 2

value = target()
""".strip(),
        encoding="utf-8",
    )
    config = {"working_dir": str(tmp_path)}

    symbols = PythonSymbolsTool(config=config).execute(path=str(source))
    qualified = {item["qualified_name"] for item in symbols.metadata["symbols"]}

    assert {"Alpha", "Alpha.target", "target", "value"}.issubset(qualified)

    definition = PythonDefinitionTool(config=config).execute(symbol="Alpha.target", path=str(source))
    assert definition.success is True
    assert definition.metadata["definition"]["qualified_name"] == "Alpha.target"

    references = PythonReferencesTool(config=config).execute(symbol="target", path=str(source), max_results=10)
    assert references.metadata["count"] == 1
    assert references.metadata["references"][0]["context"] == "value = target()"


def test_utf8_environment_helper_provides_locale_overrides():
    from opennova.utils.encoding import utf8_environment

    env = utf8_environment({"PATH": "/bin"})

    assert env["LC_ALL"].endswith("UTF-8")
    assert env["LANG"].endswith("UTF-8")
    assert env["PYTHONUTF8"] == "1"


def test_slash_command_registry_exposes_03_productization_commands():
    from opennova.cli.commands import SlashCommandRegistry

    registry = SlashCommandRegistry.default()
    registry.register_plugin_command({"name": "demo", "description": "Demo command", "plugin": "demo"})
    names = registry.names()

    assert {"/permissions", "/plugins", "/hooks", "/automations", "/diagnostics", "/status"}.issubset(names)
    assert "/demo" in names
    assert registry.get("/demo").plugin == "demo"


def test_todo_write_tool_replaces_structured_todos():
    from opennova.tools.todo_tools import TodoWriteTool

    tool = TodoWriteTool()
    result = tool.execute(
        todos=[
            {"id": "1", "content": "Write tests", "status": "done"},
            {"id": "2", "content": "Implement feature", "status": "in_progress"},
        ]
    )

    assert result.success is True
    assert result.metadata["todos"][1]["status"] == "in_progress"
    assert "2 todo" in result.output


def test_checkpoint_manager_records_and_restores_file_snapshots(tmp_path: Path):
    from opennova.checkpoints import CheckpointManager

    target = tmp_path / "file.txt"
    target.write_text("before", encoding="utf-8")
    manager = CheckpointManager(tmp_path)
    checkpoint_id = manager.create("before edit", [target])

    target.write_text("after", encoding="utf-8")
    manager.restore(checkpoint_id)

    assert target.read_text(encoding="utf-8") == "before"
    assert manager.list_checkpoints()[0].id == checkpoint_id


def test_transcript_exporter_writes_session_events(tmp_path: Path):
    from opennova.transcript import TranscriptExporter

    output = TranscriptExporter(tmp_path).export(
        session_id="session-1",
        messages=[{"role": "user", "content": "hello"}],
        tool_events=[{"type": "tool_start", "tool_name": "read_file"}],
    )

    text = output.read_text(encoding="utf-8")
    assert "session-1" in text
    assert "tool_start" in text
