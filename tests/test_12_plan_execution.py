from __future__ import annotations

from pathlib import Path

import yaml

from opennova.automation import (
    AutomationArchive,
    LocalAutomationDaemon,
    LocalAutomationScheduler,
    daemon_status,
)
from opennova.cli.tool_cards import ToolCardStore, build_tool_card_binding_plan
from opennova.plugins import PluginManager, PluginPolicy
from opennova.runtime.events import ToolEvent
from opennova.tools.diagnostics_tools import PythonAnalysisServerManager
from opennova.transcript import TranscriptExporter, resolve_checkpoint_diff_from_session


def test_tool_card_binding_plan_marks_enabled_actions():
    store = ToolCardStore(collapse_threshold=8)
    store.apply_event(ToolEvent(type="tool_start", tool_id="a", tool_name="read_file"))
    store.apply_event(
        ToolEvent(
            type="permission_request",
            tool_id="b",
            tool_name="write_file",
            metadata={"reason": "approval needed"},
        )
    )
    store.select_next()

    plan = build_tool_card_binding_plan(store)
    by_action = {item["action"]: item for item in plan}

    assert by_action["select_next"]["enabled"] is True
    assert by_action["select_previous"]["enabled"] is True
    assert by_action["approve"]["enabled"] is True
    assert by_action["deny"]["enabled"] is True
    assert by_action["cancel"]["enabled"] is True


def test_resolve_checkpoint_diff_from_session(tmp_path: Path):
    TranscriptExporter(tmp_path).export(
        session_id="session-1",
        messages=[],
        tool_events=[
            {
                "type": "tool_result",
                "tool_name": "write_file",
                "tool_id": "tool-1",
                "metadata": {"checkpoint_id": "cp-1"},
                "diff": "--- old\n+++ new\n",
            }
        ],
    )

    assert resolve_checkpoint_diff_from_session(tmp_path, "session-1", "cp-1") == "--- old\n+++ new"
    assert resolve_checkpoint_diff_from_session(tmp_path, "session-1", "missing") == ""


def test_plugin_startup_warnings_include_drift_and_policy(tmp_path: Path):
    plugin_root = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_root.mkdir(parents=True)
    (plugin_root / "hook.py").write_text("def noop():\n    return None\n", encoding="utf-8")
    manifest = {
        "name": "demo",
        "tools": [{"name": "reader", "description": "Read", "command": "echo read", "permission": "read"}],
        "hooks": ["hook.py"],
    }
    (plugin_root / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    manager = PluginManager(tmp_path)
    manager.trust_plugin("demo")
    manager.load_enabled_plugins({})
    lockfile = manager.build_lockfile()
    manifest["tools"][0]["permission"] = "edit"
    (plugin_root / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    manager.load_enabled_plugins({})

    warnings = manager.startup_warnings(lockfile=lockfile, policy=PluginPolicy.strict())

    assert any(item["type"] == "drift" for item in warnings)
    assert any(item["type"] == "policy" and "missing-signature" in item["message"] for item in warnings)


def test_daemon_status_includes_archive_summary(tmp_path: Path):
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: 1000.0)
    daemon = LocalAutomationDaemon(scheduler)
    daemon.start()
    archive = AutomationArchive(tmp_path / "archive")
    archive.append_event({"type": "automation_run", "success": True})

    status = daemon_status(daemon, archive=archive)

    assert status["running"] is True
    assert status["last_events_count"] == 0
    assert status["archive"]["total"] == 1


def test_python_analysis_server_manager_lifecycle_and_events(tmp_path: Path):
    manager = PythonAnalysisServerManager(backend="ast")

    assert manager.status()["running"] is False
    manager.start()
    diagnostics = manager.event_for("diagnostics", tmp_path)
    hover = manager.event_for("hover", tmp_path, symbol="Thing")
    manager.stop()

    assert manager.status()["running"] is False
    assert diagnostics.kind == "diagnostics"
    assert hover.kind == "hover"
    assert hover.payload["symbol"] == "Thing"
