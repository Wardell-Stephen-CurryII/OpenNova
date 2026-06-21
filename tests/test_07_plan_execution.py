from __future__ import annotations

from pathlib import Path

import yaml

from opennova.automation import LocalAutomationDaemon, LocalAutomationScheduler, ScheduledTask
from opennova.checkpoints import CheckpointManager
from opennova.cli.automation_commands import handle_automation_command
from opennova.cli.plugin_commands import handle_plugin_command
from opennova.cli.tool_cards import ToolCardStore, build_tool_card_view
from opennova.plugins import PluginManager
from opennova.runtime.events import ToolEvent
from opennova.tools.diagnostics_tools import PythonBackendStatus, PythonExternalAnalyzer
from opennova.tools.file_tools import WriteFileTool


def test_tool_card_view_state_supports_expand_diff_permission_and_cancel():
    store = ToolCardStore(collapse_threshold=8)
    store.apply_event(ToolEvent(type="tool_start", tool_id="t1", tool_name="write_file"))
    store.apply_event(
        ToolEvent(
            type="permission_request",
            tool_id="t1",
            tool_name="write_file",
            metadata={"reason": "needs write approval"},
        )
    )
    card = store.apply_event(
        ToolEvent(
            type="tool_result",
            tool_id="t1",
            tool_name="write_file",
            success=True,
            output="0123456789abcdef",
            diff="--- old\n+++ new\n",
            duration_ms=15,
        )
    )

    collapsed = build_tool_card_view(card, expanded=False)
    expanded = build_tool_card_view(card, expanded=True)
    cancelled = build_tool_card_view(store.cancel("t1"), expanded=True)

    assert collapsed.expanded is False
    assert collapsed.diff_panel == "--- old\n+++ new"
    assert collapsed.approval_state == "requested"
    assert "01234567" in collapsed.rendered
    assert "0123456789abcdef" not in collapsed.rendered
    assert expanded.expanded is True
    assert "0123456789abcdef" in expanded.rendered
    assert cancelled.cancelled is True
    assert "cancelled=yes" in cancelled.rendered


def test_write_file_creates_checkpoint_before_overwriting_existing_file(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    tool = WriteFileTool({"working_dir": str(tmp_path), "checkpoint_writes": True})

    result = tool.execute(str(target), "new\n")

    assert result.success is True
    checkpoint_id = result.metadata["checkpoint_id"]
    assert result.metadata["change_type"] == "modify"
    assert result.metadata["diff"]
    assert target.read_text(encoding="utf-8") == "new\n"

    CheckpointManager(tmp_path).restore(checkpoint_id)
    assert target.read_text(encoding="utf-8") == "old\n"


def test_write_file_does_not_checkpoint_new_files(tmp_path: Path):
    target = tmp_path / "new.py"
    tool = WriteFileTool({"working_dir": str(tmp_path), "checkpoint_writes": True})

    result = tool.execute(str(target), "print('new')\n")

    assert result.success is True
    assert "checkpoint_id" not in result.metadata
    assert target.read_text(encoding="utf-8") == "print('new')\n"


def test_python_external_analyzer_plans_backend_commands():
    project = Path("/tmp/project")
    pyright = PythonExternalAnalyzer(
        PythonBackendStatus(
            backend="pyright",
            pyright_available=True,
            ruff_available=False,
        )
    )
    ruff = PythonExternalAnalyzer(
        PythonBackendStatus(
            backend="ruff",
            pyright_available=False,
            ruff_available=True,
        )
    )
    ast = PythonExternalAnalyzer(
        PythonBackendStatus(
            backend="ast",
            pyright_available=False,
            ruff_available=False,
        )
    )

    assert pyright.plan_diagnostics(project).argv == ["pyright", str(project), "--outputjson"]
    assert ruff.plan_diagnostics(project).argv == ["ruff", "check", str(project), "--output-format=json"]
    ast_plan = ast.plan_diagnostics(project)
    assert ast_plan.argv == []
    assert ast_plan.fallback_reason == "No external Python analyzer available; using AST fallback"


def test_plugin_lockfile_drift_and_plugin_test_command(tmp_path: Path):
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
    lockfile = manager.build_lockfile()

    assert manager.compare_lockfile(lockfile)["changed"] == []
    manifest["tools"][0]["permission"] = "edit"
    (plugin_root / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    manager.load_enabled_plugins({})

    drift = manager.compare_lockfile(lockfile)
    report = handle_plugin_command(manager, "test demo")

    assert drift["changed"][0]["name"] == "demo"
    assert "permission" in drift["changed"][0]["changes"][0]
    assert report.success is True
    assert "Plugin demo passed validation" in report.output


def test_automation_command_controls_daemon_tick_and_status(tmp_path: Path):
    now = 1000.0
    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: now)
    task_id = scheduler.schedule_once("daily", "summarize", run_at=now)
    daemon = LocalAutomationDaemon(scheduler)
    calls: list[str] = []

    def runner(task: ScheduledTask) -> str:
        calls.append(task.id)
        return f"ran {task.name}"

    started = handle_automation_command(scheduler, "daemon start", runner=runner, daemon=daemon)
    status = handle_automation_command(scheduler, "daemon status", runner=runner, daemon=daemon)
    ticked = handle_automation_command(scheduler, "daemon tick", runner=runner, daemon=daemon)
    stopped = handle_automation_command(scheduler, "daemon stop", runner=runner, daemon=daemon)

    assert started.success is True
    assert "running=True" in status.output
    assert calls == [task_id]
    assert "automation_run" in ticked.output
    assert stopped.success is True
    assert daemon.running is False
