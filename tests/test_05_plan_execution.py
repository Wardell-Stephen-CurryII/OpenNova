"""Tests for 05 plan execution."""

from __future__ import annotations

from pathlib import Path


def test_checkpoint_command_list_diff_and_restore(tmp_path: Path):
    from opennova.checkpoints import CheckpointManager
    from opennova.cli.checkpoint_commands import handle_checkpoint_command

    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")
    checkpoint_id = CheckpointManager(tmp_path).create("before edit", [target])
    target.write_text("after\n", encoding="utf-8")

    listed = handle_checkpoint_command(tmp_path, "list")
    diffed = handle_checkpoint_command(tmp_path, f"diff {checkpoint_id}")
    restored = handle_checkpoint_command(tmp_path, f"restore {checkpoint_id}")

    assert checkpoint_id[:8] in listed.output
    assert "-before" in diffed.output
    assert "+after" in diffed.output
    assert restored.success is True
    assert target.read_text(encoding="utf-8") == "before\n"


def test_tool_card_store_tracks_events_diff_and_cancellation():
    from opennova.cli.tool_cards import ToolCardStore
    from opennova.runtime.events import ToolEvent

    store = ToolCardStore(collapse_threshold=5)
    store.apply_event(ToolEvent(type="tool_start", tool_id="tool_1", tool_name="edit_file"))
    store.apply_event(
        ToolEvent(
            type="permission_request",
            tool_id="tool_1",
            tool_name="edit_file",
            metadata={"reason": "Needs approval"},
        )
    )
    store.apply_event(
        ToolEvent(
            type="tool_result",
            tool_id="tool_1",
            tool_name="edit_file",
            success=True,
            output="abcdefghi",
            diff="--- a\n+++ b",
        )
    )
    store.cancel("tool_1")

    card = store.get("tool_1")
    assert card.tool_name == "edit_file"
    assert card.status == "cancelled"
    assert card.collapsible is True
    assert card.output_preview == "abcde\n... (output collapsed)"
    assert card.diff == "--- a\n+++ b"
    assert card.permission_reason == "Needs approval"


def test_python_diagnostics_metadata_reports_backend(tmp_path: Path):
    from opennova.tools.diagnostics_tools import PythonDiagnosticsTool

    source = tmp_path / "ok.py"
    source.write_text("x = 1\n", encoding="utf-8")
    result = PythonDiagnosticsTool(config={"working_dir": str(tmp_path)}).execute(path=str(source))

    assert result.success is True
    assert result.metadata["backend"]["name"] in {"ast", "pyright", "ruff"}
    assert "available" in result.metadata["backend"]


def test_plugin_tool_schema_validation_records_errors(tmp_path: Path):
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "bad"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
name: bad
enabled: true
tools:
  - name: bad_tool
    description: Missing command
    args: ["ok"]
    permission: invalid
""".strip(),
        encoding="utf-8",
    )

    manager = PluginManager(project_path=tmp_path, trust_path=tmp_path / "trust.json")
    manager.trust_plugin("bad")
    manager.load_enabled_plugins(config={})
    tools = manager.build_tools(config={"working_dir": str(tmp_path)})

    assert tools == []
    assert "bad.bad_tool" in manager.errors
    assert "command" in manager.errors["bad.bad_tool"]


def test_plugin_tool_permission_metadata(tmp_path: Path):
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "good"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        """
name: good
enabled: true
tools:
  - name: good_tool
    description: Good tool
    command: python
    args: ["-c", "print('ok')"]
    permission: read
""".strip(),
        encoding="utf-8",
    )

    manager = PluginManager(project_path=tmp_path, trust_path=tmp_path / "trust.json")
    manager.trust_plugin("good")
    manager.load_enabled_plugins(config={})
    tools = manager.build_tools(config={"working_dir": str(tmp_path)})

    assert tools[0].is_read_only() is True
    assert tools[0].permission == "read"


def test_local_automation_monitor_tick_runs_due_tasks(tmp_path: Path):
    from opennova.automation import LocalAutomationMonitor, LocalAutomationScheduler

    now = [100.0]
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: now[0])
    scheduler.schedule_once("docs", "Review docs", run_at=50.0)
    monitor = LocalAutomationMonitor(scheduler)

    events = monitor.tick(lambda task: f"ran {task.name}")

    assert events[0]["type"] == "automation_run"
    assert events[0]["task_name"] == "docs"
    assert events[0]["success"] is True
    assert scheduler.history[-1].output == "ran docs"
