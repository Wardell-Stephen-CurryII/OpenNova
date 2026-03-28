# OpenNova API 参考

本文档描述 OpenNova 的核心 API 和扩展点。

## 核心 API

### AgentRuntime

主运行时类，管理 agent 的生命周期。

```python
from opennova.runtime.agent import AgentRuntime
from opennova.config import load_config

# 加载配置
config = load_config()

# 创建 agent
agent = AgentRuntime(config)

# 运行任务
result = await agent.run("读取 README.md", mode="act")

# 获取工具列表
tools = agent.get_tools()

# 获取技能列表
skills = agent.get_skills()
```

### ToolRegistry

工具注册表，管理所有可用工具。

```python
from opennova.tools.base import ToolRegistry, BaseTool, ToolResult

# 获取单例
registry = ToolRegistry()

# 注册工具
registry.register(my_tool)

# 获取工具
tool = registry.get("read_file")

# 列出所有工具
tools = registry.list_tools()
```

### BaseTool

自定义工具基类。

```python
from opennova.tools.base import BaseTool, ToolResult

class MyTool(BaseTool):
    name = "my_tool"
    description = "工具描述"
    
    def execute(self, arg1: str, arg2: int = 10) -> ToolResult:
        try:
            # 工具逻辑
            result = do_something(arg1, arg2)
            return ToolResult(
                success=True,
                output=result,
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=str(e),
            )
```

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
        **kwargs
    ) -> LLMResponse:
        # 实现聊天逻辑
        pass
    
    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        # 实现流式输出
        pass
```

### ProviderFactory

提供商工厂类。

```python
from opennova.providers.factory import ProviderFactory

# 创建 provider
provider = ProviderFactory.create_provider(config)

# 注册自定义 provider
ProviderFactory.register_provider("my_provider", MyProvider)
```

## Skills API

### BaseSkill

自定义技能基类。

```python
from opennova.skills.base import BaseSkill, SkillMetadata
from opennova.tools.base import ToolResult

class MySkill(BaseSkill):
    name = "my_skill"
    description = "技能描述"
    
    metadata = SkillMetadata(
        name="my_skill",
        version="1.0.0",
        description="技能描述",
        author="你的名字",
        tags=["tag1", "tag2"],
    )
    
    def execute(self, **kwargs) -> ToolResult:
        # 实现技能逻辑
        return ToolResult(success=True, output="完成")
```

### SkillRegistry

技能注册表。

```python
from opennova.skills.registry import SkillRegistry

registry = SkillRegistry()

# 从目录加载
registry.load_from_dirs(["/path/to/skills"])

# 注册技能
registry.register(my_skill)

# 启用/禁用
registry.enable_skill("my_skill")
registry.disable_skill("my_skill")
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

### MCPManager

MCP 连接管理器。

```python
from opennova.mcp.connector import MCPManager

manager = MCPManager(tool_registry)

# 添加服务器
await manager.add_server(config)

# 连接所有服务器
results = await manager.connect_all([config1, config2])

# 断开连接
await manager.disconnect_all()
```

## Memory API

### ContextManager

上下文管理器。

```python
from opennova.memory.context import ContextManager

ctx = ContextManager(model="gpt-4o")

# 添加消息
ctx.add_user_message("Hello")
ctx.add_assistant_message("Hi there!")

# 获取统计
stats = ctx.get_stats()
print(f"Tokens: {stats.total_tokens}/{ctx.context_window}")

# 获取 LLM 格式消息
messages = ctx.get_messages_for_llm()
```

### WorkingMemory

工作记忆。

```python
from opennova.memory.working import WorkingMemory

memory = WorkingMemory(task="任务描述")

# 记录操作
action = memory.record_action("read_file", {"path": "test.py"})
memory.update_action(action.id, ActionStatus.SUCCESS, "内容")

# 观察文件
memory.observe_file("test.py", "read", "预览内容")

# 获取摘要
summary = memory.get_summary()
```

## Security API

### Guardrails

安全检查器。

```python
from opennova.security.guardrails import Guardrails

guard = Guardrails(sandbox_mode=True)

# 检查命令
result = guard.check_command("rm -rf /")
print(result.allowed)  # False
print(result.risk_level)  # RiskLevel.BLOCK

# 检查路径
result = guard.check_file_path("/etc/passwd", "read")
print(result.allowed)  # False
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

# 安全读写
success, content = sandbox.safe_read("test.txt")
success, msg = sandbox.safe_write("output.txt", b"data")

# 回滚
sandbox.rollback()
```

## Diff API

### DiffEngine

Diff 生成和应用。

```python
from opennova.diff.engine import DiffEngine

engine = DiffEngine()

# 生成 diff
diff = engine.generate_diff(original, modified, "file.py")

# 应用 patch
result = engine.apply_patch("file.py", diff, backup=True)

# 预览
preview = engine.preview_diff(diff)
```

### ChangeSet

文件变更集合。

```python
from opennova.diff.changeset import ChangeSet

changeset = ChangeSet(task="重构")

# 添加变更
changeset.add_new_file("new.py", "content")
changeset.add_modification("old.py", original, new)

# 应用
result = changeset.apply(backup=True)
```

## Planning API

### Planner

任务规划器。

```python
from opennova.planning.planner import Planner

planner = Planner(llm_provider)

# 创建计划
plan = await planner.create_plan("重构认证模块")

# 获取下一步
step = plan.get_next_step()

# 更新状态
plan.mark_step_done(step.id, "完成")
```
