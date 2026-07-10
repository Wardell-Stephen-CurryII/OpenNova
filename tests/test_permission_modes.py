"""Tests for the request, auto, and full approval modes."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from opennova.cli.tui import OpenNovaTUI
from opennova.config import DEFAULT_CONFIG, Config, validate_config
from opennova.main import _load_and_validate_config, main
from opennova.runtime.agent import AgentRuntime
from opennova.runtime.loop import ParsedAction, ReActLoop
from opennova.runtime.state import AgentState
from opennova.security.audit import SecurityAuditLogger
from opennova.security.guardrails import Guardrails, PermissionMode, RiskLevel
from opennova.security.permissions import PermissionStore
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.shell_tools import ExecuteCommandTool


def test_permission_mode_matrix_and_hard_blocks():
    request = Guardrails(permission_mode="request")
    auto = Guardrails(permission_mode="auto")
    full = Guardrails(permission_mode="full")

    request_read = request.check_tool_call("read_file", {"file_path": "README.md"}, ".")
    auto_write = auto.check_tool_call("write_file", {"file_path": "README.md"}, ".")
    auto_delete = auto.check_tool_call("delete_file", {"file_path": "README.md"}, ".")
    auto_warn = auto.check_tool_call("execute_command", {"command": "echo hi | cat"}, ".")
    full_warn = full.check_tool_call("execute_command", {"command": "echo hi | cat"}, ".")
    full_block = full.check_tool_call("execute_command", {"command": "rm -rf /"}, ".")

    assert request_read.risk_level == RiskLevel.SAFE
    assert request_read.requires_confirmation is True
    assert auto_write.requires_confirmation is False
    assert auto_delete.risk_level == RiskLevel.WARN
    assert auto_delete.requires_confirmation is True
    assert auto_warn.requires_confirmation is True
    assert full_warn.requires_confirmation is False
    assert full_warn.metadata["approval_bypassed"] is True
    assert full_block.allowed is False


def test_request_mode_does_not_wrap_plan_or_question_interactions_in_approval():
    guardrails = Guardrails(permission_mode="request")

    for tool_name in ("ask_user_question", "enter_plan_mode", "exit_plan_mode"):
        result = guardrails.check_tool_call(tool_name, {}, ".")
        assert result.allowed is True
        assert result.requires_confirmation is False


@pytest.mark.asyncio
async def test_react_loop_requests_approval_for_safe_action_in_request_mode():
    class ReadTool(BaseTool):
        name = "read_file"
        description = "read"

        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute(self, file_path: str) -> ToolResult:
            self.calls += 1
            return ToolResult(success=True, output="ok")

    approvals = []

    async def approve(metadata):
        approvals.append(metadata)
        question = metadata["questions"][0]["question"]
        return {
            "answers": {question: "Proceed"},
            "all_answers": [{"question": question, "answer": "Proceed"}],
        }

    tool = ReadTool()
    loop = ReActLoop(
        llm=SimpleNamespace(model="test-model"),
        tool_registry=ToolRegistry([tool]),
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(permission_mode="request"),
        working_dir=".",
        interaction_callback=approve,
    )

    result = await loop._act(
        ParsedAction(tool_name="read_file", arguments={"file_path": "README.md"})
    )

    assert result.success is True
    assert tool.calls == 1
    assert len(approvals) == 1


def test_full_mode_keeps_explicit_deny_and_mcp_restrictions():
    denied = Guardrails(permission_mode="full", always_deny_tools=["grep_code"])
    untrusted_mcp = Guardrails(permission_mode="full")

    deny_result = denied.check_tool_call("grep_code", {"directory": "."}, ".")
    mcp_result = untrusted_mcp.check_tool_call(
        "remote_tool",
        {},
        ".",
        tool_context={
            "kind": "mcp",
            "server": "demo",
            "tool": "remote_tool",
            "trusted": False,
            "require_confirmation": True,
        },
    )
    mcp_denied = untrusted_mcp.check_tool_call(
        "remote_tool",
        {},
        ".",
        tool_context={
            "kind": "mcp",
            "server": "demo",
            "tool": "remote_tool",
            "trusted": True,
            "denied_tools": ["remote_tool"],
        },
    )

    assert deny_result.allowed is False
    assert mcp_result.allowed is True
    assert mcp_result.requires_confirmation is False
    assert mcp_denied.allowed is False


def test_legacy_permission_modes_normalize_to_new_modes():
    assert PermissionMode.normalize("default") == PermissionMode.AUTO
    assert PermissionMode.normalize("allowEdits") == PermissionMode.AUTO
    assert PermissionMode.normalize("ask") == PermissionMode.REQUEST
    assert PermissionMode.normalize("bypass") == PermissionMode.FULL
    assert PermissionMode.normalize("readOnly") == PermissionMode.READ_ONLY


def test_runtime_switch_updates_command_tool_and_audit_context():
    command_tool = ExecuteCommandTool(config={"permission_mode": "auto"})
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.guardrails = Guardrails(permission_mode="auto")
    runtime.security_config = {"permission_mode": "auto"}
    runtime.config = {"security": runtime.security_config}
    runtime.tool_registry = ToolRegistry([command_tool])
    runtime.security_audit_logger = SimpleNamespace(permission_mode="auto")
    runtime._emit = lambda *args: None

    result = AgentRuntime.set_permission_mode(runtime, "full")

    assert result == PermissionMode.FULL
    assert runtime.get_permission_mode() == PermissionMode.FULL
    assert command_tool.guardrails.effective_permission_mode == PermissionMode.FULL
    assert runtime.security_audit_logger.permission_mode == "full"
    assert runtime.config["security"]["permission_mode"] == "full"


def test_tui_permissions_command_switches_current_runtime_mode():
    store = PermissionStore()

    class Agent:
        guardrails = Guardrails(permission_mode="auto", permission_store=store)

        def set_permission_mode(self, mode):
            return self.guardrails.set_permission_mode(mode)

        def get_permission_mode(self):
            return self.guardrails.effective_permission_mode

    class Log:
        def __init__(self):
            self.lines = []

        def write(self, value):
            self.lines.append(str(value))

    log = Log()
    app = SimpleNamespace(
        agent=Agent(),
        query_one=lambda selector: log,
        _set_status=lambda value: None,
    )

    asyncio.run(OpenNovaTUI._cmd_permissions(app, "mode request"))

    assert app.agent.get_permission_mode() == PermissionMode.REQUEST
    assert any("Permission mode: request" in line for line in log.lines)


def test_permission_mode_config_validation_and_cli_choice():
    config_data = {
        **DEFAULT_CONFIG,
        "providers": {
            "deepseek": {
                "api_key": "test-key",
                "base_url": "https://example.test",
                "default_model": "test-model",
            }
        },
        "security": {**DEFAULT_CONFIG["security"], "permission_mode": "invalid"},
    }
    assert any("permission_mode" in error for error in validate_config(Config(config_data)))

    result = CliRunner().invoke(main, ["--permission-mode", "invalid"])
    assert result.exit_code == 2
    assert "Invalid value for '--permission-mode'" in result.output


def test_cli_permission_mode_overrides_config_for_current_process():
    config_data = copy.deepcopy(DEFAULT_CONFIG)
    config_data["providers"]["deepseek"]["api_key"] = "test-key"
    config = Config(config_data)

    with patch("opennova.main.load_config", return_value=config):
        loaded = _load_and_validate_config(permission_mode="full")

    assert loaded.get("security.permission_mode") == "full"


def test_security_audit_records_permission_decision_metadata(tmp_path):
    path = tmp_path / "security.jsonl"
    logger = SecurityAuditLogger(path=path)
    logger.permission_mode = "full"
    result = Guardrails(permission_mode="full").check_tool_call(
        "execute_command",
        {"command": "echo hi | cat"},
        ".",
    )

    logger.log_tool_event(
        tool_name="execute_command",
        arguments={"command": "echo hi | cat"},
        guard_result=result,
    )

    event = json.loads(path.read_text(encoding="utf-8").strip())
    assert event["permission_mode"] == "full"
    assert event["guard"]["approval_required"] is False
    assert event["guard"]["approval_bypassed"] is True
