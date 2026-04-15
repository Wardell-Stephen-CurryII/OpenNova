# OpenNova API 参考

本文档描述 OpenNova v0.2.0 的核心 API 和主要扩展点。

> 说明：OpenNova 仍处于 Alpha 阶段，部分 API 仍可能演进；本文更适合作为当前代码结构与扩展入口的实用参考。

## 核心 API

### AgentRuntime

主运行时类，负责组装 LLM、工具、技能、上下文、记忆和执行循环。

```python
from opennova.runtime.agent import AgentRuntime
from opennova.config import load_config

config = load_config()
agent = AgentRuntime(config)

result = await agent.run("读取 README.md", mode="act")
tools = agent.get_tools()
skills = agent.get_skills()
```

常见职责：
- 注册内置工具和已加载技能
- 管理回调（stream、plan、interaction 等）
- 在 act / plan 模式下驱动运行时
- 暴露当前工具、技能和模型信息

### ReActLoop

`opennova.runtime.loop.ReActLoop` 是实际执行推理-行动-观察循环的核心。它负责：
- 构造发送给模型的消息
- 执行工具调用
- 处理流式输出
- 在工具请求用户交互时走 interaction callback
- 更新运行状态与消息历史

### ToolRegistry

工具注册表，管理所有可用工具。

```python
from opennova.tools.base import ToolRegistry

registry = ToolRegistry()
registry.register(my_tool)
tool = registry.get("read_file")
tools = registry.list_tools()
```

### BaseTool / ToolResult

自定义工具的基础抽象。

```python
from opennova.tools.base import BaseTool, ToolResult

class MyTool(BaseTool):
    name = "my_tool"
    description = "工具描述"

    def execute(self, arg1: str, arg2: int = 10) -> ToolResult:
        try:
            result = do_something(arg1, arg2)
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
```

`ToolResult` 通常包含：
- `success`
- `output`
- `error`
- `metadata`

其中 `metadata` 既可用于结构化结果，也可用于像 `ask_user_question` 这样的交互契约。

## 内置工具概览

当前内置工具主要分布在这些模块：
- `opennova.tools.file_tools`
- `opennova.tools.shell_tools`
- `opennova.tools.web_tools`
- `opennova.tools.ask_question_tool`

典型工具面包括：
- `read_file` / `write_file` / `create_file` / `delete_file`
- `list_directory`
- `execute_command`
- `ask_user_question`
- `web_fetch`
- `web_search`

说明：
- `web_fetch` 会发起真实 HTTP 请求。
- `web_search` 如果没有配置后端，会明确返回未配置错误。
- `ask_user_question` 在 REPL 环境下可通过 interaction callback 收集用户选择。

## Provider API

### BaseLLMProvider

LLM 提供商抽象基类。

```python
from opennova.providers.base import BaseLLMProvider, Message

class MyProvider(BaseLLMProvider):
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs,
    ) -> LLMResponse:
        pass

    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        pass
```

### ProviderFactory

提供商工厂类。

```python
from opennova.providers.factory import ProviderFactory

provider = ProviderFactory.create_provider(config)
ProviderFactory.register_provider("my_provider", MyProvider)
```

## Skills API

### SkillLoader

发现并解析目录形式的 markdown skills。

```python
from opennova.skills.base import SkillLoader

skill_files = SkillLoader.discover_skills(["/path/to/skills"])
loaded = SkillLoader.load_skill_file("/path/to/skills/my_skill/SKILL.md")
all_skills = SkillLoader.load_all_skills(["/path/to/skills"])
```

Skills 目录结构为：
- `~/.opennova/skills/<skill-name>/SKILL.md`
- `.opennova/skills/<skill-name>/SKILL.md`
- 其他配置目录下相同的 `<skill-name>/SKILL.md` 结构

### SkillRegistry

markdown skill 的加载、启停与提示词物化入口。

```python
from opennova.skills.registry import SkillRegistry

registry = SkillRegistry()
registry.load_all(directories=["/path/to/skills"], excluded=["disabled_skill"])
registry.enable_skill("my_skill")
registry.disable_skill("my_skill")
prompt = registry.materialize_skill_prompt("my_skill", "src/main.py")
info = registry.get_skill_info("my_skill")
```

## MCP API

### MCPServerConfig

MCP 服务器配置。

```python
from opennova.mcp.types import MCPServerConfig, TransportType

config = MCPServerConfig(
    name="my_server",
    transport=TransportType.STDIO,
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/path"],
    env={"API_KEY": "xxx"},
)
```

### MCPManager / MCPConnector

MCP 连接管理和单连接封装。

```python
from opennova.mcp.connector import MCPManager

manager = MCPManager(tool_registry)
await manager.add_server(config)
results = await manager.connect_all([config1, config2])
await manager.disconnect_all()
```

MCP 相关模块负责：
- 建立 stdio / SSE 连接
- 初始化 MCP 会话
- 发现服务器工具
- 将 MCP 工具包装成 OpenNova 工具

## Memory API

### ContextManager

上下文管理器。

```python
from opennova.memory.context import ContextManager

ctx = ContextManager(model="gpt-4o")
ctx.add_user_message("Hello")
ctx.add_assistant_message("Hi there!")
stats = ctx.get_stats()
messages = ctx.get_messages_for_llm()
```

### WorkingMemory

工作记忆，用于记录任务过程中的操作与观察。

```python
from opennova.memory.working import WorkingMemory

memory = WorkingMemory(task="任务描述")
action = memory.record_action("read_file", {"path": "test.py"})
memory.update_action(action.id, ActionStatus.SUCCESS, "内容")
memory.observe_file("test.py", "read", "预览内容")
summary = memory.get_summary()
```

## Planning API

计划相关结构主要位于：
- `opennova.runtime.state`
- `opennova.planning.models`
- `opennova.planning.planner`

运行时中的计划能力通常包括：
- 生成结构化步骤
- 跟踪步骤状态
- 在 REPL 中展示计划
- 等待用户确认后继续执行

## Security API

### Guardrails

安全检查器。

```python
from opennova.security.guardrails import Guardrails

guard = Guardrails(sandbox_mode=True)
result = guard.check_command("rm -rf /")
print(result.allowed)
print(result.risk_level)
```

### Sandbox

执行沙盒。

```python
from opennova.security.sandbox import Sandbox, SandboxConfig

config = SandboxConfig(
    working_dir="/project",
    read_only=False,
    max_file_size=10 * 1024 * 1024,
)

sandbox = Sandbox(config)
success, content = sandbox.safe_read("test.txt")
success, msg = sandbox.safe_write("output.txt", b"data")
sandbox.rollback()
```

## Diff API

### DiffEngine

Diff 生成和应用。

```python
from opennova.diff.engine import DiffEngine

engine = DiffEngine()
diff = engine.generate_diff(original, modified, "file.py")
result = engine.apply_patch("file.py", diff, backup=True)
preview = engine.preview_diff(diff)
```

### ChangeSet

文件变更集合，用于跟踪一次任务中的修改。

```python
from opennova.diff.changeset import ChangeSet

changeset = ChangeSet(task="重构")
```

## 扩展建议

如果你要为 OpenNova 增加新能力，通常路径如下：
1. 新工具：继承 `BaseTool`，返回 `ToolResult`
2. 新 Skill：新增 `~/.opennova/skills/<skill-name>/SKILL.md` 或项目级 `<skill-name>/SKILL.md`
3. 新 Provider：实现 `BaseLLMProvider`，接入 `ProviderFactory`
4. 新 MCP 集成：通过 `MCPServerConfig` 与 connector 接入

## 参考源码入口

- `src/opennova/runtime/agent.py`
- `src/opennova/runtime/loop.py`
- `src/opennova/tools/base.py`
- `src/opennova/tools/file_tools.py`
- `src/opennova/tools/shell_tools.py`
- `src/opennova/tools/web_tools.py`
- `src/opennova/tools/ask_question_tool.py`
- `src/opennova/mcp/connector.py`
- `src/opennova/skills/base.py`
