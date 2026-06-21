from __future__ import annotations

from pathlib import Path

import yaml

from opennova.automation import (
    LocalAutomationDaemon,
    LocalAutomationScheduler,
    ScheduledTask,
)
from opennova.checkpoints import CheckpointManager
from opennova.cli.checkpoint_commands import handle_checkpoint_command
from opennova.cli.tool_cards import ToolCardStore, render_tool_card, render_tool_cards
from opennova.plugins import PluginManager
from opennova.runtime.events import ToolEvent
from opennova.tools.diagnostics_tools import get_python_backend_status


def test_checkpoint_restore_preview_outputs_diff_without_restoring(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "app.py"
    target.write_text("print('old')\n", encoding="utf-8")

    manager = CheckpointManager(project)
    checkpoint_id = manager.create("before edit", [target])
    target.write_text("print('new')\n", encoding="utf-8")

    result = handle_checkpoint_command(project, f"restore --preview {checkpoint_id[:8]}")

    assert result.success is True
    assert "--- checkpoint/app.py" in result.output
    assert "+++ app.py" in result.output
    assert "-print('old')" in result.output
    assert "+print('new')" in result.output
    assert target.read_text(encoding="utf-8") == "print('new')\n"


def test_tool_card_renderer_shows_status_diff_permission_and_collapsed_output():
    store = ToolCardStore(collapse_threshold=12)
    store.apply_event(
        ToolEvent(
            type="tool_start",
            tool_id="tool-1",
            tool_name="write_file",
        )
    )
    store.apply_event(
        ToolEvent(
            type="permission_request",
            tool_id="tool-1",
            tool_name="write_file",
            metadata={"reason": "writes protected file"},
        )
    )
    card = store.apply_event(
        ToolEvent(
            type="tool_result",
            tool_id="tool-1",
            tool_name="write_file",
            success=True,
            output="0123456789abcdef",
            diff="--- a\n+++ b\n",
            duration_ms=42,
        )
    )

    rendered = render_tool_card(card)
    rendered_all = render_tool_cards(store)

    assert "[succeeded] write_file (tool-1)" in rendered
    assert "duration=42ms" in rendered
    assert "0123456789ab" in rendered
    assert "... (output collapsed)" in rendered
    assert "--- a\n+++ b" in rendered
    assert rendered in rendered_all

    cancelled = store.cancel("tool-1")
    assert "cancelled=yes" in render_tool_card(cancelled)


def test_python_backend_status_exposes_optional_backend_flags():
    status = get_python_backend_status()

    assert status.backend in {"ast", "pyright", "ruff"}
    assert isinstance(status.pyright_available, bool)
    assert isinstance(status.ruff_available, bool)
    assert status.fallback == "ast"
    assert status.to_dict()["backend"] == status.backend


def test_plugin_lockfile_and_test_plugin_report_permissions(tmp_path: Path):
    plugin_root = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "description": "Demo plugin",
                "tools": [
                    {
                        "name": "demo_read",
                        "description": "Read only helper",
                        "command": "echo demo",
                        "permission": "read",
                    }
                ],
                "hooks": ["hook.py"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_root / "hook.py").write_text("def noop():\n    return None\n", encoding="utf-8")

    manager = PluginManager(tmp_path)
    manager.trust_plugin("demo")
    manager.load_enabled_plugins({})

    report = manager.test_plugin("demo")
    lockfile = manager.build_lockfile()

    assert report.success is True
    assert report.errors == []
    assert lockfile["plugins"][0]["name"] == "demo"
    assert lockfile["plugins"][0]["trusted"] is True
    assert lockfile["plugins"][0]["tools"][0]["permission"] == "read"
    assert lockfile["plugins"][0]["hooks"] == ["hook.py"]


def test_plugin_test_reports_bad_tool_manifest(tmp_path: Path):
    plugin_root = tmp_path / ".opennova" / "plugins" / "bad"
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "bad",
                "tools": [
                    {
                        "name": "broken",
                        "description": "Broken helper",
                        "command": "echo bad",
                        "permission": "admin",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = PluginManager(tmp_path)
    manager.trust_plugin("bad")
    manager.load_enabled_plugins({})

    report = manager.test_plugin("bad")

    assert report.success is False
    assert "permission" in report.errors[0]


def test_local_automation_daemon_run_once_tracks_state_and_events(tmp_path: Path):
    now = 1000.0
    scheduler = LocalAutomationScheduler(tmp_path / "automation.json", clock=lambda: now)
    task_id = scheduler.schedule_once("daily", "summarize", run_at=now)
    daemon = LocalAutomationDaemon(scheduler)
    calls: list[str] = []

    def runner(task: ScheduledTask) -> str:
        calls.append(task.id)
        return f"ran {task.name}"

    assert daemon.running is False
    assert daemon.run_once(runner) == []

    daemon.start()
    events = daemon.run_once(runner)

    assert daemon.running is True
    assert calls == [task_id]
    assert events[0]["type"] == "automation_run"
    assert daemon.last_events == events

    daemon.stop()
    assert daemon.running is False
    assert daemon.run_once(runner) == []
