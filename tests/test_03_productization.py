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
    assert str(plugin_dir / "skills") in config["skills"]["dirs"]
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

    assert _use_tui_for_interactive(no_tui=False, force_tui=False, platform="win32") is True


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


def test_no_tui_still_disables_tui_on_windows():
    from opennova.main import _use_tui_for_interactive

    assert _use_tui_for_interactive(no_tui=True, force_tui=False, platform="win32") is False


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
