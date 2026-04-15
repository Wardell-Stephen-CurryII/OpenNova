# OpenNova

OpenNova v0.2.0 是一个从零开始构建的轻量级 Python CLI AI 编码助手。

**简体中文** | **[English](README.md)**

**[快速开始](docs/QUICKSTART.md)** | **[完整教程](docs/TUTORIAL.md)** | **[API 文档](docs/API.md)**

## 概览

OpenNova 运行在终端中，用一个小而清晰的核心提供实用的编码 Agent 工作流：
- **多模型提供商运行时**：支持 OpenAI、Anthropic、DeepSeek
- **交互式 REPL**：支持 slash commands、历史记录和流式输出
- **Plan + Act 工作流**：复杂任务可先生成计划，再确认执行
- **工具与 Skill 扩展**：支持内置工具、用户自定义工具和插件式 Skill
- **MCP 集成**：可连接外部 MCP 工具服务器
- **安全护栏**：拦截危险命令和受保护路径访问

## v0.2.0 包含什么

0.2.0 版本标志着核心能力面已经补齐：
- ReAct 运行时、流式响应和工具执行
- REPL 中的计划模式与执行确认流程
- Diff/Patch 代码修改系统
- 上下文管理、工作记忆和项目记忆组件
- MCP stdio 与 SSE 传输支持
- Skill 自动发现和内置示例 Skills
- REPL 运行中的交互式用户问题
- 真实 HTTP 支持的 `web_fetch`

注意：`web_search` 已保留为工具接口，但当前 runtime 没有配置真实搜索后端时会明确返回“未配置”，不会伪造搜索结果。

## 安装

### 前置要求
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 本地开发方式

```bash
# 克隆仓库
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova

# 安装依赖到项目环境
uv sync

# 初始化配置
uv run opennova init
```

如果你希望安装成全局 CLI，也可以执行 `uv tool install .`，之后直接使用 `opennova`。下面的示例默认使用 `uv run opennova ...`，确保命令始终运行当前仓库代码。

## 配置

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

mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./src"]

skills:
  enabled: true
  dirs: []
  exclude: []
```

也可以使用环境变量：

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
| `/plan <task>` | 生成计划，展示后询问是否立即执行 |
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

OpenNova 当前内置的工具面比早期版本更完整：

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件内容，可指定行范围 |
| `write_file` | 向文件写入内容 |
| `create_file` | 创建新文件 |
| `delete_file` | 删除文件，并进行确认 |
| `list_directory` | 列出目录内容 |
| `execute_command` | 通过安全护栏执行 Shell 命令 |
| `ask_user_question` | 在运行过程中向用户询问 2-4 个选项的问题 |
| `web_fetch` | 获取真实 HTTP/HTTPS 页面并返回提取后的内容 |
| `web_search` | 搜索接口占位；没有后端时会明确返回未配置 |

## 内置 Skills

OpenNova 内置了几个示例 Skill：

| Skill | 说明 |
|-------|------|
| `code_review` | 代码质量与最佳实践审查 |
| `generate_docs` | 生成文档或 docstring |
| `git_helper` | Git 命令辅助 |
| `analyze_project` | 分析项目结构 |

## 创建自定义 Skill

现在的自定义 Skill 使用 Claude Code 风格的 markdown 技能包格式。创建目录 `~/.opennova/skills/my_skill/`，并在其中放入 `SKILL.md`：

```markdown
---
name: my_skill
description: 对目标文件或功能区域做总结。
when_to_use: 当用户需要可复用的项目分析提示词时使用。
allowed-tools: read_file, list_directory
arguments: [target]
argument-hint: <file-or-area>
---
仔细分析目标内容。

Target: $ARGUMENTS

请总结：
- 它的作用
- 关键风险
- 可能的扩展点
```

Skills 会按以下目录结构自动发现：
- `~/.opennova/skills/<skill-name>/SKILL.md`
- `.opennova/skills/<skill-name>/SKILL.md`
- 配置目录中同样的 `<skill-name>/SKILL.md` 结构

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
- **sse**：连接 HTTP SSE endpoint

## 架构

```text
opennova/
├── providers/         # LLM provider 实现
├── tools/             # 内置工具和工具注册表
├── runtime/           # Agent runtime、loop 和 state
├── cli/               # REPL 和终端渲染
├── diff/              # Diff/Patch 系统
├── memory/            # 上下文和记忆管理
├── planning/          # 计划数据结构和 planner
├── security/          # 安全护栏和路径沙箱
├── mcp/               # MCP transport 和 connector
├── skills/            # Skill 系统和示例
└── main.py            # CLI 入口
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
