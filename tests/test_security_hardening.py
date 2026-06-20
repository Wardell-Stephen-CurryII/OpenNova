"""Focused tests for sandbox/guardrails hardening behaviors."""

from pathlib import Path
from unittest.mock import patch

import pytest

from opennova.runtime.loop import ParsedAction, ReActLoop
from opennova.runtime.state import AgentState
from opennova.security.guardrails import Guardrails, RiskLevel
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.file_tools import WriteFileTool
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

    result = await loop._act(ParsedAction(tool_name="execute_command", arguments={"command": "rm -rf /"}))
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
        guardrails=Guardrails(),
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
        guardrails=Guardrails(),
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
    tool = ExecuteCommandTool(config={"working_dir": ".", "strict_shell_parsing": False})

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
    registry.register(ExecuteCommandTool(config={"working_dir": str(tmp_path)}))

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=str(tmp_path),
    )

    with patch("opennova.tools.shell_tools.subprocess.run") as mock_run:
        mock_run.return_value = Completed(returncode=0, stdout="ok\n")
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
    registry.register(ExecuteCommandTool(config={"working_dir": str(tmp_path)}))

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=str(tmp_path),
    )

    result = await loop._act(
        ParsedAction(tool_name="execute_command", arguments={"cmd": "rm -rf /"})
    )

    assert result.success is False
    assert "dangerous" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_react_loop_normalizes_execute_command_array_commands(tmp_path: Path):
    registry = ToolRegistry()
    registry.clear()
    registry.register(ExecuteCommandTool(config={"working_dir": str(tmp_path)}))

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=str(tmp_path),
    )

    with patch("opennova.tools.shell_tools.subprocess.run") as mock_run:
        mock_run.return_value = Completed(returncode=0, stdout="ok\n")
        result = await loop._act(
            ParsedAction(
                tool_name="execute_command",
                arguments={"command": ["echo", "hi"]},
            )
        )

    assert result.success is True
    assert result.metadata["command"] == "echo hi"
    assert mock_run.call_args.args[0] == ["echo", "hi"]


@pytest.mark.asyncio
async def test_react_loop_normalizes_execute_command_args_field(tmp_path: Path):
    registry = ToolRegistry()
    registry.clear()
    registry.register(ExecuteCommandTool(config={"working_dir": str(tmp_path)}))

    loop = ReActLoop(
        llm=DummyProvider(),
        tool_registry=registry,
        state=AgentState(),
        stream=False,
        guardrails=Guardrails(),
        working_dir=str(tmp_path),
    )

    with patch("opennova.tools.shell_tools.subprocess.run") as mock_run:
        mock_run.return_value = Completed(returncode=0, stdout="ok\n")
        result = await loop._act(
            ParsedAction(
                tool_name="execute_command",
                arguments={"command": "echo", "args": ["hi"]},
            )
        )

    assert result.success is True
    assert result.metadata["command"] == "echo hi"
    assert mock_run.call_args.args[0] == ["echo", "hi"]


@pytest.mark.asyncio
async def test_web_fetch_blocks_when_network_disabled():
    tool = WebFetchTool(config={"allow_network": False})
    result = await tool.async_execute("https://example.com")
    assert result.success is False
    assert "network access is disabled" in (result.error or "").lower()
