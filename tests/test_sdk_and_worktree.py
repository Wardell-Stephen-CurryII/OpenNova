"""Tests for SDK/headless events and worktree workflow tools."""

from __future__ import annotations

import asyncio
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
        self.closed = False

    def register_callback(self, event: str, callback):
        self.callbacks.setdefault(event, []).append(callback)

        def unsubscribe():
            listeners = self.callbacks.get(event, [])
            if callback in listeners:
                listeners.remove(callback)
            if not listeners:
                self.callbacks.pop(event, None)

        return unsubscribe

    def emit(self, event: str, *args):
        for callback in tuple(self.callbacks.get(event, [])):
            callback(*args)

    async def run(self, task: str, mode: str = "act", stream: bool = True) -> str:
        self.emit("action", "read_file", {"file_path": "README.md"})
        self.emit("result", ToolResult(success=True, output="read ok"))
        self.emit("stream", StreamChunk(content="final text"))
        return f"completed: {task}"

    def resume_session(self, session_id: str):
        return []

    def get_sessions(self):
        return []

    async def aclose(self):
        self.closed = True


class BlockingRuntime(FakeRuntime):
    """Runtime double that remains active until the SDK cancels it."""

    def __init__(self, config):
        super().__init__(config)
        self.started = asyncio.Event()
        self.cancelled = False

    async def run(self, task: str, mode: str = "act", stream: bool = True) -> str:
        del task, mode, stream
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


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
    assert client.get_runtime(session_id).callbacks == {}


@pytest.mark.asyncio
async def test_sdk_callbacks_remain_stable_across_one_hundred_turns():
    from opennova.sdk import OpenNovaClient

    client = OpenNovaClient({"default_provider": "deepseek"}, runtime_factory=FakeRuntime)
    session_id = client.create_session()
    runtime = client.get_runtime(session_id)

    for turn in range(100):
        result = await client.submit_message(session_id, f"turn {turn}", stream=False)
        assert result == f"completed: turn {turn}"
        assert runtime.callbacks == {}

    await client.aclose()


@pytest.mark.asyncio
async def test_sdk_cancel_and_close_release_the_session_runtime():
    from opennova.sdk import OpenNovaClient

    client = OpenNovaClient({}, runtime_factory=BlockingRuntime)
    session_id = client.create_session()
    runtime = client.get_runtime(session_id)

    async def collect_events():
        return [event async for event in client.stream_message(session_id, "wait")]

    consumer = asyncio.create_task(collect_events())
    await runtime.started.wait()
    assert await client.cancel_run(session_id) is True
    events = await consumer

    assert [event.type for event in events] == ["run_start", "run_cancelled"]
    assert runtime.cancelled is True
    assert runtime.callbacks == {}
    assert await client.close_session(session_id) is True
    assert runtime.closed is True
    assert client.list_sessions() == []


@pytest.mark.asyncio
async def test_sdk_aclose_is_idempotent_and_rejects_new_sessions():
    from opennova.sdk import OpenNovaClient

    client = OpenNovaClient({}, runtime_factory=FakeRuntime)
    session_id = client.create_session()
    runtime = client.get_runtime(session_id)

    await client.aclose()
    await client.aclose()

    assert runtime.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        client.create_session()


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


def test_runtime_registers_sdk_alignment_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from opennova.runtime.agent import AgentRuntime

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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
