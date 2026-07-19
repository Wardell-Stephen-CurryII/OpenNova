# OpenNova 0.4.2 API 文档

**简体中文** | **[English](API.en.md)**

本文记录当前源码中可复用的 Python 接口。OpenNova 的用户交互界面是 Textual TUI；这里的 API 面向脚本、服务、插件和二次开发，不是旧交互式 CLI 的说明。

## 安装与导入

```bash
uv sync
```

```python
from opennova import OpenNovaClient, SDKEvent, __version__
from opennova.config import Config, load_config
```

包版本和 `pyproject.toml` 在 0.4.2 中保持一致。

## 推荐入口：OpenNovaClient

`OpenNovaClient` 是无界面、按 session 管理的高级接口。

```python
import asyncio

from opennova import OpenNovaClient
from opennova.config import load_config

async def main() -> None:
    async with OpenNovaClient(load_config()) as client:
        session_id = client.create_session()
        result = await client.submit_message(
            session_id,
            "检查当前项目的测试结构",
            mode="act",
            stream=True,
        )
        print(result)

asyncio.run(main())
```

### 会话方法

```python
session_id = client.create_session()
runtime = client.get_runtime(session_id)
active = client.list_sessions()
client.resume_session(persisted_session_id)
```

- `create_session() -> str`：创建隔离的 `AgentRuntime`
- `get_runtime(session_id) -> AgentRuntime`：取得当前进程中的运行时
- `list_sessions() -> list[dict]`：列出当前 client 管理的运行时，不等同于磁盘会话列表
- `resume_session(session_id) -> str`：加载磁盘会话，并继续使用原 session id
- `await cancel_run(session_id) -> bool`：取消并等待当前运行结束
- `await close_session(session_id) -> bool`：取消运行、关闭 runtime 并移除 session
- `await aclose()`：关闭 client 拥有的全部 session；推荐使用 `async with`

### 消息方法

```python
result = await client.submit_message(session_id, "任务", mode="act", stream=True)

async for event in client.stream_message(session_id, "任务"):
    print(event.type, event.data)
```

`stream_message()` 返回 `SDKEvent`，主要事件包括：

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

需要注册工具、回调或直接控制 Plan 时，可以使用底层运行时：

```python
from opennova.config import load_config
from opennova.runtime.agent import AgentRuntime

runtime = AgentRuntime(load_config())
runtime.register_callback("stream", lambda chunk: print(chunk.content, end=""))
result = await runtime.run("总结 README", mode="act", stream=True)
runtime.flush_session()
await runtime.aclose()
```

常用公开方法：

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

`register_callback()` 返回取消订阅函数。MCP 可以通过 `connect_mcp_servers()` 和 `disconnect_mcp_servers()` 显式管理；Act 模式也会按需连接已配置 server。

## 会话恢复数据

`AgentRuntime.resume_session()` 返回 `LoadedSession`。它不仅包含 LLM 上下文，还包含用于 TUI 重放和运行状态恢复的数据：

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

恢复后 `SessionManager` 会绑定原 session 文件，后续保存不会创建重复 session。

## 配置 API

```python
from opennova.config import Config, load_config, validate_config

config = load_config()
provider = config.get("default_provider")
config.set("security.permission_mode", "request")
errors = validate_config(config)
```

配置优先级：

1. `DEFAULT_CONFIG`
2. `~/.opennova/config.yaml`
3. `.opennova/config.yaml` 或显式路径
4. 环境变量展开

重要配置组为 `providers`、`agent.compression`、`session.persistence`、`security`、`mcp` 和 `skills`。

## Tool API

所有工具继承 `BaseTool` 并返回 `ToolResult`。

```python
from opennova.tools.base import BaseTool, ToolResult

class WordCountTool(BaseTool):
    name = "word_count"
    description = "Count whitespace-separated words."

    def execute(self, text: str) -> ToolResult:
        return ToolResult(success=True, output=str(len(text.split())))

runtime.register_tool(WordCountTool())
```

参数 JSON Schema 默认从 `execute()` 的类型标注生成，也可以覆盖 `get_parameters_schema()` 或 `get_schema()`。工具可以覆盖以下能力提示：

```python
tool.is_read_only(**args)
tool.is_destructive(**args)
tool.requires_permission(**args)
tool.is_concurrency_safe(**args)
tool.interrupt_behavior()
tool.is_open_world(**args)
```

`ToolRegistry` 提供 `register()`、`get()`、`list_tools()`、`list_names()`、`has_tool()` 和 `unregister()`。当前 `AgentRuntime` 默认注册 39 个内置工具，插件和 MCP 工具会在此基础上动态增加。

## 运行时工具事件

```python
from opennova.runtime.events import ToolEvent, ToolUseContext
```

`ToolEvent` 是 SDK 与 TUI 共用的规范事件，包含 `tool_id`、`tool_name`、参数、风险等级、耗时、输出、错误、diff 和 metadata。调用 `to_dict()` 可获得可序列化数据。

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

会话格式是 JSONL v2 快照加运行时事件。loader 兼容旧格式，并对旧的重复消息做尽力去重。列表按 `modified` 倒序排列，会话标题默认来自第一条用户消息的 20 字符片段。

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

Skill 支持用户级、项目级和配置目录，具备命名空间解析、用户/模型调用控制、参数替换、路径激活和使用排序。

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

当前连接实现支持 stdio 和 SSE。`TransportType.WEBSOCKET` 已定义，但配置校验会明确报告尚未支持。

## Security API

```python
from opennova.security.guardrails import Guardrails

guard = Guardrails(sandbox_mode=True, permission_mode="auto")
command_result = guard.check_command("git status")
path_result = guard.check_file_path("README.md", operation="read")
http_result = guard.check_http_request("https://example.com", method="GET")
tool_result = guard.check_tool_call("execute_command", {"command": "git status"})
```

`GuardResult` 包含是否允许、风险等级、原因和 metadata。`request`、`auto`、`full` 只控制审批策略，不能覆盖 hard block、显式 deny、网络/路径限制或 OS 进程沙箱。配置展示、工具事件、工具 observation 和 transcript 默认经过密钥脱敏。

## 扩展入口

- Provider：继承 `BaseLLMProvider`，实现普通与流式 completion，并在 `ProviderFactory` 注册
- Tool：继承 `BaseTool`，通过 `AgentRuntime.register_tool()` 注册
- Skill：新增 `<scope>/skills/<name>/SKILL.md`
- MCP：通过 `MCPServerConfig` 和 `MCPManager` 接入
- Plugin：在项目插件目录声明工具、slash command 和 hooks，并经过工作区路径、内容摘要和 lock 检查

这些 Python API 仍处于 `0.x` 阶段。集成代码应优先使用 `OpenNovaClient`、`SDKEvent`、`BaseTool` 和配置类型等明确入口，避免依赖带下划线的内部方法。
