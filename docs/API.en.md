# OpenNova 0.4.2 API Reference

**[简体中文](API.md)** | **English**

This document describes the reusable Python interfaces in the current source tree. OpenNova's
user interface is the Textual TUI; these APIs are intended for scripts, services, plugins, and
custom integrations, not for the removed legacy interactive CLI.

## Installation and imports

```bash
uv sync
```

```python
from opennova import OpenNovaClient, SDKEvent, __version__
from opennova.config import Config, load_config
```

The package version and `pyproject.toml` are both set to 0.4.2.

## Recommended entry point: OpenNovaClient

`OpenNovaClient` is the high-level, headless interface for managing sessions.

```python
import asyncio

from opennova import OpenNovaClient
from opennova.config import load_config

async def main() -> None:
    async with OpenNovaClient(load_config()) as client:
        session_id = client.create_session()
        result = await client.submit_message(
            session_id,
            "Inspect the test structure in the current project",
            mode="act",
            stream=True,
        )
        print(result)

asyncio.run(main())
```

### Session methods

```python
session_id = client.create_session()
runtime = client.get_runtime(session_id)
active = client.list_sessions()
client.resume_session(persisted_session_id)
```

- `create_session() -> str`: Create an isolated `AgentRuntime`.
- `get_runtime(session_id) -> AgentRuntime`: Get an in-process runtime.
- `list_sessions() -> list[dict]`: List runtimes managed by the current client; this is not the
  same as listing sessions stored on disk.
- `resume_session(session_id) -> str`: Load a session from disk and continue using its original
  session id.
- `await cancel_run(session_id) -> bool`: Cancel and await the active run.
- `await close_session(session_id) -> bool`: Cancel the run, close its runtime, and remove it.
- `await aclose()`: Close every session owned by the client; prefer `async with`.

### Message methods

```python
result = await client.submit_message(session_id, "Task", mode="act", stream=True)

async for event in client.stream_message(session_id, "Task"):
    print(event.type, event.data)
```

`stream_message()` yields `SDKEvent` values. The main event types are:

- `run_start`
- `thought`
- `text_delta`
- `plan`
- `tool_start`
- `permission_request`
- `tool_result`
- `tool_error`
- `tool_cancelled`
- `run_cancelled`
- `run_complete`
- `run_error`

```python
payload = event.to_dict()
# {"type": ..., "session_id": ..., "data": {...}}
```

## AgentRuntime

Use the lower-level runtime when you need to register tools or callbacks, or control Plan mode
directly:

```python
from opennova.config import load_config
from opennova.runtime.agent import AgentRuntime

runtime = AgentRuntime(load_config())
runtime.register_callback("stream", lambda chunk: print(chunk.content, end=""))
result = await runtime.run("Summarize README", mode="act", stream=True)
runtime.flush_session()
await runtime.aclose()
```

Common public methods:

```python
await runtime.run(task, mode="act", stream=True)
await runtime.chat(message, stream=True)
await runtime.execute_approved_plan(stream=True)
runtime.cancel_run("Cancelled by host")
await runtime.aclose()
runtime.clear_conversation()
runtime.resume_session(session_id)
runtime.get_sessions()
runtime.get_tools()
runtime.get_skills()
runtime.get_model_info()
runtime.get_state()
runtime.set_permission_mode("auto")
runtime.register_tool(tool)
unsubscribe = runtime.register_callback("tool_event", callback)
```

`register_callback()` returns an unsubscribe function. MCP connections can be managed explicitly
with `connect_mcp_servers()` and `disconnect_mcp_servers()`; Act mode also connects configured
servers on demand.

## Restored session data

`AgentRuntime.resume_session()` returns a `LoadedSession`. In addition to the LLM context, it
contains the data required for TUI replay and runtime-state restoration:

```python
loaded = runtime.resume_session(session_id)
loaded.messages
loaded.compression_summary
loaded.transcript_events
loaded.plan_state
loaded.runtime_state
loaded.state_events
loaded.recovery_warnings
```

After restoration, `SessionManager` remains bound to the original session file, so subsequent
saves do not create a duplicate session.

## Configuration API

```python
from opennova.config import Config, load_config, validate_config

config = load_config()
provider = config.get("default_provider")
config.set("security.permission_mode", "request")
errors = validate_config(config)
```

Configuration precedence, from lowest to highest:

1. `DEFAULT_CONFIG`
2. `~/.opennova/config.yaml`
3. `.opennova/config.yaml` or an explicit path
4. Environment-variable expansion

The main configuration groups are `providers`, `agent.compression`, `session.persistence`,
`security`, `mcp`, and `skills`.

## Tool API

All tools inherit from `BaseTool` and return `ToolResult`.

```python
from opennova.tools.base import BaseTool, ToolResult

class WordCountTool(BaseTool):
    name = "word_count"
    description = "Count whitespace-separated words."

    def execute(self, text: str) -> ToolResult:
        return ToolResult(success=True, output=str(len(text.split())))

runtime.register_tool(WordCountTool())
```

By default, the parameter JSON Schema is generated from the type annotations on `execute()`. A
tool can override `get_parameters_schema()` or `get_schema()` instead. Tools can also override
the following capability hints:

```python
tool.is_read_only(**args)
tool.is_destructive(**args)
tool.requires_permission(**args)
tool.is_concurrency_safe(**args)
tool.interrupt_behavior()
tool.is_open_world(**args)
```

`ToolRegistry` provides `register()`, `get()`, `list_tools()`, `list_names()`, `has_tool()`, and
`unregister()`. `AgentRuntime` currently registers 39 built-in tools by default; plugins and MCP
tools are added dynamically.

## Runtime tool events

```python
from opennova.runtime.events import ToolEvent, ToolUseContext
```

`ToolEvent` is the canonical event shared by the SDK and TUI. It includes `tool_id`, `tool_name`,
arguments, risk level, duration, output, errors, diffs, and metadata. Call `to_dict()` to obtain a
serializable representation.

## SessionManager

```python
from opennova.session.manager import SessionManager

manager = SessionManager(project_path="/path/to/project")
session_id = manager.start_session()
manager.save_runtime_snapshot(messages, transcript_events=events)
sessions = manager.list_sessions()
loaded = manager.load_session_with_summary(session_id)
manager.resume_session(session_id)
```

The session format consists of a JSONL v2 snapshot followed by runtime events. The loader supports
legacy formats and makes a best-effort attempt to collapse duplicate legacy messages. Sessions are
listed by `modified` time in descending order, and the default title is a 20-character excerpt
from the first user message.

## Skill API

```python
from opennova.skills.registry import SkillRegistry

registry = SkillRegistry()
registry.load_all(directories=["/path/to/skills"], excluded=[])
registry.list_enabled_skills()
registry.resolve_skill_name("review")
materialized = registry.materialize_skill_prompt("review", "src/")
registry.activate_for_paths(["src/app.py"], cwd="/project")
```

Skills can be loaded from user, project, and configured directories. They support namespaced
resolution, user/model invocation controls, argument substitution, path-based activation, and
usage ranking.

## MCP API

```python
from opennova.mcp.connector import MCPManager
from opennova.mcp.types import MCPServerConfig, TransportType
from opennova.tools.base import ToolRegistry

server = MCPServerConfig(
    name="filesystem",
    transport=TransportType.STDIO,
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "./src"],
)

manager = MCPManager(ToolRegistry())
await manager.add_server(server)
resources = await manager.list_resources("filesystem")
await manager.disconnect_all()
```

The current connector implementation supports stdio and SSE. `TransportType.WEBSOCKET` is
defined, but configuration validation explicitly reports it as unsupported.

## Security API

```python
from opennova.security.guardrails import Guardrails

guard = Guardrails(sandbox_mode=True, permission_mode="auto")
command_result = guard.check_command("git status")
path_result = guard.check_file_path("README.md", operation="read")
http_result = guard.check_http_request("https://example.com", method="GET")
tool_result = guard.check_tool_call("execute_command", {"command": "git status"})
```

`GuardResult` contains the allow/deny decision, risk level, reason, and metadata. The `request`,
`auto`, and `full` modes control approval policy only; they cannot override hard blocks, explicit
deny rules, network/path restrictions, or the OS process sandbox.
Configuration display, tool events, tool observations, and transcripts redact detected secrets by
default.

## Extension points

- Provider: Subclass `BaseLLMProvider`, implement regular and streaming completions, and register
  it with `ProviderFactory`.
- Tool: Subclass `BaseTool` and register it through `AgentRuntime.register_tool()`.
- Skill: Add `<scope>/skills/<name>/SKILL.md`.
- MCP: Integrate through `MCPServerConfig` and `MCPManager`.
- Plugin: Declare tools, slash commands, and hooks in the project plugin directory and pass the
  workspace-path, content-digest, and lock checks.

These Python APIs are still in the `0.x` development phase. Integration code should prefer
explicit entry points such as `OpenNovaClient`, `SDKEvent`, `BaseTool`, and the configuration
types, and avoid relying on underscore-prefixed internal methods.
