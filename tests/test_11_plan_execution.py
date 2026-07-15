from __future__ import annotations

from pathlib import Path

import yaml

from opennova.automation import AutomationArchive, compute_retry_delay
from opennova.cli.checkpoint_commands import handle_checkpoint_command
from opennova.cli.plugin_commands import handle_plugin_command
from opennova.cli.tool_cards import tool_card_key_bindings
from opennova.plugins import PluginManager, PluginPolicy
from opennova.tools.diagnostics_tools import PythonBackendStatus, PythonExternalAnalyzer
from opennova.transcript import TranscriptExporter


def test_tool_card_key_bindings_describe_supported_actions():
    bindings = tool_card_key_bindings()
    by_key = {binding["key"]: binding for binding in bindings}

    assert by_key["j"]["action"] == "select_next"
    assert by_key["k"]["action"] == "select_previous"
    assert by_key["enter"]["action"] == "toggle_expanded"
    assert by_key["a"]["action"] == "approve"
    assert by_key["d"]["action"] == "deny"
    assert by_key["c"]["action"] == "cancel"


def test_checkpoint_diff_from_transcript_returns_matching_diff(tmp_path: Path):
    transcript = TranscriptExporter(tmp_path).export(
        session_id="session",
        messages=[],
        tool_events=[
            {
                "type": "tool_result",
                "tool_name": "edit_file",
                "tool_id": "tool-1",
                "metadata": {"checkpoint_id": "cp-1"},
                "diff": "--- old\n+++ new\n",
            }
        ],
    )

    result = handle_checkpoint_command(tmp_path, f"diff --from-transcript {transcript} cp-1")

    assert result.success is True
    assert result.output == "--- old\n+++ new"


def test_checkpoint_diff_from_transcript_reports_missing_id(tmp_path: Path):
    transcript = TranscriptExporter(tmp_path).export(
        session_id="session", messages=[], tool_events=[]
    )

    result = handle_checkpoint_command(tmp_path, f"diff --from-transcript {transcript} missing")

    assert result.success is False
    assert "Checkpoint not found in transcript" in result.error


def test_plugin_policy_audit_flags_signature_hooks_and_mcp(tmp_path: Path):
    plugin_root = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_root.mkdir(parents=True)
    (plugin_root / "hook.py").write_text("def noop():\n    return None\n", encoding="utf-8")
    (plugin_root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "hooks": ["hook.py"],
                "mcp_servers": [{"name": "demo-mcp"}],
            }
        ),
        encoding="utf-8",
    )

    manager = PluginManager(tmp_path, trust_path=tmp_path / "trust.json")
    manager.trust_plugin("demo")
    manager.load_enabled_plugins({})
    violations = manager.audit_policy(PluginPolicy.strict())
    result = handle_plugin_command(manager, "audit --policy strict")

    assert "missing-signature" in violations[0]["violations"]
    assert "hooks-disallowed" in violations[0]["violations"]
    assert "mcp-disallowed" in violations[0]["violations"]
    assert result.success is True
    assert "missing-signature" in result.output


def test_retry_backoff_and_archive_summary(tmp_path: Path):
    archive = AutomationArchive(tmp_path)
    archive.append_event({"type": "automation_retry", "success": False})
    archive.append_event({"type": "automation_run", "success": True, "task_id": "task-1"})

    assert compute_retry_delay(attempt=0, base_seconds=2, max_seconds=30) == 2
    assert compute_retry_delay(attempt=3, base_seconds=2, max_seconds=10) == 10
    assert archive.summary()["total"] == 2
    assert archive.summary()["failed"] == 1
    assert archive.summary()["last_event"]["task_id"] == "task-1"


def test_python_analysis_hover_definition_and_references_events(tmp_path: Path):
    analyzer = PythonExternalAnalyzer(
        PythonBackendStatus(
            backend="ast",
            pyright_available=False,
            ruff_available=False,
        )
    )

    hover = analyzer.event_for_hover(tmp_path, symbol="Thing")
    definition = analyzer.event_for_definition(tmp_path, symbol="Thing")
    references = analyzer.event_for_references(tmp_path, symbol="Thing")

    assert hover.kind == "hover"
    assert definition.kind == "definition"
    assert references.kind == "references"
    assert hover.payload["symbol"] == "Thing"
    assert references.success is True
