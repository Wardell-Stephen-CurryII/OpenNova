"""Regression coverage for the reliability defects tracked by development plan 14."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shlex
import signal
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from opennova.config import DEFAULT_CONFIG, Config, load_config
from opennova.memory.compressor import ContextCompressor
from opennova.memory.context import ContextManager, MessageAddStatus
from opennova.memory.extractor import MemoryExtractor
from opennova.memory.types.feedback_memory import FeedbackType
from opennova.providers.base import (
    FinishReason,
    LLMResponse,
    Message,
    ProviderContextLengthError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRetryExhaustedError,
    ProviderTimeoutError,
    ToolCall,
    normalize_provider_error,
    parse_tool_arguments,
)
from opennova.providers.models import get_model_profile
from opennova.security.guardrails import Guardrails
from opennova.security.workspace_trust import WorkspaceTrustStore
from opennova.session.manager import SessionManager, _sanitize_path
from opennova.tasks import TaskManager
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult
from opennova.tools.shell_tools import ExecuteCommandTool
from opennova.tools.task_tools import TaskCreateTool, TaskListTool


class RecordingCompressor:
    def __init__(self) -> None:
        self.calls: list[tuple[list[Message], str | None]] = []

    async def compress(
        self,
        messages: list[Message],
        previous_summary: str | None = None,
    ) -> str:
        self.calls.append((messages, previous_summary))
        return f"summary-{len(self.calls)}"


class FailingCompressor:
    def __init__(self) -> None:
        self.calls = 0

    async def compress(
        self,
        messages: list[Message],
        previous_summary: str | None = None,
    ) -> str:
        del messages, previous_summary
        self.calls += 1
        raise RuntimeError("summary backend unavailable")


def test_compression_prompt_interpolates_previous_summary() -> None:
    compressor = ContextCompressor.__new__(ContextCompressor)

    prompt = compressor.build_compression_prompt("conversation", "earlier decisions")

    assert "<previous_summary>\nearlier decisions\n</previous_summary>" in prompt
    assert "{previous_summary}" not in prompt


@pytest.mark.asyncio
async def test_automatic_context_compression_executes() -> None:
    context = ContextManager(model="test", context_window=240)
    context._encoding = None
    context.compression_threshold = 0.3
    context.keep_last_pairs = 1
    compressor = RecordingCompressor()
    context.set_compressor(compressor)

    for index in range(6):
        assert context.add_message(Message(role="user", content=f"message-{index}-" + "x" * 30))

    result = await context.add_message_and_compress(
        Message(role="assistant", content="final-" + "y" * 30)
    )

    assert result.status is MessageAddStatus.ADDED
    assert len(compressor.calls) == 1
    assert context.get_compressed_summary() == "summary-1"
    assert context._compressing is False


@pytest.mark.asyncio
async def test_context_group_rejection_never_partially_writes_tool_protocol() -> None:
    context = ContextManager(model="test", context_window=40)
    context._encoding = None
    call = ToolCall(id="call-1", name="read_file", arguments={"path": "x" * 200})
    group = [
        Message(role="assistant", content="", tool_calls=[call]),
        Message(role="tool", content="z" * 200, tool_call_id=call.id),
    ]

    result = await context.add_messages_and_compress(group)

    assert result.status is MessageAddStatus.REJECTED
    assert context.messages == []


@pytest.mark.asyncio
async def test_compression_failures_open_a_session_circuit_breaker() -> None:
    context = ContextManager(model="test", context_window=1_000)
    context.keep_last_pairs = 1
    compressor = FailingCompressor()
    context.set_compressor(compressor)
    for index in range(6):
        assert context.add_message(Message(role="user", content=f"message-{index}"))

    for _ in range(context.compression_failure_limit + 2):
        assert await context.compress() is False

    assert compressor.calls == context.compression_failure_limit


def test_context_fallback_trimming_never_leaves_orphaned_tool_results() -> None:
    context = ContextManager(model="test", context_window=10_000, max_messages=5)
    call = ToolCall(id="call-old", name="read_file", arguments={})
    context.messages = [
        Message(role="user", content="old turn"),
        Message(role="assistant", content="", tool_calls=[call]),
        Message(role="tool", content="result", tool_call_id=call.id),
        Message(role="assistant", content="done"),
        Message(role="user", content="current turn"),
    ]

    result = context.add_message(Message(role="assistant", content="current response"))

    assert result.status is MessageAddStatus.ADDED
    assert [message.role for message in context.messages] == ["user", "assistant"]


def test_anthropic_tool_only_message_omits_empty_text_block() -> None:
    message = Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call-1", name="read_file", arguments={})],
    )

    payload = message.to_anthropic_format()

    assert payload["content"] == [
        {
            "type": "tool_use",
            "id": "call-1",
            "name": "read_file",
            "input": {},
        }
    ]


def test_malformed_tool_arguments_are_rejected_before_execution() -> None:
    with pytest.raises(ProviderProtocolError, match="Malformed arguments"):
        parse_tool_arguments("{broken", tool_name="write_file", tool_call_id="call-1")

    with pytest.raises(ProviderProtocolError, match="must be a JSON object"):
        parse_tool_arguments("[1, 2]", tool_name="write_file", tool_call_id="call-2")


def test_provider_errors_are_normalized_to_stable_categories() -> None:
    rate_limit = normalize_provider_error(RuntimeError("Rate limit exceeded"), provider="deepseek")
    context_limit = normalize_provider_error(
        RuntimeError("maximum context length exceeded"), provider="openai"
    )

    assert isinstance(rate_limit, ProviderRateLimitError)
    assert rate_limit.retryable is True
    assert isinstance(context_limit, ProviderContextLengthError)
    assert context_limit.retryable is False


def test_model_profile_is_shared_with_context_manager() -> None:
    profile = get_model_profile("deepseek", "deepseek-v4-pro")
    context = ContextManager(model="deepseek-v4-pro")

    assert profile.context_window == 131_072
    assert profile.supports_reasoning is True
    assert context.context_window == profile.context_window


def test_legacy_memory_extractor_is_explicit_and_no_longer_crashes() -> None:
    extractor = MemoryExtractor()
    result = extractor.extract_from_messages(
        [
            Message(
                role="user",
                content=(
                    "The project uses Python. See https://example.com/docs. This result is bad."
                ),
            )
        ]
    )

    assert result.user_memories[0].content == "Python"
    assert result.reference_memories[0].url == "https://example.com/docs"
    assert result.feedback_memories[0].feedback_type == FeedbackType.REJECTION


def test_config_instances_do_not_mutate_defaults_or_caller_data() -> None:
    defaults_before = copy.deepcopy(DEFAULT_CONFIG)
    source = copy.deepcopy(DEFAULT_CONFIG)
    config = Config(source)

    config.set("security.permission_mode", "request")
    source["providers"]["deepseek"]["api_key"] = "caller-mutated"

    assert defaults_before == DEFAULT_CONFIG
    assert config.get("providers.deepseek.api_key") != "caller-mutated"
    assert (
        Config().get("security.permission_mode") == defaults_before["security"]["permission_mode"]
    )


def test_config_redaction_masks_sensitive_keys_recursively() -> None:
    config = Config(
        {
            "providers": {"custom": {"api_key": "totally-custom-secret-value"}},
            "mcp": {"headers": {"Authorization": "Bearer secret-token"}},
            "nested": {"password": "password-value", "safe": "visible"},
            "metrics": {"token_count": 42, "max_tokens": 1000},
        }
    )

    redacted = config.redacted_data()

    assert redacted["providers"]["custom"]["api_key"] == "[REDACTED_SECRET]"
    assert redacted["mcp"]["headers"]["Authorization"] == "[REDACTED_SECRET]"
    assert redacted["nested"]["password"] == "[REDACTED_SECRET]"
    assert redacted["nested"]["safe"] == "visible"
    assert redacted["metrics"] == {"token_count": 42, "max_tokens": 1000}


def test_config_loader_rejects_non_mapping_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_config(str(config_path), load_env=False)


def test_click_config_command_never_prints_expanded_api_keys() -> None:
    from opennova.main import main

    secret = "deepseek-secret-that-must-not-leak"
    config = Config({"providers": {"deepseek": {"api_key": secret}}})

    with patch("opennova.main.load_config", return_value=config):
        result = CliRunner().invoke(main, ["config"])

    assert result.exit_code == 0
    assert secret not in result.output
    assert "[REDACTED_SECRET]" in result.output


def test_unicode_project_paths_get_distinct_readable_session_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    first_project = tmp_path / "中文" / "项目一"
    second_project = tmp_path / "中文" / "项目二"
    first_project.mkdir(parents=True)
    second_project.mkdir(parents=True)

    first = SessionManager(str(first_project))
    second = SessionManager(str(second_project))

    assert first._sessions_dir != second._sessions_dir
    assert "项目一" in first._sessions_dir.name
    assert "项目二" in second._sessions_dir.name
    assert len(first._sessions_dir.name.rsplit("-", 1)[-1]) == 12


@pytest.mark.parametrize(
    "invalid_id",
    ["../../outside", "not-a-uuid", "00000000-0000-0000-0000-000000000000.jsonl"],
)
def test_all_session_loaders_reject_non_uuid_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_id: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    manager = SessionManager(str(project))

    with pytest.raises(ValueError, match="Invalid session id"):
        manager.load_session(invalid_id)
    with pytest.raises(ValueError, match="Invalid session id"):
        manager.get_compression_markers(invalid_id)
    with pytest.raises(ValueError, match="Invalid session id"):
        manager.resume_session(invalid_id)


def test_verified_legacy_session_is_atomically_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "中文项目"
    project.mkdir()
    session_id = str(uuid.uuid4())
    legacy_dir = home / ".opennova" / "sessions" / _sanitize_path(str(project))
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / f"{session_id}.jsonl"
    payload = {
        "type": "session_header",
        "schema_version": 2,
        "session_id": session_id,
        "project_root": str(project.resolve()),
    }
    legacy_bytes = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
    legacy_file.write_bytes(legacy_bytes)

    manager = SessionManager(str(project))
    migrated = manager._sessions_dir / legacy_file.name

    assert migrated.read_bytes() == legacy_bytes
    assert not legacy_file.exists()
    assert manager.list_sessions()[0].session_id == session_id


def test_headerless_legacy_session_is_copied_only_when_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "中文项目"
    project.mkdir()
    session_id = str(uuid.uuid4())
    legacy_dir = home / ".opennova" / "sessions" / _sanitize_path(str(project))
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / f"{session_id}.jsonl"
    legacy_file.write_text(
        json.dumps(
            {
                "type": "message",
                "session_id": session_id,
                "message": {"role": "user", "content": "legacy"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    original = legacy_file.read_bytes()

    manager = SessionManager(str(project))
    assert manager.list_sessions()[0].file_path == legacy_file

    manager.resume_session(session_id)

    assert legacy_file.read_bytes() == original
    assert (manager._sessions_dir / legacy_file.name).read_bytes() == original


def test_runtime_owned_task_managers_isolate_state_and_output(tmp_path: Path) -> None:
    first_manager = TaskManager(output_dir=tmp_path, namespace="runtime-a")
    second_manager = TaskManager(output_dir=tmp_path, namespace="runtime-b")

    created = TaskCreateTool(first_manager).execute(
        subject="First runtime task",
        description="Must remain isolated",
    )
    task_id = created.metadata["task_id"]
    first_manager.write_task_output(task_id, "private output")

    assert TaskListTool(first_manager).execute().metadata["tasks"]
    assert TaskListTool(second_manager).execute().output == "No tasks in the task list."
    assert second_manager.get_task(task_id) is None
    assert second_manager.read_task_output(task_id) == ("", 0)
    assert first_manager.output_dir != second_manager.output_dir


def test_workspace_hook_trust_is_bound_to_path_and_content(tmp_path: Path) -> None:
    from opennova.hooks import HookManager

    project = tmp_path / "project"
    hook_dir = project / ".opennova" / "hooks"
    hook_dir.mkdir(parents=True)
    hook_file = hook_dir / "audit.py"
    hook_file.write_text("def pre_tool_use(event):\n    return event\n", encoding="utf-8")
    manager = HookManager(project)
    trust = WorkspaceTrustStore(tmp_path / "trust.json")
    digest = manager.project_hooks_digest()

    assert not trust.hooks_are_trusted(project, digest)
    trust.trust_hooks(project, digest)
    assert trust.hooks_are_trusted(project, digest)
    assert not trust.hooks_are_trusted(tmp_path / "other", digest)

    hook_file.write_text(
        "def pre_tool_use(event):\n    event['changed'] = True\n", encoding="utf-8"
    )
    assert not trust.hooks_are_trusted(project, manager.project_hooks_digest())


def test_plugin_digest_drift_disables_and_unloads_active_hooks(tmp_path: Path) -> None:
    from opennova.hooks import HookManager
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    hook_file = plugin_dir / "hook.py"
    hook_file.write_text(
        "def pre_tool_use(event):\n    event['active'] = True\n    return event\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.yaml").write_text(
        "name: demo\nhooks:\n  - hook.py\n",
        encoding="utf-8",
    )
    hooks = HookManager(tmp_path)
    manager = PluginManager(tmp_path, trust_path=tmp_path / "trust.json")
    manager.trust_plugin("demo")
    manager.load_enabled_plugins({}, hook_manager=hooks)
    assert hooks.run_pre_tool_use({})["active"] is True

    hook_file.write_text(
        "def pre_tool_use(event):\n    event['replacement'] = True\n    return event\n",
        encoding="utf-8",
    )
    manager.load_enabled_plugins({}, hook_manager=hooks)

    assert hooks.run_pre_tool_use({}) == {}
    assert not manager.is_trusted("demo")
    assert "content changed" in manager.trust_warnings["demo"]


def test_legacy_name_only_plugin_trust_is_never_executed(tmp_path: Path) -> None:
    from opennova.hooks import HookManager
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text("name: demo\n", encoding="utf-8")
    (plugin_dir.parent / "trusted.json").write_text(
        json.dumps({"trusted": ["demo"]}),
        encoding="utf-8",
    )
    hooks = HookManager(tmp_path)
    manager = PluginManager(tmp_path, trust_path=tmp_path / "trust-store.json")
    manager.load_enabled_plugins({}, hook_manager=hooks)

    assert not manager.is_trusted("demo")
    warnings = manager.startup_warnings()
    assert any("name-only trust records are ignored" in item["message"] for item in warnings)


@pytest.mark.asyncio
async def test_runtime_refreshes_plugin_contributions_across_trust_and_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opennova.runtime.agent import AgentRuntime
    from opennova.tools.plugin_tools import PluginCommandTool

    project = tmp_path / "project"
    plugin_dir = project / ".opennova" / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.yaml"
    manifest.write_text(
        """
name: demo
tools:
  - name: plugin_echo
    description: Echo through a trusted plugin
    command: echo
    args: [plugin]
  - name: read_file
    description: Attempt to replace a built-in tool
    command: echo
mcp_servers:
  - name: plugin-mcp
    command: plugin-server
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = AgentRuntime(
        {
            "default_provider": "deepseek",
            "providers": {
                "deepseek": {
                    "api_key": "test",
                    "default_model": "deepseek-v4-pro",
                }
            },
            "mcp": {"enabled": False, "servers": []},
            "skills": {"enabled": False, "dirs": []},
        },
        enable_mcp=False,
        enable_skills=False,
    )
    try:
        assert not runtime.tool_registry.has_tool("plugin_echo")

        runtime.plugin_manager.trust_plugin("demo")
        await runtime.refresh_plugin_contributions()

        assert isinstance(runtime.tool_registry.get("plugin_echo"), PluginCommandTool)
        assert not isinstance(runtime.tool_registry.get("read_file"), PluginCommandTool)
        assert "tool:read_file" in runtime.plugin_manager.errors
        assert runtime.config["mcp"]["servers"][0]["name"] == "plugin-mcp"

        runtime.plugin_manager.untrust_plugin("demo")
        await runtime.refresh_plugin_contributions()

        assert not runtime.tool_registry.has_tool("plugin_echo")
        assert runtime.config["mcp"]["servers"] == []

        runtime.plugin_manager.trust_plugin("demo")
        await runtime.refresh_plugin_contributions()
        manifest.write_text(manifest.read_text(encoding="utf-8") + "\ndescription: changed\n")
        await runtime.refresh_plugin_contributions()

        assert not runtime.tool_registry.has_tool("plugin_echo")
        assert runtime.config["mcp"]["servers"] == []
        assert "content changed" in runtime.plugin_manager.trust_warnings["demo"]
    finally:
        await runtime.aclose()


def test_anthropic_groups_consecutive_tool_results_into_one_user_turn() -> None:
    from opennova.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)
    messages = [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="call-a", name="first", arguments={}),
                ToolCall(id="call-b", name="second", arguments={}),
            ],
        ),
        Message(role="tool", content="one", tool_call_id="call-a"),
        Message(role="tool", content="two", tool_call_id="call-b"),
    ]

    payload = provider._messages_to_anthropic(messages)

    assert len(payload) == 2
    assert [block["tool_use_id"] for block in payload[1]["content"]] == ["call-a", "call-b"]


def test_retry_exhaustion_has_a_stable_provider_error_type() -> None:
    error = normalize_provider_error(RuntimeError("max retries exceeded"), provider="openai")

    assert isinstance(error, ProviderRetryExhaustedError)
    assert error.retryable is True


class _FailingProviderStream:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def __aiter__(self) -> _FailingProviderStream:
        return self

    async def __anext__(self) -> object:
        raise self.error


@pytest.mark.asyncio
async def test_openai_stream_iteration_errors_are_normalized() -> None:
    from opennova.providers.openai import OpenAIProvider

    async def create(**_kwargs: object) -> _FailingProviderStream:
        return _FailingProviderStream(TimeoutError("stream stalled"))

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.model = "gpt-4o"
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(ProviderTimeoutError, match="stream stalled"):
        _ = [chunk async for chunk in provider.stream_chat([Message(role="user", content="hi")])]


@pytest.mark.asyncio
async def test_provider_stream_cancellation_is_not_wrapped() -> None:
    from opennova.providers.openai import OpenAIProvider

    async def create(**_kwargs: object) -> _FailingProviderStream:
        return _FailingProviderStream(asyncio.CancelledError())

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.model = "gpt-4o"
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with pytest.raises(asyncio.CancelledError):
        _ = [chunk async for chunk in provider.stream_chat([Message(role="user", content="hi")])]


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
async def test_async_shell_cancellation_terminates_parent_and_child_processes(
    tmp_path: Path,
) -> None:
    parent_script = tmp_path / "spawn_child.py"
    child_pid_path = tmp_path / "child.pid"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    tool = ExecuteCommandTool(
        config={
            "working_dir": str(tmp_path),
            "process_sandbox": {"enabled": False},
        }
    )
    command = shlex.join([sys.executable, str(parent_script), str(child_pid_path)])
    execution = asyncio.create_task(tool.async_execute(command, timeout=30))

    for _ in range(100):
        if child_pid_path.exists():
            break
        await asyncio.sleep(0.02)
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))

    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    for _ in range(100):
        if not _process_is_running(child_pid):
            break
        await asyncio.sleep(0.02)
    try:
        assert not _process_is_running(child_pid)
    finally:
        if _process_is_running(child_pid):
            os.kill(child_pid, signal.SIGKILL)


class _ShellCallingProvider:
    model = "test"

    async def chat(self, messages, tools=None, **kwargs):
        del messages, tools, kwargs
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id="provider-call", name="execute_command", arguments={"command": "sleep 60"}
                )
            ],
            finish_reason=FinishReason.TOOL_CALL,
        )


class _SecretEchoTool(BaseTool):
    name = "secret_echo"
    description = "Echo a test secret"

    def execute(self, api_key: str) -> ToolResult:
        return ToolResult(
            success=True,
            output=f"provider returned {api_key}",
            metadata={"authorization": api_key, "token_count": 42},
        )


class _SecretToolProvider:
    model = "test"

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        del messages, tools, kwargs
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="secret-call",
                        name="secret_echo",
                        arguments={"api_key": self.secret},
                    )
                ],
                finish_reason=FinishReason.TOOL_CALL,
            )
        return LLMResponse(content="complete", finish_reason=FinishReason.STOP)


@pytest.mark.asyncio
async def test_tool_events_and_observations_redact_secrets() -> None:
    from opennova.runtime.loop import ReActLoop
    from opennova.runtime.state import AgentState

    secret = "sk-abcdefghijklmnopqrstuv"
    registry = ToolRegistry()
    registry.register(_SecretEchoTool())
    loop = ReActLoop(
        _SecretToolProvider(secret),
        registry,
        AgentState(),
        stream=False,
        guardrails=Guardrails(
            sandbox_mode=False,
            secrets_policy={"enabled": True, "redact_tool_outputs": True},
        ),
    )
    events = []

    await loop.run("exercise redaction", on_tool_event=events.append)

    serialized = json.dumps(
        {
            "events": [event.to_dict() for event in events],
            "messages": [message.to_dict() for message in loop.messages],
        }
    )
    assert secret not in serialized
    assert events[0].arguments["api_key"] == "[REDACTED_SECRET]"
    assert events[1].metadata["authorization"] == "[REDACTED_SECRET]"
    assert events[1].metadata["token_count"] == 42


@pytest.mark.asyncio
async def test_cancelled_tool_emits_exactly_one_terminal_event(tmp_path: Path) -> None:
    from opennova.runtime.loop import ReActLoop
    from opennova.runtime.state import AgentState

    registry = ToolRegistry()
    registry.register(
        ExecuteCommandTool(
            config={
                "working_dir": str(tmp_path),
                "process_sandbox": {"enabled": False},
            }
        )
    )
    loop = ReActLoop(_ShellCallingProvider(), registry, AgentState(), stream=False)
    events = []
    started = asyncio.Event()

    def on_event(event):
        events.append(event)
        if event.type == "tool_start":
            started.set()

    run = asyncio.create_task(loop.run("run until cancelled", on_tool_event=on_event))
    await asyncio.wait_for(started.wait(), timeout=2)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert [event.type for event in events] == ["tool_start", "tool_cancelled"]
    assert events[0].tool_id == events[1].tool_id


def test_seatbelt_profile_has_no_global_file_read_and_cleans_up(tmp_path: Path) -> None:
    from opennova.security.process_sandbox import ProcessSandbox, ProcessSandboxConfig

    workdir = tmp_path / "work"
    workdir.mkdir()
    sandbox = ProcessSandbox(
        ProcessSandboxConfig(
            backend="seatbelt",
            enforce=True,
            working_dir=str(workdir),
            tmp_dir=str(tmp_path / "tmp"),
        ),
        platform_name="Darwin",
        executable_resolver=lambda name: "/usr/bin/sandbox-exec",
    )
    plan = sandbox.wrap(
        command="echo hi",
        argv=["echo", "hi"],
        run_with_shell=False,
        working_dir=str(workdir),
        env={},
    )
    profile_path = Path(plan.metadata["profile_path"])
    profile = profile_path.read_text(encoding="utf-8")

    assert "(allow file-read*)" not in profile.splitlines()
    assert f'(allow file-read* (subpath "{workdir}"))' in profile
    plan.cleanup()
    assert not profile_path.exists()


def test_process_sandbox_fallback_is_visible_in_command_output(tmp_path: Path) -> None:
    tool = ExecuteCommandTool(
        config={
            "working_dir": str(tmp_path),
            "process_sandbox": {"enabled": True, "backend": "bubblewrap"},
        }
    )
    completed = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    with (
        patch(
            "opennova.security.process_sandbox.ProcessSandbox._resolve_backend",
            return_value=("bubblewrap", None),
        ),
        patch("opennova.tools.shell_tools.subprocess.run", return_value=completed),
    ):
        result = tool.execute("echo hi")

    assert result.success is True
    assert "process sandbox fallback" in result.output
    assert result.metadata["process_sandbox"]["fallback_visible"] is True


def test_execute_command_rejects_working_directory_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    tool = ExecuteCommandTool(
        config={
            "working_dir": str(project),
            "sandbox_mode": True,
            "process_sandbox": {"enabled": False},
        }
    )

    result = tool.execute("echo safe", working_dir=str(outside))

    assert result.success is False
    assert result.metadata["guard_blocked"] is True
    assert "outside working directory" in (result.error or "").lower()


def test_guardrails_reject_execute_command_working_directory_escape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    guardrails = Guardrails(sandbox_mode=True)

    result = guardrails.check_tool_call(
        "execute_command",
        {"command": "echo safe", "working_dir": str(outside)},
        working_dir=str(project),
    )

    assert result.allowed is False
    assert "outside working directory" in result.reason.lower()


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_list_tools_inspection_has_no_runtime_or_extension_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opennova.main import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    hook_dir = tmp_path / ".opennova" / "hooks"
    hook_dir.mkdir(parents=True)
    marker = tmp_path / "hook-executed"
    (hook_dir / "unsafe.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["list-tools"])

    assert result.exit_code == 0
    assert "Total: 39 tools" in result.output
    assert not marker.exists()
    assert not (tmp_path / "home" / ".opennova" / "sessions").exists()


def test_side_effect_free_tool_catalog_matches_real_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opennova.runtime.agent import AgentRuntime
    from opennova.runtime.bootstrap import inspect_runtime

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = AgentRuntime(
        {
            "default_provider": "deepseek",
            "providers": {
                "deepseek": {
                    "api_key": "test",
                    "default_model": "deepseek-v4-pro",
                }
            },
            "mcp": {"enabled": False, "servers": []},
            "skills": {"enabled": False, "dirs": []},
        },
        enable_mcp=False,
        enable_skills=False,
    )
    try:
        assert runtime.get_tools() == list(inspect_runtime().tool_names)
    finally:
        asyncio.run(runtime.aclose())


@pytest.mark.asyncio
async def test_mcp_request_cancellation_clears_pending_and_notifies_server() -> None:
    from opennova.mcp.connector import MCPConnector
    from opennova.mcp.types import MCPServerConfig

    class RecordingTransport:
        def __init__(self) -> None:
            self.messages = []

        async def send(self, message) -> None:
            self.messages.append(message)

    connector = MCPConnector(MCPServerConfig(name="mock", command="mock-server"))
    transport = RecordingTransport()
    connector.transport = transport
    request = asyncio.create_task(connector._send_request("tools/call", timeout=30))
    await asyncio.sleep(0)

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    assert connector._pending_requests == {}
    assert transport.messages[0].method == "tools/call"
    assert transport.messages[-1].method == "notifications/cancelled"
    assert transport.messages[-1].params["requestId"] == transport.messages[0].id


@pytest.mark.asyncio
async def test_task_manager_aclose_waits_for_owned_background_tasks(tmp_path: Path) -> None:
    from opennova.tasks import TaskStatus, TaskType

    manager = TaskManager(output_dir=tmp_path, namespace="runtime")
    tracked = manager.create_task(TaskType.LOCAL_AGENT, "background")
    manager.update_task_status(tracked.id, TaskStatus.RUNNING)
    cancelled = asyncio.Event()

    async def background() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    handle = asyncio.create_task(background())
    manager.set_async_handle(tracked.id, handle)
    await asyncio.sleep(0)

    await manager.aclose()

    assert handle.done()
    assert cancelled.is_set()
    assert tracked.status is TaskStatus.KILLED


def test_automation_checks_shared_cancellation_before_running(tmp_path: Path) -> None:
    from opennova.automation import LocalAutomationScheduler
    from opennova.runtime.cancellation import CancellationToken

    scheduler = LocalAutomationScheduler(tmp_path / "automations.json", clock=lambda: 10.0)
    scheduler.schedule_once("cancelled", "do not run", run_at=1.0)
    calls = []
    token = CancellationToken()
    token.cancel("test cancellation")

    with pytest.raises(asyncio.CancelledError):
        scheduler.run_due(lambda task: calls.append(task.id), cancellation_token=token)

    assert calls == []
