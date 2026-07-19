"""Focused tests for sandbox/guardrails hardening behaviors."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from opennova.runtime.loop import ParsedAction, ReActLoop
from opennova.runtime.state import AgentState
from opennova.security.audit import SecurityAuditLogger
from opennova.security.guardrails import Guardrails, RiskLevel
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.file_tools import ReadFileTool, WriteFileTool
from opennova.tools.shell_tools import ExecuteCommandTool
from opennova.tools.web_tools import WebFetchTool


class DummyProvider:
    model = "dummy"

    async def chat(self, messages, tools=None, **kwargs):
        raise NotImplementedError

    async def stream_chat(self, messages, tools=None, **kwargs):
        if False:
            yield None


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_guardrails_blocks_network_command_when_disabled():
    guard = Guardrails(allow_network=False)
    result = guard.check_command("curl https://example.com")
    assert result.allowed is False
    assert result.risk_level == RiskLevel.BLOCK


def test_guardrails_warns_on_shell_features():
    guard = Guardrails()
    result = guard.check_command("echo hello | sed 's/hello/hi/'")
    assert result.allowed is True
    assert result.risk_level == RiskLevel.WARN
    assert result.requires_confirmation is True
    assert result.metadata["command_analysis"]["uses_shell_features"] is True


def test_parameter_permission_rule_allows_scoped_write_without_confirmation(tmp_path: Path):
    guard = Guardrails(
        permission_rules=[
            {
                "id": "allow-src-writes",
                "tool": "write_file",
                "decision": "allow",
                "path_globs": ["src/**"],
                "reason": "Project source writes are allowed",
            }
        ]
    )
    target = tmp_path / "src" / "module.py"

    result = guard.check_tool_call(
        "write_file",
        {"file_path": str(target)},
        working_dir=str(tmp_path),
    )

    assert result.allowed is True
    assert result.risk_level == RiskLevel.SAFE
    assert result.requires_confirmation is False
    assert result.metadata["rule_id"] == "allow-src-writes"


def test_parameter_permission_rule_denies_matching_path(tmp_path: Path):
    guard = Guardrails(
        permission_rules=[
            {
                "id": "deny-secrets",
                "tool": "write_file",
                "decision": "deny",
                "path_globs": ["secrets/**"],
                "reason": "Secrets are protected",
            }
        ]
    )

    result = guard.check_tool_call(
        "write_file",
        {"file_path": str(tmp_path / "secrets" / "token.txt")},
        working_dir=str(tmp_path),
    )

    assert result.allowed is False
    assert result.risk_level == RiskLevel.BLOCK
    assert result.metadata["rule_id"] == "deny-secrets"


def test_command_prefix_permission_rule_is_narrow():
    guard = Guardrails(
        permission_rules=[
            {
                "id": "allow-pytest",
                "tool": "execute_command",
                "decision": "allow",
                "command_prefixes": ["uv run pytest"],
            }
        ]
    )

    allowed = guard.check_tool_call("execute_command", {"command": "uv run pytest -q"})
    not_matched = guard.check_tool_call("execute_command", {"command": "uv sync"})

    assert allowed.allowed is True
    assert allowed.requires_confirmation is False
    assert allowed.metadata["rule_id"] == "allow-pytest"
    assert not_matched.allowed is True
    assert not_matched.risk_level == RiskLevel.WARN
    assert "rule_id" not in not_matched.metadata


def test_structured_command_policy_classifies_core_commands():
    guard = Guardrails()

    git_status = guard.check_command("git status --short")
    git_reset = guard.check_command("git reset --hard HEAD")
    inline_python = guard.check_command("python -c 'print(1)'")

    assert git_status.risk_level == RiskLevel.SAFE
    assert git_status.metadata["command_analysis"]["family"] == "git"
    assert git_reset.risk_level == RiskLevel.DANGER
    assert inline_python.risk_level == RiskLevel.WARN


def test_strict_shell_parsing_blocks_shell_features():
    guard = Guardrails(strict_shell_parsing=True)
    result = guard.check_command("echo hello | cat")
    assert result.allowed is False
    assert result.risk_level == RiskLevel.BLOCK
    assert "strict shell parsing" in result.reason.lower()


def test_network_policy_blocks_configured_web_domain():
    guard = Guardrails(network_policy={"blocked_domains": ["blocked.example"]})

    result = guard.check_tool_call("web_fetch", {"url": "https://blocked.example/data"})

    assert result.allowed is False
    assert result.risk_level == RiskLevel.BLOCK
    assert result.metadata["network_analysis"]["hostname"] == "blocked.example"


def test_network_policy_allowlist_blocks_unlisted_domain():
    guard = Guardrails(network_policy={"allowed_domains": ["docs.example"]})

    allowed = guard.check_tool_call("web_fetch", {"url": "https://docs.example/page"})
    blocked = guard.check_tool_call("web_fetch", {"url": "https://other.example/page"})

    assert allowed.allowed is True
    assert blocked.allowed is False
    assert "not in allowed" in blocked.reason.lower()


def test_network_policy_requires_confirmation_for_localhost_by_default():
    guard = Guardrails()

    result = guard.check_tool_call("web_fetch", {"url": "http://127.0.0.1:8000/health"})

    assert result.allowed is True
    assert result.risk_level == RiskLevel.DANGER
    assert result.requires_confirmation is True
    assert result.metadata["network_analysis"]["is_internal"] is True


def test_command_policy_applies_blocked_domain_to_curl():
    guard = Guardrails(network_policy={"blocked_domains": ["blocked.example"]})

    result = guard.check_command("curl https://blocked.example/install.sh")

    assert result.allowed is False
    assert result.risk_level == RiskLevel.BLOCK
    assert result.metadata["command_analysis"]["network_analysis"]["hostname"] == "blocked.example"


def test_mcp_untrusted_tool_requires_confirmation():
    guard = Guardrails()

    result = guard.check_tool_call(
        "demo_danger",
        {"path": "README.md"},
        tool_context={"kind": "mcp", "server": "demo", "tool": "danger", "trusted": False},
    )

    assert result.allowed is True
    assert result.risk_level == RiskLevel.DANGER
    assert result.requires_confirmation is True
    assert result.metadata["mcp_server"] == "demo"


def test_mcp_denied_tool_blocks_before_execution():
    guard = Guardrails()

    result = guard.check_tool_call(
        "demo_danger",
        {"path": "README.md"},
        tool_context={
            "kind": "mcp",
            "server": "demo",
            "tool": "danger",
            "trusted": True,
            "denied_tools": ["danger"],
        },
    )

    assert result.allowed is False
    assert result.risk_level == RiskLevel.BLOCK
    assert result.metadata["mcp_tool"] == "danger"


def test_read_file_redacts_secret_content(tmp_path: Path):
    secret_file = tmp_path / ".env"
    secret_file.write_text("OPENAI_API_KEY=sk-testsecret1234567890\nSAFE=value\n", encoding="utf-8")
    tool = ReadFileTool(config={"working_dir": str(tmp_path)})

    result = tool.execute(str(secret_file))

    assert result.success is True
    assert "sk-testsecret1234567890" not in result.output
    assert "[REDACTED_SECRET]" in result.output
    assert result.metadata["secret_findings_count"] >= 1


def test_write_file_secret_content_warns_by_default(tmp_path: Path):
    guard = Guardrails()

    result = guard.check_tool_call(
        "write_file",
        {
            "file_path": str(tmp_path / "config.py"),
            "content": "TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz1234567890'",
        },
        working_dir=str(tmp_path),
    )

    assert result.allowed is True
    assert result.risk_level == RiskLevel.DANGER
    assert result.requires_confirmation is True
    assert result.metadata["secret_findings_count"] >= 1


def test_write_file_secret_content_blocks_when_configured(tmp_path: Path):
    guard = Guardrails(secrets_policy={"block_on_write": True})

    result = guard.check_tool_call(
        "write_file",
        {
            "file_path": str(tmp_path / "config.py"),
            "content": "password = 'super-secret-password'",
        },
        working_dir=str(tmp_path),
    )

    assert result.allowed is False
    assert result.risk_level == RiskLevel.BLOCK
    assert result.metadata["secret_findings_count"] >= 1


def test_security_audit_redacts_secret_values(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    logger = SecurityAuditLogger(path=audit_path)

    logger.log_tool_event(
        tool_name="write_file",
        arguments={"content": "OPENAI_API_KEY=sk-testsecret1234567890"},
        result=ToolResult(success=False, output="", error="failed with sk-testsecret1234567890"),
    )

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert "sk-testsecret1234567890" not in json.dumps(event)
    assert event["arguments"]["content"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_react_loop_blocks_execute_command_before_tool_execution():
    class TrackingCommandTool(BaseTool):
        name = "execute_command"
        description = "tracking shell tool"

        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute(self, command: str) -> ToolResult:
            self.calls += 1
            return ToolResult(success=True, output="should not run")

    registry = ToolRegistry()
    registry.clear()
    tool = TrackingCommandTool()
    registry.register(tool)

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=".",
    )

    result = await loop._act(
        ParsedAction(tool_name="execute_command", arguments={"command": "rm -rf /"})
    )
    assert result.success is False
    assert "dangerous" in (result.error or "").lower()
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_react_loop_warn_confirmation_executes_after_proceed():
    class TrackingCommandTool(BaseTool):
        name = "execute_command"
        description = "tracking shell tool"

        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute(self, command: str) -> ToolResult:
            self.calls += 1
            return ToolResult(success=True, output="ran")

    registry = ToolRegistry()
    registry.clear()
    tool = TrackingCommandTool()
    registry.register(tool)

    async def interaction_callback(metadata):
        question = metadata["questions"][0]["question"]
        return {
            "skipped": False,
            "answers": {question: "Proceed"},
            "all_answers": [{"question": question, "answer": "Proceed", "skipped": False}],
            "display": "Proceed",
        }

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(permission_mode="request"),
        working_dir=".",
        interaction_callback=interaction_callback,
    )

    result = await loop._act(
        ParsedAction(tool_name="execute_command", arguments={"command": "echo hi | cat"})
    )
    assert result.success is True
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_react_loop_warn_confirmation_cancels_on_decline():
    class TrackingCommandTool(BaseTool):
        name = "execute_command"
        description = "tracking shell tool"

        def __init__(self):
            super().__init__()
            self.calls = 0

        def execute(self, command: str) -> ToolResult:
            self.calls += 1
            return ToolResult(success=True, output="ran")

    registry = ToolRegistry()
    registry.clear()
    tool = TrackingCommandTool()
    registry.register(tool)

    async def interaction_callback(metadata):
        question = metadata["questions"][0]["question"]
        return {
            "skipped": False,
            "answers": {question: "Cancel"},
            "all_answers": [{"question": question, "answer": "Cancel", "skipped": False}],
            "display": "Cancel",
        }

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(permission_mode="request"),
        working_dir=".",
        interaction_callback=interaction_callback,
    )

    result = await loop._act(
        ParsedAction(tool_name="execute_command", arguments={"command": "echo hi | cat"})
    )
    assert result.success is False
    assert "declined" in (result.error or "").lower()
    assert tool.calls == 0


def test_file_write_tool_respects_sandbox_boundaries(tmp_path: Path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    outside = tmp_path / "outside.txt"

    tool = WriteFileTool(config={"working_dir": str(workdir)})
    blocked = tool.execute(str(outside), "x")
    assert blocked.success is False
    assert "outside allowed directories" in (blocked.error or "").lower()

    inside = workdir / "ok.txt"
    allowed = tool.execute(str(inside), "hello")
    assert allowed.success is True
    assert inside.read_text(encoding="utf-8") == "hello"


def test_execute_command_prefers_argv_and_falls_back_to_shell():
    tool = ExecuteCommandTool(
        config={
            "working_dir": ".",
            "strict_shell_parsing": False,
            "process_sandbox": {"enabled": False},
        }
    )

    with patch("opennova.tools.shell_tools.subprocess.run") as mock_run:
        mock_run.side_effect = [
            Completed(returncode=0, stdout="ok-argv\n"),
            Completed(returncode=0, stdout="ok-shell\n"),
        ]
        plain = tool.execute("echo hello")
        shell = tool.execute("echo hello | cat")

    assert plain.success is True
    assert shell.success is True
    assert mock_run.call_args_list[0].kwargs["shell"] is False
    assert mock_run.call_args_list[1].kwargs["shell"] is True


def test_execute_command_rejects_shell_syntax_in_strict_mode():
    tool = ExecuteCommandTool(config={"working_dir": ".", "strict_shell_parsing": True})
    result = tool.execute("echo hello | cat")
    assert result.success is False
    assert "strict shell parsing" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_react_loop_normalizes_execute_command_model_aliases_before_guard(tmp_path: Path):
    registry = ToolRegistry()
    registry.clear()
    registry.register(
        ExecuteCommandTool(
            config={"working_dir": str(tmp_path), "process_sandbox": {"enabled": False}}
        )
    )

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=str(tmp_path),
    )

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    with patch("opennova.tools.shell_tools.asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.return_value = DummyProcess()
        result = await loop._act(
            ParsedAction(
                tool_name="execute_command",
                arguments={"cmd": "echo hi", "cwd": str(tmp_path)},
            )
        )

    assert result.success is True
    assert result.metadata["command"] == "echo hi"
    assert result.metadata["working_dir"] == str(tmp_path)


@pytest.mark.asyncio
async def test_react_loop_checks_execute_command_aliases_for_dangerous_commands(tmp_path: Path):
    registry = ToolRegistry()
    registry.clear()
    registry.register(
        ExecuteCommandTool(
            config={"working_dir": str(tmp_path), "process_sandbox": {"enabled": False}}
        )
    )
    audit_path = tmp_path / "audit" / "security.jsonl"

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=str(tmp_path),
        audit_logger=SecurityAuditLogger(path=audit_path, session_id="test-session"),
    )

    result = await loop._act(
        ParsedAction(
            tool_name="execute_command",
            arguments={"cmd": "rm -rf /", "api_key": "secret-value"},
        )
    )

    assert result.success is False
    assert "dangerous" in (result.error or "").lower()
    audit_event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert audit_event["session_id"] == "test-session"
    assert audit_event["tool_name"] == "execute_command"
    assert audit_event["arguments"]["api_key"] == "[REDACTED]"
    assert audit_event["confirmation_outcome"] == "blocked"
    assert audit_event["guard"]["risk_level"] == "block"


@pytest.mark.asyncio
async def test_react_loop_normalizes_execute_command_array_commands(tmp_path: Path):
    registry = ToolRegistry()
    registry.clear()
    registry.register(
        ExecuteCommandTool(
            config={"working_dir": str(tmp_path), "process_sandbox": {"enabled": False}}
        )
    )

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=str(tmp_path),
    )

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    with patch("opennova.tools.shell_tools.asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.return_value = DummyProcess()
        result = await loop._act(
            ParsedAction(
                tool_name="execute_command",
                arguments={"command": ["echo", "hi"]},
            )
        )

    assert result.success is True
    assert result.metadata["command"] == "echo hi"
    assert mock_exec.call_args.args[:2] == ("echo", "hi")


@pytest.mark.asyncio
async def test_react_loop_normalizes_execute_command_args_field(tmp_path: Path):
    registry = ToolRegistry()
    registry.clear()
    registry.register(
        ExecuteCommandTool(
            config={"working_dir": str(tmp_path), "process_sandbox": {"enabled": False}}
        )
    )

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=str(tmp_path),
    )

    class DummyProcess:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    with patch("opennova.tools.shell_tools.asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.return_value = DummyProcess()
        result = await loop._act(
            ParsedAction(
                tool_name="execute_command",
                arguments={"command": "echo", "args": ["hi"]},
            )
        )

    assert result.success is True
    assert result.metadata["command"] == "echo hi"
    assert mock_exec.call_args.args[:2] == ("echo", "hi")


@pytest.mark.asyncio
async def test_web_fetch_blocks_when_network_disabled():
    tool = WebFetchTool(config={"allow_network": False})
    result = await tool.async_execute("https://example.com")
    assert result.success is False
    assert "network access is disabled" in (result.error or "").lower()
