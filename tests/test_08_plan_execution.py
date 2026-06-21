from __future__ import annotations

from pathlib import Path

import yaml

from opennova.automation import LocalAutomationDaemon, LocalAutomationScheduler, ScheduledTask
from opennova.checkpoints import CheckpointManager
from opennova.cli.automation_commands import handle_automation_command
from opennova.cli.plugin_commands import handle_plugin_command
from opennova.cli.tool_cards import ToolCardStore, build_tool_card_panel
from opennova.plugins import PluginManager
from opennova.runtime.events import ToolEvent
from opennova.tools.diagnostics_tools import PythonBackendStatus, PythonExternalAnalyzer
from opennova.tools.file_tools import EditFileTool, MultiEditFileTool


def test_tool_card_panel_selects_card_and_exposes_actions():
    store = ToolCardStore(collapse_threshold=8)
    store.apply_event(ToolEvent(type="tool_start", tool_id="a", tool_name="read_file"))
    card = store.apply_event(
        ToolEvent(
            type="tool_result",
            tool_id="b",
            tool_name="write_file",
            success=True,
            output="0123456789abcdef",
            diff="--- old\n+++ new\n",
        )
    )
    store.apply_event(
        ToolEvent(
            type="permission_request",
            tool_id="b",
            tool_name="write_file",
            metadata={"reason": "write approval"},
        )
    )

    panel = build_tool_card_panel(store, selected_tool_id=card.tool_id, expanded=True)

    assert panel.selected_tool_id == "b"
    assert panel.cards[0].tool_id == "a"
    assert panel.cards[1].expanded is True
    assert panel.diff_panel == "--- old\n+++ new"
    assert panel.actions["approve"] is True
    assert panel.actions["cancel"] is True


def test_edit_file_creates_checkpoint_before_writing(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    tool = EditFileTool({"working_dir": str(tmp_path), "checkpoint_writes": True})

    result = tool.execute(str(target), "beta", "gamma")

    assert result.success is True
    checkpoint_id = result.metadata["checkpoint_id"]
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"

    CheckpointManager(tmp_path).restore(checkpoint_id)
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_multi_edit_file_creates_checkpoint_before_writing(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    tool = MultiEditFileTool({"working_dir": str(tmp_path), "checkpoint_writes": True})

    result = tool.execute(
        str(target),
        [
            {"old_text": "alpha", "new_text": "one"},
            {"old_text": "beta", "new_text": "two"},
        ],
    )

    assert result.success is True
    checkpoint_id = result.metadata["checkpoint_id"]
    assert target.read_text(encoding="utf-8") == "one\ntwo\n"

    CheckpointManager(tmp_path).restore(checkpoint_id)
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_python_external_analyzer_runs_with_injected_runner(tmp_path: Path):
    analyzer = PythonExternalAnalyzer(
        PythonBackendStatus(
            backend="ruff",
            pyright_available=False,
            ruff_available=True,
        )
    )

    def runner(argv: list[str]) -> object:
        assert argv == ["ruff", "check", str(tmp_path), "--output-format=json"]
        return {"returncode": 0, "stdout": "[]", "stderr": ""}

    result = analyzer.run_diagnostics(tmp_path, runner=runner)

    assert result["backend"] == "ruff"
    assert result["success"] is True
    assert result["output"] == "[]"
    assert result["argv"] == ["ruff", "check", str(tmp_path), "--output-format=json"]


def test_python_external_analyzer_returns_ast_fallback_without_external_backend(tmp_path: Path):
    analyzer = PythonExternalAnalyzer(
        PythonBackendStatus(
            backend="ast",
            pyright_available=False,
            ruff_available=False,
        )
    )

    result = analyzer.run_diagnostics(tmp_path)

    assert result["backend"] == "ast"
    assert result["success"] is True
    assert "fallback" in result["output"]


def test_plugin_lock_and_drift_commands(tmp_path: Path):
    plugin_root = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_root.mkdir(parents=True)
    manifest = {
        "name": "demo",
        "tools": [
            {
                "name": "demo_tool",
                "description": "Demo tool",
                "command": "echo demo",
                "permission": "read",
            }
        ],
    }
    (plugin_root / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    manager = PluginManager(tmp_path)
    manager.trust_plugin("demo")
    manager.load_enabled_plugins({})

    locked = handle_plugin_command(manager, "lock")
    assert locked.success is True
    assert (tmp_path / ".opennova" / "plugins" / "lock.json").exists()

    manifest["tools"][0]["permission"] = "edit"
    (plugin_root / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    manager.load_enabled_plugins({})
    drift = handle_plugin_command(manager, "drift")

    assert drift.success is True
    assert "permission changed" in drift.output


def test_automation_daemon_run_until_idle_and_command(tmp_path: Path):
    now = 1000.0
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: now)
    first_id = scheduler.schedule_once("first", "one", run_at=now)
    second_id = scheduler.schedule_once("second", "two", run_at=now)
    daemon = LocalAutomationDaemon(scheduler)
    calls: list[str] = []

    def runner(task: ScheduledTask) -> str:
        calls.append(task.id)
        return task.name

    assert daemon.run_until_idle(runner) == []
    daemon.start()
    events = daemon.run_until_idle(runner, max_ticks=3)

    assert calls == [first_id, second_id]
    assert len(events) == 2
    assert daemon.last_events == events

    third_id = scheduler.schedule_once("third", "three", run_at=now)
    result = handle_automation_command(scheduler, "daemon run", runner=runner, daemon=daemon)

    assert result.success is True
    assert third_id in calls
    assert "automation_run" in result.output
