"""Tests for 03 runtime productization work."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

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
                tool_calls=[
                    ToolCall(
                        id="llm-call-1", name="read_file", arguments={"file_path": "README.md"}
                    )
                ],
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

    assert (
        guardrails.check_tool_call("write_file", {"file_path": "ok.txt"}).requires_confirmation
        is False
    )
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
    sources = manager.get_skill_sources()
    assert len(sources) == 1
    assert sources[0].root == plugin_dir / "skills"
    assert sources[0].plugin_name == "demo"
    assert config["mcp"]["servers"][0]["name"] == "demo_mcp"
    assert manager.commands[0]["plugin"] == "demo"
    assert (
        hooks.run_pre_tool_use({"tool_name": "read_file", "metadata": {}})["metadata"][
            "trusted_hook"
        ]
        is True
    )


def test_automation_scheduler_records_history_and_supports_pause_resume_delete_run_now(
    tmp_path: Path,
):
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

    definition = PythonDefinitionTool(config=config).execute(
        symbol="Alpha.target", path=str(source)
    )
    assert definition.success is True
    assert definition.metadata["definition"]["qualified_name"] == "Alpha.target"

    references = PythonReferencesTool(config=config).execute(
        symbol="target", path=str(source), max_results=10
    )
    assert references.metadata["count"] == 1
    assert references.metadata["references"][0]["context"] == "value = target()"


def test_utf8_environment_helper_provides_locale_overrides():
    from opennova.utils.encoding import utf8_environment

    env = utf8_environment({"PATH": "/bin"})

    assert env["LC_ALL"].endswith("UTF-8")
    assert env["LANG"].endswith("UTF-8")
    assert env["PYTHONUTF8"] == "1"


def test_session_title_snippet_formats_short_and_long_prompts():
    from opennova.session import format_session_title_snippet

    assert format_session_title_snippet("short prompt") == "short prompt"
    assert format_session_title_snippet("12345678901234567890") == "12345678901234567890"
    assert format_session_title_snippet("123456789012345678901") == "12345678901234567..."


def test_session_manager_snapshot_persists_transcript_and_newest_sorting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import os

    from opennova.session import SessionManager

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(project_path=str(project))

    first_id = manager.start_session()
    manager.save_snapshot(
        [Message(role="user", content="first prompt")],
        transcript_events=[{"kind": "user_message", "text": "first prompt"}],
    )

    second_id = manager.start_session()
    manager.save_snapshot(
        [Message(role="user", content="a much longer first prompt than twenty chars")],
        transcript_events=[{"kind": "user_message", "text": "a much longer first prompt than twenty chars"}],
    )

    first_file = manager._sessions_dir / f"{first_id}.jsonl"
    second_file = manager._sessions_dir / f"{second_id}.jsonl"
    os.utime(first_file, (100.0, 100.0))
    os.utime(second_file, (200.0, 200.0))

    sessions = manager.list_sessions()
    loaded = manager.load_session_with_summary(second_id)

    assert [session.session_id for session in sessions] == [second_id, first_id]
    assert loaded.transcript_events[0].payload["kind"] == "user_message"
    assert loaded.messages[0].content == "a much longer first prompt than twenty chars"


def test_session_manager_dedupes_legacy_appended_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from opennova.session import SessionManager

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(project_path=str(project))
    session_id = manager.start_session()

    def legacy_message_line(message: dict[str, Any]) -> str:
        return (
            f'{{"type":"message","session_id":"{session_id}","message":'
            f"{json.dumps(message, ensure_ascii=False)}}}"
        )

    first = Message(role="user", content="first").to_dict()
    second = Message(role="assistant", content="second").to_dict()
    third = Message(role="user", content="third").to_dict()
    file_path = manager._sessions_dir / f"{session_id}.jsonl"
    file_path.write_text(
        "\n".join(
            [
                legacy_message_line(first),
                legacy_message_line(second),
                legacy_message_line(first),
                legacy_message_line(second),
                legacy_message_line(third),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = manager.load_session(session_id, apply_compression=False)

    assert [message.content for message in loaded] == ["first", "second", "third"]


def test_runtime_resume_session_restores_messages_summary_and_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from opennova.memory.context import ContextManager
    from opennova.runtime.agent import AgentRuntime
    from opennova.session import SessionManager

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    manager = SessionManager(project_path=str(tmp_path / "project"))
    session_id = manager.start_session()
    manager.save_snapshot(
        [Message(role="user", content="hello"), Message(role="assistant", content="world")],
        compression_summary="compressed summary",
        transcript_events=[
            {"kind": "user_message", "text": "hello"},
            {"kind": "assistant_markdown", "content": "world"},
        ],
    )

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.context_manager = ContextManager(model="gpt-4o")
    runtime.session_manager = manager
    runtime.session_transcript = []

    loaded = AgentRuntime.resume_session(runtime, session_id)

    assert loaded.compression_summary == "compressed summary"
    assert [message.content for message in loaded.messages] == ["hello", "world"]
    assert runtime.context_manager.get_compressed_summary() == "compressed summary"
    assert runtime.session_transcript[0]["kind"] == "user_message"


def test_runtime_resume_session_keeps_writing_to_original_session_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from opennova.memory.context import ContextManager
    from opennova.runtime.agent import AgentRuntime
    from opennova.session import SessionManager

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project_path = tmp_path / "project"
    project_path.mkdir()
    manager = SessionManager(project_path=str(project_path))
    session_id = manager.start_session()
    manager.save_snapshot(
        [Message(role="user", content="hello"), Message(role="assistant", content="world")]
    )
    original_file = manager._sessions_dir / f"{session_id}.jsonl"

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.context_manager = ContextManager(model="gpt-4o")
    runtime.session_manager = manager
    runtime.session_transcript = []

    AgentRuntime.resume_session(runtime, session_id)
    runtime.context_manager.add_message(Message(role="user", content="again"))
    runtime._save_session_messages()

    session_files = sorted(manager._sessions_dir.glob("*.jsonl"))

    assert runtime.session_manager.session_id == session_id
    assert session_files == [original_file]
    loaded = manager.load_session(session_id, apply_compression=False)
    assert [message.content for message in loaded] == ["hello", "world", "again"]


def test_session_manager_snapshot_persists_plan_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from opennova.runtime.state import Plan, PlanStep
    from opennova.session import SessionManager

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(project_path=str(project))
    session_id = manager.start_session()
    plan = Plan(task="Saved plan", steps=[PlanStep(id="step_1", description="review")])

    manager.save_snapshot(
        [Message(role="user", content="hello")],
        plan_state={
            "current_plan": plan.to_dict(),
            "plan_file_path": str(project / ".opennova" / "plan" / "saved-plan.md"),
            "plan_approval_status": "awaiting_approval",
        },
    )

    loaded = manager.load_session_with_summary(session_id)

    assert loaded.plan_state["current_plan"]["task"] == "Saved plan"
    assert loaded.plan_state["plan_approval_status"] == "awaiting_approval"


def test_runtime_resume_session_restores_plan_state_from_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from opennova.memory.context import ContextManager
    from opennova.runtime.agent import AgentRuntime
    from opennova.runtime.state import Plan, PlanStep
    from opennova.session import SessionManager

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(project_path=str(project))
    session_id = manager.start_session()
    plan = Plan(task="Saved plan", steps=[PlanStep(id="step_1", description="review")])
    plan_path = project / ".opennova" / "plan" / "saved-plan.md"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        """# Saved Plan: Saved plan

- Task: Saved plan

## Summary

Summary

## Steps

### step_1
- Description: review from file
- Status: `pending`
""",
        encoding="utf-8",
    )
    manager.save_snapshot(
        [Message(role="user", content="hello")],
        plan_state={
            "current_plan": plan.to_dict(),
            "plan_file_path": str(plan_path),
            "plan_approval_status": "awaiting_approval",
        },
    )

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.context_manager = ContextManager(model="gpt-4o")
    runtime.session_manager = manager
    runtime.session_transcript = []
    runtime.state = type("State", (), {})()
    runtime.state.current_plan = None
    runtime.state.plan_file_path = None
    runtime.state.plan_approval_status = None
    runtime.state.set_plan = lambda plan_obj: setattr(runtime.state, "current_plan", plan_obj)
    runtime.state.set_plan_file_path = lambda path: setattr(runtime.state, "plan_file_path", path)

    loaded = AgentRuntime.resume_session(runtime, session_id)

    assert loaded.plan_state["plan_approval_status"] == "awaiting_approval"
    assert runtime.state.current_plan.task == "Saved plan"
    assert runtime.state.current_plan.steps[0].description == "review from file"
    assert str(runtime.state.plan_file_path).endswith("saved-plan.md")


def test_slash_command_registry_exposes_03_productization_commands():
    from opennova.cli.commands import SlashCommandRegistry

    registry = SlashCommandRegistry.default()
    registry.register_plugin_command(
        {"name": "demo", "description": "Demo command", "plugin": "demo"}
    )
    names = registry.names()

    assert {
        "/permissions",
        "/plugins",
        "/hooks",
        "/automations",
        "/diagnostics",
        "/status",
    }.issubset(names)
    assert "/demo" in names
    assert registry.get("/demo").plugin == "demo"


def test_interactive_mode_defaults_to_tui_on_windows():
    from opennova.main import _use_tui_for_interactive

    assert _use_tui_for_interactive(force_tui=False, platform="win32") is True


def test_windows_tui_keeps_ime_committed_unicode_chars():
    from opennova.cli.windows_tui_driver import should_queue_console_key

    assert (
        should_queue_console_key(
            key="中",
            key_down=True,
            control_key_state=0x0010,
            virtual_key_code=0,
        )
        is True
    )


def test_windows_tui_ignores_control_only_virtual_key_events():
    from opennova.cli.windows_tui_driver import should_queue_console_key

    assert (
        should_queue_console_key(
            key="\x00",
            key_down=True,
            control_key_state=0x0010,
            virtual_key_code=0,
        )
        is False
    )


def test_windows_tui_maps_navigation_keys_to_textual_names():
    from opennova.cli.windows_tui_driver import format_windows_virtual_key

    assert format_windows_virtual_key(13, 0) == "enter"
    assert format_windows_virtual_key(8, 0) == "backspace"
    assert format_windows_virtual_key(37, 0) == "left"
    assert format_windows_virtual_key(39, 0) == "right"


def test_windows_tui_maps_modified_navigation_keys_to_textual_names():
    from opennova.cli.windows_tui_driver import format_windows_virtual_key

    assert format_windows_virtual_key(37, 0x0008) == "ctrl+left"
    assert format_windows_virtual_key(39, 0x0010) == "shift+right"
    assert format_windows_virtual_key(9, 0x0010) == "shift+tab"


def test_windows_tui_debug_record_includes_unicode_key_details():
    from opennova.cli.windows_tui_driver import build_console_key_debug_record

    record = build_console_key_debug_record(
        key="中",
        key_down=True,
        control_key_state=0x0010,
        virtual_key_code=0,
        virtual_scan_code=0,
    )

    assert record["key"] == "中"
    assert record["codepoint"] == "U+4E2D"
    assert record["queued"] is True
    assert record["textual_key"] is None


def test_windows_tui_debug_writer_appends_jsonl(tmp_path: Path):
    import json

    from opennova.cli.windows_tui_driver import write_console_key_debug_record

    path = tmp_path / "keys.jsonl"
    write_console_key_debug_record(
        path,
        {
            "key": "中",
            "codepoint": "U+4E2D",
            "queued": True,
        },
    )

    assert json.loads(path.read_text(encoding="utf-8"))["key"] == "中"


def test_interactive_mode_always_uses_tui():
    from opennova.main import _use_tui_for_interactive

    assert _use_tui_for_interactive(force_tui=False, platform="win32") is True
    assert _use_tui_for_interactive(force_tui=False, platform="darwin") is True


def test_cli_rejects_removed_no_tui_option(monkeypatch):
    from opennova.main import main

    monkeypatch.setattr("opennova.main._load_and_validate_config", lambda *args, **kwargs: {})

    result = CliRunner().invoke(main, ["run", "--no-tui"])

    assert result.exit_code != 0
    assert "No such option: --no-tui" in result.output


def test_cli_resume_flag_launches_tui_in_picker_mode(monkeypatch):
    from opennova.main import main

    seen = []

    async def fake_run_tui(config, startup_resume_mode=None):
        seen.append(startup_resume_mode)

    monkeypatch.setattr("opennova.main._load_and_validate_config", lambda *args, **kwargs: {})
    monkeypatch.setattr("opennova.cli.tui.run_tui", fake_run_tui)

    result = CliRunner().invoke(main, ["--resume"])

    assert result.exit_code == 0
    assert seen == ["resume"]


def test_cli_continue_flag_launches_tui_in_continue_mode(monkeypatch):
    from opennova.main import main

    seen = []

    async def fake_run_tui(config, startup_resume_mode=None):
        seen.append(startup_resume_mode)

    monkeypatch.setattr("opennova.main._load_and_validate_config", lambda *args, **kwargs: {})
    monkeypatch.setattr("opennova.cli.tui.run_tui", fake_run_tui)

    result = CliRunner().invoke(main, ["--continue"])

    assert result.exit_code == 0
    assert seen == ["continue"]


def test_cli_resume_flag_rejects_task(monkeypatch):
    from opennova.main import main

    monkeypatch.setattr("opennova.main._load_and_validate_config", lambda *args, **kwargs: {})

    task_result = CliRunner().invoke(main, ["--resume", "run", "hello"])

    assert task_result.exit_code != 0
    assert "--resume/--continue cannot be used with a direct task." in task_result.output


def test_tui_system_clipboard_uses_native_platform_commands():
    from opennova.cli.tui import _copy_to_system_clipboard

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 0})()

    assert _copy_to_system_clipboard("hello", system_name="Darwin", run=run) is True
    assert calls[-1][0] == ["pbcopy"]
    assert calls[-1][1]["input"] == "hello"

    assert _copy_to_system_clipboard("hello", system_name="Windows", run=run) is True
    assert calls[-1][0] == ["clip"]


def test_tui_system_clipboard_prefers_wayland_then_xclip_on_linux():
    from opennova.cli.tui import _copy_to_system_clipboard

    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return type("Result", (), {"returncode": 0})()

    assert (
        _copy_to_system_clipboard(
            "hello",
            system_name="Linux",
            run=run,
            which=lambda command: "/usr/bin/wl-copy" if command == "wl-copy" else None,
        )
        is True
    )
    assert commands[-1] == ["wl-copy"]

    assert (
        _copy_to_system_clipboard(
            "hello",
            system_name="Linux",
            run=run,
            which=lambda command: "/usr/bin/xclip" if command == "xclip" else None,
        )
        is True
    )
    assert commands[-1] == ["xclip", "-selection", "clipboard"]


def test_tui_system_clipboard_returns_false_on_command_failure():
    from opennova.cli.tui import _copy_to_system_clipboard

    def run(command, **kwargs):
        raise OSError("clipboard unavailable")

    assert _copy_to_system_clipboard("hello", system_name="Darwin", run=run) is False


def test_tui_messages_log_extracts_selected_rendered_text():
    from rich.segment import Segment
    from textual.geometry import Offset
    from textual.selection import Selection
    from textual.strip import Strip

    from opennova.cli.tui import _MessagesLog

    messages = _MessagesLog()
    messages.lines = [
        Strip([Segment("hello")]),
        Strip([Segment("world")]),
    ]

    selected = messages.get_selection(Selection.from_offsets(Offset(1, 0), Offset(3, 1)))

    assert selected == ("ello\nwor", "\n")


def test_tui_messages_log_extracts_wide_character_selection():
    from rich.segment import Segment
    from textual.geometry import Offset
    from textual.selection import Selection
    from textual.strip import Strip

    from opennova.cli.tui import _MessagesLog

    messages = _MessagesLog()
    messages.lines = [Strip([Segment("中文abc")])]

    selected = messages.get_selection(Selection.from_offsets(Offset(0, 0), Offset(2, 0)))

    assert selected == ("中文", "\n")


@pytest.mark.asyncio
async def test_tui_messages_log_supports_mouse_selection_in_place():
    from textual.app import App, ComposeResult

    from opennova.cli.tui import _MessagesLog

    class MessagesHarness(App):
        def compose(self) -> ComposeResult:
            yield _MessagesLog(
                id="messages",
                highlight=False,
                markup=False,
                wrap=True,
            )

        def on_mount(self) -> None:
            self.query_one("#messages", _MessagesLog).write("hello world", width=20)

    app = MessagesHarness()
    async with app.run_test(size=(40, 5)) as pilot:
        await pilot.pause()
        await pilot.mouse_down("#messages", offset=(0, 0))
        await pilot.mouse_up("#messages", offset=(4, 0))
        await pilot.pause()

        assert app.screen.get_selected_text() == "hello"


def test_tui_copy_selection_copies_current_screen_selection(monkeypatch):
    from opennova.cli.tui import OpenNovaTUI

    class Screen:
        cleared = False

        def get_selected_text(self):
            return "selected text"

        def clear_selection(self):
            self.cleared = True

    copied = []
    statuses = []
    app = type(
        "FakeTUI",
        (),
        {
            "screen": Screen(),
            "copy_to_clipboard": copied.append,
            "_set_status": statuses.append,
        },
    )()
    monkeypatch.setattr("opennova.cli.tui._copy_to_system_clipboard", lambda text: True)

    OpenNovaTUI.action_copy_selection(app)

    assert copied == ["selected text"]
    assert app.screen.cleared is True
    assert "Copied selection" in statuses[-1]


def test_tui_copy_selection_without_selection_prompts_user(monkeypatch):
    from opennova.cli.tui import OpenNovaTUI

    class Screen:
        def get_selected_text(self):
            return ""

        def clear_selection(self):
            raise AssertionError("should not clear an empty selection")

    statuses = []
    app = type(
        "FakeTUI",
        (),
        {
            "screen": Screen(),
            "copy_to_clipboard": lambda self, text: (_ for _ in ()).throw(
                AssertionError("should not copy without selection")
            ),
            "_set_status": lambda self, text: statuses.append(text),
        },
    )()
    monkeypatch.setattr("opennova.cli.tui._copy_to_system_clipboard", lambda text: False)

    OpenNovaTUI.action_copy_selection(app)

    assert "Select text" in statuses[-1]


def test_tui_copy_bindings_include_macos_command_c():
    from opennova.cli.tui import OpenNovaTUI

    bindings = {binding.key: binding for binding in OpenNovaTUI.BINDINGS}

    assert "super+c" in bindings
    assert bindings["super+c"].priority is True
    assert bindings["ctrl+c"].priority is True


def test_tui_ctrl_c_copies_selection_when_idle(monkeypatch):
    from opennova.cli.tui import OpenNovaTUI

    class Screen:
        cleared = False

        def get_selected_text(self):
            return "selected text"

        def clear_selection(self):
            self.cleared = True

    copied = []
    statuses = []
    app = type(
        "FakeTUI",
        (),
        {
            "_agent_task": None,
            "_last_ctrl_c": 0.0,
            "screen": Screen(),
            "copy_to_clipboard": copied.append,
            "_set_status": statuses.append,
            "_is_agent_running": lambda self: False,
            "action_copy_selection": lambda self: OpenNovaTUI.action_copy_selection(self),
            "call_after_refresh": lambda self, callback: None,
            "_focus_input": lambda self: None,
            "exit": lambda self: (_ for _ in ()).throw(
                AssertionError("should not exit when text is selected")
            ),
        },
    )()
    monkeypatch.setattr("opennova.cli.tui._copy_to_system_clipboard", lambda text: True)

    OpenNovaTUI.action_cancel(app)

    assert copied == ["selected text"]
    assert app.screen.cleared is True
    assert "Copied selection" in statuses[-1]


def test_tui_ctrl_c_still_cancels_running_agent(monkeypatch):
    from opennova.cli.tui import OpenNovaTUI

    class Task:
        cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    class Screen:
        def get_selected_text(self):
            raise AssertionError("running Ctrl+C should cancel before checking selection")

    statuses = []
    task = Task()
    app = type(
        "FakeTUI",
        (),
        {
            "_agent_task": task,
            "screen": Screen(),
            "_set_status": statuses.append,
            "_is_agent_running": lambda self: True,
        },
    )()
    monkeypatch.setattr(
        "opennova.cli.tui._copy_to_system_clipboard",
        lambda text: (_ for _ in ()).throw(AssertionError("should not copy")),
    )

    OpenNovaTUI.action_cancel(app)

    assert task.cancelled is True
    assert "Cancelling" in statuses[-1]


def test_tui_user_message_uses_subtle_background():
    from opennova.cli.tui import _USER_MESSAGE_STYLE, _format_user_message

    message = _format_user_message("hello")

    assert message.plain == "You: hello"
    assert any(span.style == _USER_MESSAGE_STYLE for span in message.spans)


def test_tui_tool_execution_line_uses_solid_circle_icon():
    from opennova.cli.tui import _format_tool_execution

    assert _format_tool_execution("read_file", "path='README.md'").startswith("⏺ ")
    assert "Executing:" in _format_tool_execution("read_file", "path='README.md'")


def test_tui_truncates_tool_output_to_20_lines():
    from opennova.cli.tui import _truncate_tool_output

    output = "\n".join(f"line {index}" for index in range(1, 26))

    truncated = _truncate_tool_output("search_code", output)

    assert truncated.splitlines()[:20] == [f"line {index}" for index in range(1, 21)]
    assert truncated.splitlines()[-1] == "... (output truncated, 20/25 lines)"


def test_tui_hides_read_file_and_list_directory_outputs():
    from opennova.cli.tui import _truncate_tool_output

    assert _truncate_tool_output("read_file", "secret") == ""
    assert _truncate_tool_output("list_directory", "secret") == ""


def test_tui_truncates_create_file_diff_to_20_lines():
    from opennova.cli.tui import OpenNovaTUI

    writes: list[str] = []
    log = type("Log", (), {"write": lambda self, value: writes.append(value)})()
    diff = "\n".join(f"+line {index}" for index in range(1, 26))

    OpenNovaTUI._write_diff(object(), log, diff, max_lines=20)

    assert writes[1:21] == [f"+line {index}" for index in range(1, 21)]
    assert writes[21] == "[dim]... (diff truncated, 20/25 lines)[/dim]"


def test_tui_stream_callback_does_not_duplicate_final_answer():
    from opennova.cli.tui import OpenNovaTUI
    from opennova.providers.base import StreamChunk

    class Agent:
        def __init__(self):
            self.callbacks = {}

        def register_callback(self, event_name, callback):
            self.callbacks[event_name] = callback

    class Log:
        def __init__(self):
            self.writes = []

        def write(self, value):
            self.writes.append(value)

    log = Log()
    app = type(
        "FakeTUI",
        (),
        {
            "agent": Agent(),
            "_tool_progress": type("Progress", (), {})(),
            "query_one": lambda self, selector: log,
        },
    )()

    OpenNovaTUI._register_callbacks(app)
    app.agent.callbacks["stream"](
        StreamChunk(content="现在北京时间是 **2026 年 6 月 20 日（周六）23:31:16**。")
    )
    app.agent.callbacks["stream"](StreamChunk(content="", finish_reason="stop"))

    assert log.writes == []


def test_tui_canonical_tool_result_hides_read_file_output():
    from opennova.cli.tui import OpenNovaTUI
    from opennova.runtime.events import ToolEvent

    class Agent:
        def __init__(self):
            self.callbacks = {}

        def register_callback(self, event_name, callback):
            self.callbacks[event_name] = callback

    class Progress:
        def __init__(self):
            self.waiting_for_interaction = False
            self.interaction_label = ""
            self.current_tool_name = ""
            self.current_tool_id = ""
            self.current_args = {}
            self.started_at = 0.0

        def clear_interaction(self):
            self.waiting_for_interaction = False
            self.interaction_label = ""

    writes: list[tuple[str, Any]] = []

    class Log:
        def write(self, value):
            writes.append(("write", value))

    tool_cards = type("Cards", (), {"apply_event": lambda self, event: None})()
    app = type(
        "FakeTUI",
        (),
        {
            "agent": Agent(),
            "_tool_progress": Progress(),
            "_tool_cards": tool_cards,
            "query_one": lambda self, selector: Log(),
            "_write_tool_start": lambda self, log, tool_name, detail: writes.append(
                ("start", tool_name, detail)
            ),
            "_write_tool_result": lambda self, log, **kwargs: writes.append(
                ("result", kwargs["tool_name"], kwargs["output"])
            ),
        },
    )()

    OpenNovaTUI._register_callbacks(app)

    app.agent.callbacks["tool_event"](
        ToolEvent(type="tool_start", tool_id="tool_1", tool_name="read_file")
    )
    app.agent.callbacks["tool_event"](
        ToolEvent(
            type="tool_result",
            tool_id="tool_1",
            tool_name="read_file",
            success=True,
            output="1: hidden content",
            duration_ms=12,
        )
    )

    assert ("result", "read_file", "") in writes


def test_tui_resume_without_args_uses_picker():
    from opennova.cli.tui import OpenNovaTUI

    class Log:
        def write(self, value):
            raise AssertionError("picker path should not write directly")

    calls = []

    async def fake_resume_via_picker(self, exclude_current):
        calls.append(exclude_current)
        return True

    app = type(
        "FakeTUI",
        (),
        {
            "_resume_via_picker": fake_resume_via_picker,
            "query_one": lambda self, selector: Log(),
        },
    )()

    import asyncio

    asyncio.run(OpenNovaTUI._cmd_resume(app, ""))

    assert calls == [True]


def test_slash_command_registry_marks_resume_as_async():
    from opennova.cli.commands import SlashCommandRegistry

    registry = SlashCommandRegistry.default()

    assert registry.get("/resume").sync is False


@pytest.mark.asyncio
async def test_tui_input_submission_launches_resume_picker_in_background():
    from opennova.cli.tui import OpenNovaTUI

    class Event:
        def __init__(self, value: str):
            self.value = value

        def stop(self) -> None:
            return None

    class InputWidget:
        def __init__(self) -> None:
            self.value = "/resume"

    class Log:
        def __init__(self) -> None:
            self.writes = []

        def write(self, value):
            self.writes.append(value)

        def scroll_end(self, animate=False):
            return None

    input_widget = InputWidget()
    log = Log()
    launched = []

    async def fail_if_called_directly(self, text: str) -> None:
        raise AssertionError("/resume should not be awaited directly from on_input_submitted")

    def launch_task(self, coro) -> None:
        launched.append(coro)
        coro.close()

    app = type(
        "FakeTUI",
        (),
        {
            "_SYNC_COMMANDS": OpenNovaTUI._SYNC_COMMANDS,
            "_interaction_mode": False,
            "_interaction_future": None,
            "_last_submitted_text": "",
            "_last_submitted_time": 0.0,
            "_clear_suggestions": lambda self: None,
            "_add_to_history": lambda self, text: None,
            "_write_user_message": lambda self, log, text: log.write(("user", text)),
            "_focus_input": lambda self: None,
            "_is_agent_running": lambda self: False,
            "_handle_command": fail_if_called_directly,
            "_launch_agent_task": launch_task,
            "query_one": lambda self, selector, *args: input_widget if selector == "#input" else log,
        },
    )()

    await OpenNovaTUI.on_input_submitted(app, Event("/resume"))

    assert launched
    assert input_widget.value == ""
    assert ("user", "/resume") in log.writes


def test_tui_startup_continue_restores_newest_session():
    from opennova.cli.tui import OpenNovaTUI
    from opennova.session import SessionMeta

    calls = []

    async def fake_resume(self, session_id):
        calls.append(session_id)
        return True

    app = type(
        "FakeTUI",
        (),
        {
            "_startup_resume_mode": "continue",
            "_get_resumable_sessions": lambda self, exclude_current: [
                SessionMeta(
                    session_id="session-new",
                    created=0.0,
                    modified=20.0,
                    first_prompt="new",
                    message_count=2,
                    file_size=1,
                    file_path=Path("/tmp/new.jsonl"),
                ),
                SessionMeta(
                    session_id="session-old",
                    created=0.0,
                    modified=10.0,
                    first_prompt="old",
                    message_count=1,
                    file_size=1,
                    file_path=Path("/tmp/old.jsonl"),
                ),
            ],
            "_resume_session_by_id": fake_resume,
            "_show_welcome": lambda self: (_ for _ in ()).throw(
                AssertionError("should not show welcome when continue succeeds")
            ),
            "_focus_input": lambda self: None,
            "query_one": lambda self, selector: type("Log", (), {"write": lambda self, value: None})(),
        },
    )()

    import asyncio

    asyncio.run(OpenNovaTUI._handle_startup_resume(app))

    assert calls == ["session-new"]


def test_tui_restore_loaded_session_replays_transcript_events():
    from opennova.cli.tui import OpenNovaTUI
    from opennova.session import LoadedSession, SessionTranscriptEvent

    class Log:
        def __init__(self):
            self.writes = []
            self.cleared = False

        def write(self, value):
            self.writes.append(value)

        def clear_messages(self):
            self.cleared = True
            self.writes.clear()

        def scroll_end(self, animate=False):
            return None

    log = Log()
    app = type(
        "FakeTUI",
        (),
        {
            "_replaying_transcript": False,
            "_write_user_message": lambda self, log, text, record=False: log.write(("user", text)),
            "_write_assistant_message": lambda self, log, text, record=False: log.write(("assistant", text)),
            "_write_tool_start": lambda self, log, tool_name, detail, record=False: log.write(
                ("tool_start", tool_name, detail)
            ),
            "_write_tool_result": lambda self, log, **kwargs: log.write(
                ("tool_result", kwargs["tool_name"], kwargs["summary_markup"])
            ),
            "_replay_transcript_event": lambda self, log, event: OpenNovaTUI._replay_transcript_event(
                self, log, event
            ),
            "_replay_legacy_message": lambda self, log, message: OpenNovaTUI._replay_legacy_message(
                self, log, message
            ),
        },
    )()

    loaded = LoadedSession(
        session_id="session-1",
        messages=[],
        transcript_events=[
            SessionTranscriptEvent(kind="user_message", payload={"kind": "user_message", "text": "hello"}),
            SessionTranscriptEvent(
                kind="assistant_markdown",
                payload={"kind": "assistant_markdown", "content": "world"},
            ),
            SessionTranscriptEvent(
                kind="tool_start",
                payload={"kind": "tool_start", "tool_name": "read_file", "detail": "(path='README.md')"},
            ),
        ],
        compression_summary=None,
        compression_markers=[],
    )

    OpenNovaTUI._restore_loaded_session(app, log, loaded)

    assert log.cleared is True
    assert log.writes == [
        ("user", "hello"),
        ("assistant", "world"),
        ("tool_start", "read_file", "(path='README.md')"),
    ]


def test_ask_question_dialog_options_always_include_custom_answer():
    from opennova.cli.ask_question_dialog import CUSTOM_OPTION_ID, options_with_custom_answer

    options = options_with_custom_answer(
        [
            {"index": 1, "label": "A", "description": "First"},
            {"index": 2, "label": "B", "description": "Second"},
        ]
    )

    assert options[-1]["id"] == CUSTOM_OPTION_ID
    assert options[-1]["custom"] is True
    assert options[-1]["index"] == 3


def test_ask_question_dialog_builds_selected_option_answer():
    from opennova.cli.ask_question_dialog import answer_from_selected_option

    option = {"index": 1, "label": "Use cache", "description": "Fast"}

    answer = answer_from_selected_option(
        question="Which path?",
        header="Plan",
        option=option,
    )

    assert answer["answer"] == "Use cache"
    assert answer["selected_options"] == [option]
    assert answer["skipped"] is False
    assert answer["custom"] is False


def test_ask_question_dialog_builds_custom_text_answer():
    from opennova.cli.ask_question_dialog import (
        answer_from_selected_option,
        options_with_custom_answer,
    )

    custom_option = options_with_custom_answer([])[-1]

    answer = answer_from_selected_option(
        question="Which path?",
        header="Plan",
        option=custom_option,
        custom_text="Use my own plan",
    )

    assert answer["answer"] == "Use my own plan"
    assert answer["selected_options"] == []
    assert answer["skipped"] is False
    assert answer["custom"] is True


@pytest.mark.asyncio
async def test_ask_question_dialog_selects_option_with_enter():
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    from opennova.cli.ask_question_dialog import AskQuestionDialog

    class DialogHarness(App):
        def __init__(self):
            super().__init__()
            self.answer = None

        def compose(self) -> ComposeResult:
            yield Static("ready")

        async def on_mount(self) -> None:
            await self.push_screen(
                AskQuestionDialog(
                    question="Pick one",
                    header=None,
                    options=[
                        {"index": 1, "label": "First", "description": "A"},
                        {"index": 2, "label": "Second", "description": "B"},
                    ],
                ),
                callback=self._on_answer,
            )

        def _on_answer(self, answer):
            self.answer = answer
            self.exit()

    app = DialogHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")

    assert app.answer["answer"] == "First"
    assert app.answer["custom"] is False


@pytest.mark.asyncio
async def test_ask_question_dialog_accepts_custom_text_with_keyboard():
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    from opennova.cli.ask_question_dialog import AskQuestionDialog

    class DialogHarness(App):
        def __init__(self):
            super().__init__()
            self.answer = None

        def compose(self) -> ComposeResult:
            yield Static("ready")

        async def on_mount(self) -> None:
            await self.push_screen(
                AskQuestionDialog(
                    question="Pick one",
                    header=None,
                    options=[
                        {"index": 1, "label": "First", "description": "A"},
                        {"index": 2, "label": "Second", "description": "B"},
                    ],
                ),
                callback=self._on_answer,
            )

        def _on_answer(self, answer):
            self.answer = answer
            self.exit()

    app = DialogHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "down", "enter", "u", "s", "e", "enter")

    assert app.answer["answer"] == "use"
    assert app.answer["custom"] is True


@pytest.mark.asyncio
async def test_ask_question_dialog_accepts_multi_select_with_keyboard():
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    from opennova.cli.ask_question_dialog import AskQuestionDialog

    class DialogHarness(App):
        def __init__(self):
            super().__init__()
            self.answer = None

        def compose(self) -> ComposeResult:
            yield Static("ready")

        async def on_mount(self) -> None:
            await self.push_screen(
                AskQuestionDialog(
                    question="Pick features",
                    header=None,
                    options=[
                        {"index": 1, "label": "First", "description": "A"},
                        {"index": 2, "label": "Second", "description": "B"},
                    ],
                    multi_select=True,
                ),
                callback=self._on_answer,
            )

        def _on_answer(self, answer):
            self.answer = answer
            self.exit()

    app = DialogHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space", "down", "space", "enter")

    assert app.answer["answer"] == "First, Second"
    assert [option["label"] for option in app.answer["selected_options"]] == ["First", "Second"]
    assert app.answer["custom"] is False


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
