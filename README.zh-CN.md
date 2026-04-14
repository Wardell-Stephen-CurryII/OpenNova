# OpenNova

一个从零开始构建的轻量级 Python CLI AI 编码助手。

**简体中文** | **[English](README.md)**

**[快速开始](docs/QUICKSTART.md)** | **[完整教程](docs/TUTORIAL.md)** | **[API 文档](docs/API.md)**

## 概览

OpenNova 是一个运行在终端中的极简 AI 编码助手，设计目标是：
- **轻量**：不依赖 LangChain、CrewAI 等重型框架
- **灵活**：支持多个 LLM 提供商（OpenAI、Anthropic、DeepSeek）
- **可扩展**：基于插件的工具系统，并支持 Skill
- **安全**：内置危险操作防护和确认机制

## 功能特性

### 第一阶段 ✅
- ✅ ReAct（Reason-Act-Observe）推理循环
- ✅ 多模型提供商支持（OpenAI、Anthropic、DeepSeek）
- ✅ 流式输出，实时展示响应
- ✅ 内置工具：文件操作、Shell 命令
- ✅ 支持命令历史的交互式 REPL
- ✅ 配置管理（YAML + 环境变量）

### 第二阶段 ✅
- ✅ Diff/Patch 代码修改系统
- ✅ 支持任务拆解的计划模式
- ✅ Memory 与上下文管理（token 计数、工作/项目记忆）
- ✅ 安全护栏（危险命令检测、路径沙箱）
- ✅ 富终端渲染（语法高亮、diff 预览、进度条）

### 第三阶段 ✅
- ✅ MCP（Model Context Protocol）集成
- ✅ Skill 插件系统与自动发现
- ✅ 内置示例 Skills（代码审查、文档生成、Git 助手）
- ✅ 支持自定义工具的可扩展架构

## 安装

### 前置要求
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova

# 安装依赖
uv sync

# 初始化配置
uv run opennova init
```

### 配置

编辑 `~/.opennova/config.yaml`，或者直接设置环境变量：

```yaml
default_provider: openai
default_model: gpt-4o

providers:
  openai:
    api_key: ${OPENAI_API_KEY}
    default_model: gpt-4o

  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    default_model: claude-sonnet-4

  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
    default_model: deepseek-chat

agent:
  max_iterations: 20
  auto_confirm: false
  show_thinking: true

# MCP 服务配置
mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./src"]
    - name: github
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_PERSONAL_ACCESS_TOKEN: ${GITHUB_TOKEN}

# Skills 配置
skills:
  enabled: true
  dirs: []
  exclude: []
```

或者使用环境变量：

```bash
export OPENAI_API_KEY=your_key_here
export ANTHROPIC_API_KEY=your_key_here
export DEEPSEEK_API_KEY=your_key_here
```

## 使用方式

### 交互模式（REPL）

```bash
uv run opennova
```

### 单次任务模式

```bash
# 直接执行任务
uv run opennova run "Read the README.md file"

# 以计划模式运行
uv run opennova run --plan "Refactor the authentication module"

# 指定模型
uv run opennova run -m gpt-4o "Create a new Python module"
```

### REPL 命令

在交互式 REPL 中：

| 命令 | 说明 |
|------|------|
| `/plan <task>` | 先生成计划再执行 |
| `/act <task>` | 直接执行（默认模式） |
| `/tools` | 列出可用工具 |
| `/skills` | 列出已加载的 Skills |
| `/reload-skills` | 从磁盘重新加载 Skills |
| `/model` | 显示当前模型信息 |
| `/config` | 显示当前配置 |
| `/history [n]` | 显示最近的会话历史 |
| `/clear` | 清空当前会话状态 |
| `/help` | 显示帮助信息 |
| `/exit` | 退出 REPL |

## 内置工具

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件内容，可指定行范围 |
| `write_file` | 向文件写入内容 |
| `create_file` | 创建新文件 |
| `delete_file` | 删除文件（需要确认） |
| `list_directory` | 列出目录内容 |
| `execute_command` | 执行 Shell 命令 |

## 内置 Skills

OpenNova 内置了几个示例 Skill：

| Skill | 说明 |
|-------|------|
| `code_review` | 代码质量与最佳实践审查 |
| `generate_docs` | 生成文档或 docstring |
| `git_helper` | Git 命令辅助 |
| `analyze_project` | 分析项目结构 |

## 创建自定义 Skill

在 `~/.opennova/skills/my_skill.py` 中创建 Skill 文件：

```python
from opennova.skills.base import BaseSkill, SkillMetadata
from opennova.tools.base import ToolResult


class MySkill(BaseSkill):
    """My custom skill."""

    name = "my_skill"
    description = "Does something useful"

    metadata = SkillMetadata(
        name="my_skill",
        version="1.0.0",
        description="A custom skill",
        author="Your Name",
    )

    def execute(self, **kwargs) -> ToolResult:
        # Your skill logic here
        return ToolResult(success=True, output="Done!")
```

Skills 会从以下位置自动发现：
- `~/.opennova/skills/`
- `.opennova/skills/`
- 配置中指定的目录

## MCP 集成

OpenNova 支持通过 Model Context Protocol（MCP）服务器扩展能力：

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
```

支持的传输方式：
- **stdio**：启动子进程，通过 stdin/stdout 通信
- **sse**：通过 Server-Sent Events 连接

## 架构

```
opennova/
├── providers/         # LLM provider 实现
│   ├── base.py        # 抽象 provider 接口
│   ├── openai.py      # OpenAI GPT-4, o1 支持
│   ├── anthropic.py   # Claude 4, 3.5 支持
│   ├── deepseek.py    # DeepSeek 支持
│   └── factory.py     # Provider 工厂
├── tools/             # 工具系统
│   ├── base.py        # BaseTool 和 ToolRegistry
│   ├── file_tools.py  # 文件操作
│   └── shell_tools.py # Shell 命令
├── runtime/           # Agent 运行时
│   ├── state.py       # Agent 状态管理
│   ├── loop.py        # ReAct 循环
│   └── agent.py       # 主协调器
├── cli/               # CLI 界面
│   ├── repl.py        # 交互式 REPL
│   └── renderer.py    # Rich 终端渲染
├── diff/              # Diff/Patch 系统
│   ├── engine.py      # Diff 生成与应用
│   ├── parser.py      # LLM 输出解析
│   └── changeset.py   # 文件变更跟踪
├── memory/            # Memory 管理
│   ├── context.py     # 上下文窗口管理
│   ├── working.py     # 短期工作记忆
│   └── project.py     # 长期项目记忆
├── planning/          # 计划系统
│   ├── planner.py     # 任务拆解
│   └── models.py      # Plan 数据结构
├── security/          # 安全模块
│   ├── guardrails.py  # 安全检查
│   └── sandbox.py     # 路径沙箱
├── mcp/               # MCP 集成
│   ├── types.py       # MCP 数据类型
│   └── connector.py   # MCP 服务器连接
├── skills/            # Skills 系统
│   ├── base.py        # BaseSkill 和加载器
│   ├── registry.py    # Skill 管理
│   └── examples.py    # 示例 Skills
└── main.py            # 入口文件
```

## 安全特性

OpenNova 内置了多项安全机制：

- **危险命令检测**：拦截潜在破坏性的 Shell 命令
- **路径沙箱**：将文件操作限制在允许目录内
- **受保护路径**：阻止访问系统目录（如 `/etc`、`/usr` 等）
- **确认提示**：对高风险操作要求用户确认
- **敏感文件检测**：访问 `.env`、`.pem` 等文件时给出提醒

## 开发

```bash
# 运行测试
uv run pytest

# 运行带覆盖率的测试
uv run pytest --cov=opennova

# 类型检查
uv run mypy src/opennova

# 格式化代码
uv run ruff format src/

# Lint 检查
uv run ruff check src/
```

## 许可证

本项目基于 MIT License 发布，完整文本请见 [LICENSE](LICENSE)。

## 作者

Xingwang Lin ([@Wardell-Stephen-CurryII](https://github.com/Wardell-Stephen-CurryII))
