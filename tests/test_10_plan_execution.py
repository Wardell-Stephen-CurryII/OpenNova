from __future__ import annotations

from pathlib import Path

import yaml

from opennova.automation import (
    AutomationArchive,
    LocalAutomationDaemon,
    LocalAutomationScheduler,
    ScheduledTask,
)
from opennova.cli.plugin_commands import handle_plugin_command
from opennova.cli.tool_cards import ToolCardStore, apply_tool_card_key, build_tool_card_panel
from opennova.plugins import PluginManager
from opennova.runtime.events import ToolEvent
from opennova.tools.diagnostics_tools import PythonBackendStatus, PythonExternalAnalyzer
from opennova.transcript import TranscriptExporter, build_checkpoint_index, extract_checkpoint_index


def test_tool_card_keymap_updates_selection_expansion_approval_and_cancel():
    store = ToolCardStore(collapse_threshold=8)
    store.apply_event(ToolEvent(type="tool_start", tool_id="a", tool_name="read_file"))
    store.apply_event(
        ToolEvent(
            type="permission_request",
            tool_id="b",
            tool_name="write_file",
            metadata={"reason": "needs approval"},
        )
    )
    store.apply_event(
        ToolEvent(
            type="tool_result",
            tool_id="b",
            tool_name="write_file",
            success=True,
            output="0123456789abcdef",
            diff="--- old\n+++ new\n",
        )
    )

    assert apply_tool_card_key(store, "j") == "selected:b"
    assert apply_tool_card_key(store, "enter") == "expanded:b"
    assert apply_tool_card_key(store, "a") == "approval:b:approved"
    assert apply_tool_card_key(store, "d") == "approval:b:denied"
    assert apply_tool_card_key(store, "c") == "cancelled:b"

    panel = build_tool_card_panel(store)
    assert panel.selected_tool_id == "b"
    assert panel.cards[1].expanded is True
    assert panel.cards[1].approval_state == "denied"
    assert panel.cards[1].cancelled is True


def test_transcript_checkpoint_index_build_and_extract(tmp_path: Path):
    events = [
        {
            "type": "tool_result",
            "tool_name": "edit_file",
            "tool_id": "tool-1",
            "metadata": {"checkpoint_id": "cp-1"},
            "diff": "--- old\n+++ new\n",
        }
    ]
    index = build_checkpoint_index(events)
    path = TranscriptExporter(tmp_path).export(
        session_id="session",
        messages=[{"role": "user", "content": "edit"}],
        tool_events=events,
    )
    extracted = extract_checkpoint_index(path)

    assert index[0]["checkpoint_id"] == "cp-1"
    assert index[0]["tool_id"] == "tool-1"
    assert extracted[0]["checkpoint_id"] == "cp-1"
    assert extracted[0]["diff"] == "--- old\n+++ new"


def test_plugin_audit_reports_trusted_tool_hook_and_mcp_risks(tmp_path: Path):
    plugin_root = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_root.mkdir(parents=True)
    (plugin_root / "hook.py").write_text("def noop():\n    return None\n", encoding="utf-8")
    (plugin_root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "signature": "local-signature",
                "tools": [
                    {
                        "name": "write_helper",
                        "description": "Writes things",
                        "command": "echo write",
                        "permission": "edit",
                    }
                ],
                "hooks": ["hook.py"],
                "mcp_servers": [{"name": "demo-mcp"}],
            }
        ),
        encoding="utf-8",
    )

    manager = PluginManager(tmp_path)
    manager.trust_plugin("demo")
    manager.load_enabled_plugins({})

    audit = manager.audit_permissions()
    result = handle_plugin_command(manager, "audit")

    assert audit[0]["name"] == "demo"
    assert "tool:write_helper:edit" in audit[0]["risks"]
    assert "hooks:1" in audit[0]["risks"]
    assert "mcp:demo-mcp" in audit[0]["risks"]
    assert audit[0]["signature"] == "local-signature"
    assert result.success is True
    assert "tool:write_helper:edit" in result.output


def test_automation_archive_writes_jsonl_events(tmp_path: Path):
    archive = AutomationArchive(tmp_path / "archive")
    event = {"type": "automation_run", "task_id": "task-1", "success": True}

    path = archive.append_event(event)
    events = archive.read_events()

    assert path.name == "automation-events.jsonl"
    assert events == [event]


def test_daemon_retry_events_can_be_archived(tmp_path: Path):
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: 1000.0)
    scheduler.schedule_once("daily", "summarize", run_at=1000.0)
    daemon = LocalAutomationDaemon(scheduler)
    daemon.start()
    archive = AutomationArchive(tmp_path / "archive")
    calls = 0

    def runner(task: ScheduledTask) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary")
        return "ok"

    daemon.run_with_retry(runner, archive_callback=archive.append_event)

    events = archive.read_events()
    assert events[0]["type"] == "automation_retry"
    assert events[-1]["type"] == "automation_run"


def test_python_analysis_event_wraps_diagnostics_result(tmp_path: Path):
    analyzer = PythonExternalAnalyzer(
        PythonBackendStatus(
            backend="ast",
            pyright_available=False,
            ruff_available=False,
        )
    )

    event = analyzer.event_for_diagnostics(tmp_path)

    assert event.kind == "diagnostics"
    assert event.backend == "ast"
    assert event.path == str(tmp_path)
    assert event.success is True
    assert event.payload["fallback"] is True
