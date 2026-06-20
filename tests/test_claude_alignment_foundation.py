"""Regression tests for Claude Code alignment foundation work."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import pytest

from opennova.providers.base import Message
from opennova.runtime.agent import AgentRuntime
from opennova.security.guardrails import Guardrails, PermissionMode, RiskLevel
from opennova.session import SessionManager
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.file_tools import EditFileTool, MultiEditFileTool, WriteFileTool
from opennova.tools.search_tools import GlobFilesTool, GrepCodeTool
from opennova.tools.shell_tools import ExecuteCommandTool


class SchemaMode(StrEnum):
    QUICK = "quick"
    THOROUGH = "thorough"


class ComplexSchemaTool(BaseTool):
    name = "complex_schema"
    description = "tool with complex typed arguments"

    def execute(
        self,
        names: list[str],
        metadata: dict[str, Any],
        mode: Literal["fast", "safe"] = "safe",
        enum_mode: SchemaMode = SchemaMode.QUICK,
        limit: int | None = None,
    ) -> ToolResult:
        return ToolResult(success=True, output="ok")


class DummyTool(BaseTool):
    name = "dummy"
    description = "dummy"

    def execute(self) -> ToolResult:
        return ToolResult(success=True, output="ok")


def test_tool_registry_instances_do_not_share_tools():
    first = ToolRegistry()
    second = ToolRegistry()

    first.register(DummyTool())

    assert first.has_tool("dummy")
    assert not second.has_tool("dummy")


def test_child_runtime_has_isolated_mutable_state_and_security_policy():
    runtime = AgentRuntime(
        {
            "default_provider": "deepseek",
            "providers": {"deepseek": {"api_key": "test-key", "default_model": "deepseek-v4-pro"}},
            "agent": {"max_iterations": 3},
            "security": {"permission_mode": "readOnly", "allow_network": False},
            "mcp": {"enabled": False, "servers": []},
            "skills": {"enabled": False, "dirs": []},
        },
        enable_mcp=False,
        enable_skills=False,
    )
    runtime.register_tool(DummyTool())

    child = runtime.create_child_runtime()
    assert not child.tool_registry.has_tool("dummy")
    child.register_tool(DummyTool())

    assert child is not runtime
    assert child.tool_registry is not runtime.tool_registry
    assert child.working_memory is not runtime.working_memory
    assert child.session_manager is not runtime.session_manager
    assert child.session_manager.session_id != runtime.session_manager.session_id
    assert child.guardrails is not runtime.guardrails
    assert child.guardrails.permission_mode == PermissionMode.READ_ONLY
    assert child.guardrails.allow_network is False


def test_complex_tool_schema_preserves_json_schema_types():
    schema = ComplexSchemaTool().get_schema().parameters["properties"]

    assert schema["names"]["type"] == "array"
    assert schema["names"]["items"]["type"] == "string"
    assert schema["metadata"]["type"] == "object"
    assert schema["mode"]["enum"] == ["fast", "safe"]
    assert schema["enum_mode"]["enum"] == ["quick", "thorough"]
    assert schema["limit"]["type"] == "integer"


def test_execute_command_rejects_string_timeout_without_type_coercion():
    result = ExecuteCommandTool(config={"working_dir": "."}).execute("date", timeout="5")

    assert result.success is False
    assert "timeout must be a number" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_command_sync_async_share_working_dir_validation(tmp_path: Path):
    missing_dir = tmp_path / "missing"
    tool = ExecuteCommandTool(config={"working_dir": str(tmp_path)})

    sync_result = tool.execute("echo hi", working_dir=str(missing_dir))
    async_result = await tool.execute_async("echo hi", working_dir=str(missing_dir))

    assert sync_result.success is False
    assert async_result.success is False
    assert sync_result.error == async_result.error


def test_session_load_with_summary_restores_latest_compression_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    manager = SessionManager(project_path=str(tmp_path / "project"))
    session_id = manager.start_session()

    manager.save_message(Message(role="user", content="before"))
    manager.save_message(Message(role="assistant", content="before answer"))
    manager.save_compression_marker("compressed summary", message_count=2)
    manager.save_message(Message(role="user", content="after"))

    loaded = manager.load_session_with_summary(session_id)

    assert loaded.session_id == session_id
    assert loaded.compression_summary == "compressed summary"
    assert [message.content for message in loaded.messages] == ["after"]


def test_edit_file_exact_replacement_and_multi_edit(tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    config = {"working_dir": str(tmp_path)}

    edit_result = EditFileTool(config=config).execute(
        str(target),
        old_text="beta",
        new_text="BETA",
    )
    multi_result = MultiEditFileTool(config=config).execute(
        str(target),
        edits=[
            {"old_text": "alpha", "new_text": "ALPHA"},
            {"old_text": "gamma", "new_text": "GAMMA"},
        ],
    )

    assert edit_result.success is True
    assert multi_result.success is True
    assert target.read_text(encoding="utf-8") == "ALPHA\nBETA\nGAMMA\n"
    assert "diff" in edit_result.metadata
    assert "diff" in multi_result.metadata


def test_edit_file_requires_unique_match_unless_replace_all(tmp_path: Path):
    target = tmp_path / "dupe.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    tool = EditFileTool(config={"working_dir": str(tmp_path)})

    result = tool.execute(str(target), old_text="same", new_text="other")

    assert result.success is False
    assert "appears 2 times" in (result.error or "")


def test_file_tools_share_read_only_sandbox_error(tmp_path: Path):
    target = tmp_path / "readonly.txt"

    result = WriteFileTool(config={"working_dir": str(tmp_path), "read_only": True}).execute(
        str(target),
        "content",
    )

    assert result.success is False
    assert "read-only" in (result.error or "").lower()


def test_glob_and_grep_tools_respect_gitignore_and_max_results(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("needle one\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle two\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("needle ignored\n", encoding="utf-8")
    config = {"working_dir": str(tmp_path)}

    glob_result = GlobFilesTool(config=config).execute("*.py", directory=str(tmp_path), max_results=1)
    grep_result = GrepCodeTool(config=config).execute(
        "needle",
        directory=str(tmp_path),
        file_glob="*.py",
        max_results=10,
    )

    assert glob_result.success is True
    assert glob_result.metadata["count"] == 1
    assert grep_result.success is True
    assert "ignored.py" not in grep_result.output
    assert grep_result.metadata["count"] == 2


def test_grep_code_returns_error_for_invalid_regex(tmp_path: Path):
    result = GrepCodeTool(config={"working_dir": str(tmp_path)}).execute(
        "[",
        directory=str(tmp_path),
        regex=True,
    )

    assert result.success is False
    assert "invalid regex" in (result.error or "").lower()


def test_guardrails_permission_modes_and_tool_rules():
    read_only = Guardrails(permission_mode=PermissionMode.READ_ONLY)
    ask = Guardrails(permission_mode=PermissionMode.ASK)
    allow_edits = Guardrails(permission_mode=PermissionMode.ALLOW_EDITS)
    allow = Guardrails(always_allow_tools=["write_file"])
    deny = Guardrails(always_deny_tools=["grep_code"])

    read_result = read_only.check_tool_call("read_file", {"file_path": "README.md"}, ".")
    edit_result = read_only.check_tool_call("edit_file", {"file_path": "README.md"}, ".")
    ask_result = ask.check_tool_call("write_file", {"file_path": "README.md"}, ".")
    ask_block_result = ask.check_tool_call("execute_command", {"command": "rm -rf /"}, ".")
    allow_result = allow.check_tool_call("write_file", {"file_path": "README.md"}, ".")
    command_result = allow_edits.check_tool_call("execute_command", {"command": "echo hi"}, ".")
    deny_result = deny.check_tool_call("grep_code", {"directory": "."}, ".")

    assert read_result.risk_level == RiskLevel.SAFE
    assert edit_result.allowed is False
    assert ask_result.requires_confirmation is True
    assert ask_block_result.allowed is False
    assert allow_result.requires_confirmation is False
    assert command_result.requires_confirmation is True
    assert deny_result.allowed is False
