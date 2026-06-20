"""Tests for SDK/headless events and worktree workflow tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from opennova.providers.base import StreamChunk
from opennova.tools.base import ToolResult


class FakeRuntime:
    """Small runtime double that emits the same callback events as AgentRuntime."""

    def __init__(self, config):
        self.config = config
        self.callbacks = {}
        self.session_manager = type(
            "SessionManagerDouble",
            (),
            {"session_id": "session-1", "list_sessions": lambda self: []},
        )()

    def register_callback(self, event: str, callback):
        self.callbacks[event] = callback

    async def run(self, task: str, mode: str = "act", stream: bool = True) -> str:
        self.callbacks["action"]("read_file", {"file_path": "README.md"})
        self.callbacks["result"](ToolResult(success=True, output="read ok"))
        self.callbacks["stream"](StreamChunk(content="final text"))
        return f"completed: {task}"

    def resume_session(self, session_id: str):
        return []

    def get_sessions(self):
        return []


@pytest.mark.asyncio
async def test_sdk_stream_message_emits_headless_event_contract():
    from opennova.sdk import OpenNovaClient

    client = OpenNovaClient({"default_provider": "deepseek"}, runtime_factory=FakeRuntime)
    session_id = client.create_session()

    events = [
        event
        async for event in client.stream_message(
            session_id,
            "inspect repository",
            mode="act",
            stream=True,
        )
    ]

    assert [event.type for event in events] == [
        "run_start",
        "tool_start",
        "tool_result",
        "text_delta",
        "run_complete",
    ]
    assert events[0].session_id == session_id
    assert events[1].data["tool_name"] == "read_file"
    assert events[1].data["tool_id"].startswith("tool_")
    assert events[2].data["tool_id"] == events[1].data["tool_id"]
    assert events[2].data["duration_ms"] >= 0
    assert events[3].data["content"] == "final text"
    assert events[-1].data["result"] == "completed: inspect repository"


@dataclass
class Completed:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def test_worktree_tools_create_and_remove_worktree(tmp_path: Path):
    from opennova.tools.worktree_tools import EnterWorktreeTool, ExitWorktreeTool

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return Completed(stdout=str(tmp_path))
        return Completed(stdout="")

    with patch("opennova.tools.worktree_tools.subprocess.run", side_effect=fake_run):
        enter = EnterWorktreeTool(config={"working_dir": str(tmp_path)})
        exit_tool = ExitWorktreeTool(config={"working_dir": str(tmp_path)})
        target = tmp_path.parent / "feature-worktree"

        enter_result = enter.execute(branch="codex/feature", path=str(target), base="master")
        exit_result = exit_tool.execute(path=str(target), force=True)

    assert enter_result.success is True
    assert exit_result.success is True
    assert ["git", "worktree", "add", "-b", "codex/feature", str(target), "master"] in calls
    assert ["git", "worktree", "remove", "--force", str(target)] in calls
    assert enter_result.metadata["path"] == str(target.resolve())


def test_runtime_registers_sdk_alignment_tools():
    from opennova.runtime.agent import AgentRuntime

    runtime = AgentRuntime(
        {
            "default_provider": "deepseek",
            "providers": {"deepseek": {"api_key": "test-key", "default_model": "deepseek-v4-pro"}},
            "mcp": {"enabled": False, "servers": []},
            "skills": {"enabled": False, "dirs": []},
        },
        enable_mcp=False,
        enable_skills=False,
    )

    tools = set(runtime.get_tools())

    assert {"enter_worktree", "exit_worktree", "glob_files", "grep_code"}.issubset(tools)
