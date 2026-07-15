from pathlib import Path

from opennova.automation import (
    AutomationArchive,
    LocalAutomationDaemon,
    LocalAutomationScheduler,
)
from opennova.cli.automation_commands import handle_automation_command
from opennova.cli.checkpoint_commands import handle_checkpoint_command
from opennova.cli.plugin_commands import handle_plugin_command
from opennova.cli.tool_cards import (
    ToolCardStore,
    build_tool_card_binding_plan,
    render_tool_card_binding_help,
)
from opennova.plugins import PluginManager
from opennova.runtime.events import ToolEvent
from opennova.tools.diagnostics_tools import PythonAnalysisServerManager
from opennova.transcript import TranscriptExporter


def test_tool_card_binding_help_includes_disabled_reasons():
    store = ToolCardStore(collapse_threshold=8)
    store.apply_event(
        ToolEvent(
            type="tool_start",
            tool_id="tool-1",
            tool_name="read_file",
        )
    )

    plan = build_tool_card_binding_plan(store)
    approve = next(item for item in plan if item["action"] == "approve")
    toggle = next(item for item in plan if item["action"] == "toggle_expanded")
    help_text = render_tool_card_binding_help(store)

    assert approve["enabled"] is False
    assert approve["disabled_reason"] == "selected tool has no pending permission request"
    assert toggle["disabled_reason"] == "selected tool output is not collapsible"
    assert "a approve disabled: selected tool has no pending permission request" in help_text
    assert "j select_next enabled" in help_text


def test_checkpoint_diff_from_session_command_uses_project_exports(tmp_path: Path):
    export_dir = tmp_path / ".opennova" / "exports"
    exporter = TranscriptExporter(export_dir)
    exporter.export(
        "session-1",
        messages=[],
        tool_events=[
            {
                "tool_id": "tool-1",
                "tool_name": "write_file",
                "metadata": {"checkpoint_id": "cp-123"},
                "diff": "--- old\n+++ new\n@@\n-before\n+after\n",
            }
        ],
    )

    result = handle_checkpoint_command(tmp_path, "diff --session session-1 cp-123")
    missing = handle_checkpoint_command(tmp_path, "diff --session session-1 missing")

    assert result.success is True
    assert "-before" in result.output
    assert missing.success is False
    assert "Checkpoint not found in session transcript" in missing.error


def test_plugin_warnings_command_reports_drift_and_strict_policy(tmp_path: Path):
    plugin_dir = tmp_path / ".opennova" / "plugins" / "sample"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.yaml"
    manifest.write_text(
        """
name: sample
description: Sample plugin.
enabled: true
hooks:
  - hooks.py
""".strip(),
        encoding="utf-8",
    )
    manager = PluginManager(tmp_path, trust_path=tmp_path / "trust.json")
    manager.load_enabled_plugins(config={})
    handle_plugin_command(manager, "lock")

    manifest.write_text(
        """
name: sample
description: Sample plugin changed.
enabled: true
hooks:
  - hooks.py
""".strip(),
        encoding="utf-8",
    )
    manager.load_enabled_plugins(config={})

    result = handle_plugin_command(manager, "warnings --policy strict")

    assert result.success is True
    assert "drift: sample" in result.output
    assert "policy: sample missing-signature,hooks-disallowed" in result.output
    assert result.metadata["policy"] == "strict"


def test_automation_daemon_status_includes_archive_summary(tmp_path: Path):
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json")
    daemon = LocalAutomationDaemon(scheduler)
    daemon.start()
    archive = AutomationArchive(tmp_path / "archive.jsonl")
    archive.append_event({"task_id": "task-1", "success": True})

    result = handle_automation_command(
        scheduler,
        "daemon status",
        daemon=daemon,
        archive=archive,
    )

    assert result.success is True
    assert "archive_total=1" in result.output
    assert result.metadata["archive"]["total_events"] == 1


def test_python_analysis_server_manager_tracks_subprocess_lifecycle(tmp_path: Path):
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> dict[str, object]:
        calls.append(argv)
        return {"pid": 4321, "returncode": None}

    manager = PythonAnalysisServerManager(backend="pyright")
    manager.start(runner=runner)
    status = manager.status()
    event = manager.event_for("hover", tmp_path / "app.py", symbol="App")

    assert calls == [["pyright-langserver", "--stdio"]]
    assert status["running"] is True
    assert status["argv"] == ["pyright-langserver", "--stdio"]
    assert status["process"]["pid"] == 4321
    assert event.payload["server_running"] is True
    assert event.payload["server_argv"] == ["pyright-langserver", "--stdio"]

    manager.stop()
    assert manager.status()["running"] is False
