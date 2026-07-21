"""P1 regression coverage for execution, lifecycle, budgets, and memory."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opennova.checkpoints import CheckpointConflictError, CheckpointManager
from opennova.cli.checkpoint_commands import handle_checkpoint_command
from opennova.cli.command_dispatch import SlashCommandDispatcher
from opennova.cli.commands import SlashCommandRegistry
from opennova.mcp.connector import MCPConnector, MCPManager
from opennova.mcp.types import (
    MCPConnectionState,
    MCPMessage,
    MCPServerConfig,
    MCPTool,
)
from opennova.memory.layered import LayeredMemoryManager
from opennova.providers.base import (
    FinishReason,
    LLMResponse,
    Message,
    ProviderRateLimitError,
    ToolCall,
    Usage,
)
from opennova.runtime.artifacts import ArtifactStore, ToolResultBudget
from opennova.runtime.events import (
    ToolUseContext,
    reset_current_tool_context,
    set_current_tool_context,
)
from opennova.runtime.file_state import FileVersionCache
from opennova.runtime.loop import ReActLoop
from opennova.runtime.model_policy import ProviderCircuitBreaker
from opennova.runtime.state import AgentState
from opennova.runtime.workflow import WorkflowDecision, WorkflowRouter
from opennova.session import SessionManager
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.file_tools import EditFileTool, ReadFileTool
from opennova.tools.ignore import GitIgnoreService
from opennova.tools.tool_search import ToolSearchTool


class _ReadTool(BaseTool):
    description = "Read a value"

    def __init__(self, name: str, execute):
        super().__init__()
        self.name = name
        self._execute = execute

    def execute(self) -> ToolResult:
        return self._execute()

    def is_read_only(self, **kwargs) -> bool:
        return True


class _TwoToolProvider:
    model = "test-model"
    provider_name = "test"

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="read both",
                tool_calls=[
                    ToolCall(id="first", name="first", arguments={}),
                    ToolCall(id="second", name="second", arguments={}),
                ],
                finish_reason=FinishReason.TOOL_CALL,
            )
        return LLMResponse(content="done", finish_reason=FinishReason.STOP)


@pytest.mark.asyncio
async def test_parallel_read_tools_are_bounded_and_observed_in_model_order() -> None:
    second_started = threading.Event()

    def first() -> ToolResult:
        assert second_started.wait(timeout=0.5), "read-only calls were not scheduled concurrently"
        return ToolResult(True, "first-result")

    def second() -> ToolResult:
        second_started.set()
        return ToolResult(True, "second-result")

    loop = ReActLoop(
        _TwoToolProvider(),
        ToolRegistry([_ReadTool("first", first), _ReadTool("second", second)]),
        AgentState(),
        stream=False,
        deferred_tools_enabled=False,
        parallel_tool_limit=2,
    )
    results: list[str] = []

    assert await loop.run("read", on_result=lambda result: results.append(result.output)) == "done"
    assert results[:2] == ["first-result", "second-result"]
    tool_messages = [message for message in loop.messages if message.role == "tool"]
    assert [message.tool_call_id for message in tool_messages] == ["first", "second"]


def test_file_version_cache_rejects_stale_edit(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    cache = FileVersionCache()
    context = ToolUseContext(
        tool_id="read-1",
        tool_name="read_file",
        arguments={},
        read_file_cache=cache,
    )
    config = {"working_dir": str(tmp_path), "checkpoint_writes": False}
    token = set_current_tool_context(context)
    try:
        assert ReadFileTool(config).execute(str(target)).success
    finally:
        reset_current_tool_context(token)

    target.write_text("value = 2\n", encoding="utf-8")
    token = set_current_tool_context(context)
    try:
        result = EditFileTool(config).execute(str(target), "value = 2", "value = 3")
    finally:
        reset_current_tool_context(token)

    assert not result.success
    assert result.metadata["stale_file"] is True
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_large_result_is_offloaded_with_head_tail_preview(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "session")
    budget = ToolResultBudget(store, per_turn_chars=2_000)
    original = "HEAD" + "x" * 3_000 + "TAIL"

    result = budget.apply_one(ToolResult(True, original), "tool-1", 700)

    assert result.metadata["result_truncated"] is True
    artifact = Path(result.metadata["artifact_path"])
    assert artifact.read_text(encoding="utf-8") == original
    assert "HEAD" in result.output and "TAIL" in result.output


@pytest.mark.asyncio
async def test_tool_search_discovers_hidden_schema_on_next_turn() -> None:
    hidden = _ReadTool("git_history", lambda: ToolResult(True, "history"))
    registry = ToolRegistry([hidden])
    registry.register(ToolSearchTool(registry))

    class Provider:
        model = "test-model"
        provider_name = "test"

        def __init__(self) -> None:
            self.tools_seen: list[list[str]] = []

        async def chat(self, messages, tools=None, **kwargs):
            names = [tool.name for tool in tools or []]
            self.tools_seen.append(names)
            if len(self.tools_seen) == 1:
                return LLMResponse(
                    "search",
                    [ToolCall("search", "tool_search", {"query": "git history"})],
                    finish_reason=FinishReason.TOOL_CALL,
                )
            return LLMResponse("done", finish_reason=FinishReason.STOP)

    provider = Provider()
    loop = ReActLoop(provider, registry, AgentState(), stream=False)
    assert await loop.run("inspect git", route_workflow=False) == "done"
    assert "git_history" not in provider.tools_seen[0]
    assert "git_history" in provider.tools_seen[1]


def test_gitignore_service_handles_nested_rules_negation_and_anchor(tmp_path: Path) -> None:
    nested = tmp_path / "pkg"
    nested.mkdir()
    (tmp_path / ".gitignore").write_text("*.log\n!keep.log\n/root.txt\n", encoding="utf-8")
    (nested / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    paths = {
        "ignored": tmp_path / "trace.log",
        "kept": tmp_path / "keep.log",
        "root": tmp_path / "root.txt",
        "nested_root": nested / "root.txt",
        "nested_tmp": nested / "cache.tmp",
    }
    for path in paths.values():
        path.write_text("x", encoding="utf-8")
    ignore = GitIgnoreService(tmp_path)

    assert ignore.is_ignored(paths["ignored"])
    assert not ignore.is_ignored(paths["kept"])
    assert ignore.is_ignored(paths["root"])
    assert not ignore.is_ignored(paths["nested_root"])
    assert ignore.is_ignored(paths["nested_tmp"])


def test_checkpoint_tracks_create_and_refuses_conflicting_restore(tmp_path: Path) -> None:
    target = tmp_path / "created.txt"
    manager = CheckpointManager(tmp_path)
    checkpoint_id = manager.create("create", [target], run_id="run-1", tool_id="tool-1")
    target.write_text("created", encoding="utf-8")
    checkpoint = manager.finalize(checkpoint_id)
    assert checkpoint.entries[0].operation == "create"

    target.write_text("changed later", encoding="utf-8")
    with pytest.raises(CheckpointConflictError):
        manager.restore(checkpoint_id)
    manager.restore(checkpoint_id, force=True)
    assert not target.exists()


def test_checkpoint_rewind_previews_before_explicit_apply(tmp_path: Path) -> None:
    target = tmp_path / "app.txt"
    target.write_text("before\n", encoding="utf-8")
    manager = CheckpointManager(tmp_path)
    checkpoint_id = manager.create("edit", [target])
    target.write_text("after\n", encoding="utf-8")
    manager.finalize(checkpoint_id)

    preview = handle_checkpoint_command(tmp_path, f"rewind {checkpoint_id}")
    assert preview.success and preview.metadata["preview"] is True
    assert target.read_text(encoding="utf-8") == "after\n"
    applied = handle_checkpoint_command(tmp_path, f"rewind --apply {checkpoint_id}")
    assert applied.success
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.asyncio
async def test_mcp_pagination_roots_elicitation_and_registry_refresh(tmp_path: Path) -> None:
    connector = MCPConnector(MCPServerConfig(name="demo", command="demo"))
    connector.state = MCPConnectionState.CONNECTED
    calls: list[tuple[str, dict | None]] = []

    async def send_request(method, params=None, timeout=30.0):
        del timeout
        calls.append((method, params))
        if method == "resources/templates/list" and not params:
            return {"resourceTemplates": [{"name": "first"}], "nextCursor": "next"}
        if method == "resources/templates/list":
            return {"resourceTemplates": [{"name": "second"}]}
        return {}

    connector._send_request = send_request
    assert [item["name"] for item in await connector.list_resource_templates()] == [
        "first",
        "second",
    ]
    assert calls[-1][1] == {"cursor": "next"}

    class Transport:
        def __init__(self) -> None:
            self.sent: list[MCPMessage] = []

        async def send(self, message: MCPMessage) -> None:
            self.sent.append(message)

    transport = Transport()
    connector.transport = transport
    connector.roots_provider = lambda: [{"uri": tmp_path.as_uri(), "name": "workspace"}]
    connector.elicitation_handler = lambda params: {
        "action": "accept",
        "content": {"value": params["message"]},
    }
    await connector._handle_server_request(MCPMessage(id=1, method="roots/list", params={}))
    await connector._handle_server_request(
        MCPMessage(id=2, method="elicitation/create", params={"message": "ok"})
    )
    assert transport.sent[0].result["roots"][0]["name"] == "workspace"
    assert transport.sent[1].result["action"] == "accept"

    registry = ToolRegistry()
    manager = MCPManager(registry)
    manager.connectors["demo"] = connector
    connector.tools = {
        "demo_old": MCPTool("old", "old", server_name="demo"),
    }
    await manager._sync_server_tools("demo")
    assert registry.has_tool("demo_old")
    connector.tools = {
        "demo_new": MCPTool("new", "new", server_name="demo"),
    }
    await manager._sync_server_tools("demo")
    assert not registry.has_tool("demo_old")
    assert registry.has_tool("demo_new")


@pytest.mark.asyncio
async def test_model_retry_fallback_budget_and_local_workflow_routing() -> None:
    class Primary:
        model = "primary"
        provider_name = "test"

        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            raise ProviderRateLimitError("busy", retryable=True)

    class Fallback:
        model = "fallback"
        provider_name = "test"

        def __init__(self) -> None:
            self.max_tokens = 0

        async def chat(self, messages, tools=None, **kwargs):
            self.max_tokens = kwargs["max_tokens"]
            return LLMResponse(
                "done",
                finish_reason=FinishReason.STOP,
                usage=Usage(3, 2, 5),
            )

    primary = Primary()
    fallback = Fallback()
    loop = ReActLoop(
        primary,
        ToolRegistry(),
        AgentState(),
        stream=False,
        token_budget=10,
        max_output_tokens=7,
        fallback_providers=[fallback],
        provider_retry_attempts=2,
    )
    assert await loop.run("answer", route_workflow=False) == "done"
    assert primary.calls == 2
    assert fallback.max_tokens == 7
    assert loop.run_budget.snapshot().total_tokens == 5
    assert (
        WorkflowRouter.route_local("先列计划，等我确认后再修改").decision == WorkflowDecision.PLAN
    )
    assert WorkflowRouter.route_local("不用计划，直接实现").decision == WorkflowDecision.ACT

    breaker = ProviderCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    breaker.record_failure(primary)
    assert not breaker.is_open(primary)
    breaker.record_failure(primary)
    assert breaker.is_open(primary)
    breaker.record_success(primary)
    assert not breaker.is_open(primary)


def test_layered_memory_metadata_expiry_management_and_normalized_dedupe(tmp_path: Path) -> None:
    manager = LayeredMemoryManager(tmp_path)
    manager.add("active", "Always run tests first.", provenance="user", scope="project")
    manager.add("duplicate", "  always   run TESTS first.  ", provenance="agent")
    manager.add(
        "expired",
        "Never inject this.",
        expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )
    manager.add("session-only", "Do not cross sessions.", scope="session")

    context = manager.load_for_context()
    assert context is not None
    assert context.lower().count("always run tests first.") == 1
    assert "Never inject this" not in context
    assert "Do not cross sessions" not in context
    assert any(record.expired for record in manager.list_records())
    assert manager.delete("active") is True
    assert manager.delete("active") is False


def test_session_fork_rewrites_identity_and_preserves_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project))
    source_id = manager.start_session()
    manager.save_message(Message(role="user", content="original task"))

    fork_id = manager.fork_session(source_id)
    manager.resume_session(fork_id)
    manager.save_snapshot([Message(role="user", content="forked task")])

    assert fork_id != source_id
    assert [message.content for message in manager.load_session(fork_id)] == ["forked task"]
    fork_path = next(
        item.file_path for item in manager.list_sessions() if item.session_id == fork_id
    )
    fork_text = fork_path.read_text(encoding="utf-8")
    assert f'"session_id": "{fork_id}"' in fork_text
    assert f'"forked_from": "{source_id}"' in fork_text


@pytest.mark.asyncio
async def test_slash_command_dispatcher_owns_normalization_and_invocation() -> None:
    class Target:
        def __init__(self) -> None:
            self.args = ""

        async def _cmd_memory(self, args: str) -> None:
            self.args = args

    target = Target()
    dispatcher = SlashCommandDispatcher(SlashCommandRegistry.default())
    assert await dispatcher.dispatch(target, "/memory add rule Run tests")
    assert target.args == "add rule Run tests"
