from __future__ import annotations

from pathlib import Path

import yaml

from opennova.automation import LocalAutomationDaemon, LocalAutomationScheduler, ScheduledTask
from opennova.cli.plugin_commands import handle_plugin_command
from opennova.cli.tool_cards import ToolCardStore, build_tool_card_panel
from opennova.plugins import PluginManager
from opennova.runtime.events import ToolEvent
from opennova.transcript import TranscriptExporter


def test_tool_card_store_tracks_selection_expansion_and_approval_state():
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

    assert store.interaction.selected_tool_id == "a"
    store.select_next()
    store.toggle_expanded("b")
    store.apply_approval("b", "approved")

    panel = build_tool_card_panel(store)

    assert panel.selected_tool_id == "b"
    assert panel.cards[1].expanded is True
    assert panel.cards[1].approval_state == "approved"
    assert panel.diff_panel == "--- old\n+++ new"


def test_transcript_export_includes_checkpoint_diff_duration_and_error(tmp_path: Path):
    path = TranscriptExporter(tmp_path).export(
        session_id="session-1",
        messages=[{"role": "user", "content": "change file"}],
        tool_events=[
            {
                "type": "tool_result",
                "tool_name": "write_file",
                "tool_id": "tool-1",
                "duration_ms": 12,
                "metadata": {"checkpoint_id": "abc123"},
                "diff": "--- old\n+++ new\n",
            },
            {
                "type": "tool_error",
                "tool_name": "shell",
                "tool_id": "tool-2",
                "error": "boom",
            },
        ],
    )

    output = path.read_text(encoding="utf-8")

    assert "checkpoint_id: `abc123`" in output
    assert "duration_ms: `12`" in output
    assert "error: boom" in output
    assert "```diff\n--- old\n+++ new\n```" in output


def test_plugin_command_trust_and_untrust_use_shared_handler(tmp_path: Path):
    plugin_root = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.yaml").write_text(
        yaml.safe_dump({"name": "demo", "description": "Demo"}),
        encoding="utf-8",
    )

    manager = PluginManager(tmp_path, trust_path=tmp_path / "trust.json")
    manager.load_enabled_plugins({})

    trusted = handle_plugin_command(manager, "trust demo")
    untrusted = handle_plugin_command(manager, "untrust demo")

    assert trusted.success is True
    assert "Trusted plugin: demo" in trusted.output
    assert untrusted.success is True
    assert "Untrusted plugin: demo" in untrusted.output
    assert manager.is_trusted("demo") is False


def test_automation_daemon_retry_and_archive_callback(tmp_path: Path):
    now = 1000.0
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: now)
    task_id = scheduler.schedule_once("daily", "summarize", run_at=now)
    daemon = LocalAutomationDaemon(scheduler)
    daemon.start()
    calls: list[str] = []
    archived: list[dict[str, object]] = []

    def runner(task: ScheduledTask) -> str:
        calls.append(task.id)
        if len(calls) == 1:
            raise RuntimeError("temporary")
        return "ok"

    events = daemon.run_with_retry(
        runner,
        max_retries=1,
        archive_callback=lambda event: archived.append(event),
    )

    assert calls == [task_id, task_id]
    assert events[0]["type"] == "automation_retry"
    assert events[-1]["type"] == "automation_run"
    assert events[-1]["success"] is True
    assert archived == events


def test_automation_daemon_retry_does_not_run_when_stopped(tmp_path: Path):
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: 1000.0)
    scheduler.schedule_once("daily", "summarize", run_at=1000.0)
    daemon = LocalAutomationDaemon(scheduler)

    assert daemon.run_with_retry(lambda task: "ok") == []
