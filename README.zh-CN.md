# OpenNova

OpenNova v0.3.0 是一个从零开始构建的轻量级 Python CLI AI 编码助手。

**简体中文** | **[English](README.md)**

**[快速开始](docs/QUICKSTART.md)** | **[完整教程](docs/TUTORIAL.md)** | **[API 文档](docs/API.md)**

## 概览

OpenNova 运行在终端中，用一个小而清晰的核心提供实用的编码 Agent 工作流：
- **多模型提供商运行时**：支持 OpenAI、Anthropic、DeepSeek
- **双界面**：prompt_toolkit REPL 和 Textual TUI 分屏聊天界面
- **会话管理**：保存、恢复和列出会话（JSONL 持久化）
- **上下文压缩**：长对话中 LLM 自动总结旧消息，保持上下文窗口在预算内
- **Plan + Act 工作流**：复杂任务可先生成计划，再确认执行
- **工具与 Skill 扩展**：支持内置工具、用户自定义工具和插件式 Skill（17 个内置工具）
- **MCP 集成**：可连接外部 MCP 工具服务器
- **安全护栏**：拦截危险命令和受保护路径访问

## v0.3.0 包含什么

0.3.0 版本新增了会话管理、上下文压缩和 Textual TUI：
- **会话管理**：`/resume <id>`、`/sessions` — 对话持久化到 JSONL 文件
- **上下文压缩**：当 token 使用率超过 55% 时，LLM 自动总结旧消息，保持长对话在预算内
- **Textual TUI**：分屏聊天界面，支持文本复制、历史导航和实时流式输出
- **17 个内置工具**：文件操作、Shell 执行、Git、任务跟踪、计划模式、子 Agent、Skill、Web
- ReAct 运行时、流式响应和工具执行
- REPL 和 TUI 中的计划模式与执行确认流程
- Diff/Patch 代码修改系统
- 上下文管理、工作记忆和项目记忆组件
- MCP stdio 与 SSE 传输支持
- Skill 自动发现和内置示例 Skills
- REPL 和 TUI 运行中的交互式用户问题
- 真实 HTTP 支持的 `web_fetch`

注意：`web_search` 已保留为工具接口，但当前 runtime 没有配置真实搜索后端时会明确返回”未配置”，不会伪造搜索结果。

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
default_provider: deepseek
default_model: deepseek-v4-pro

providers:
  openai:
    api_key: ${OPENAI_API_KEY}
    default_model: gpt-4o

  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    default_model: claude-sonnet-4

  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
    default_model: deepseek-v4-pro

agent:
  max_iterations: 20
  auto_confirm: false
  show_thinking: true
  compression:
    enabled: true
    threshold: 0.55
    keep_last_pairs: 6
    max_tool_result_tokens: 8000

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

### 交互模式

```bash
# 交互模式（默认使用 Textual TUI，包含 Windows 中文输入法兼容处理）
uv run opennova

# 显式使用 Textual TUI 模式（分屏聊天界面）
uv run opennova run --tui

# REPL 模式（prompt_toolkit）
uv run opennova run --no-tui
```

### 会话管理

```bash
# 恢复之前的会话
uv run opennova resume <session_id>

# 列出所有会话
uv run opennova sessions
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
| `/skill <name> [args]` | 直接调用指定的 Skill |
| `/reload-skills` | 从磁盘重新加载 Skills |
| `/model` | 显示当前模型信息 |
| `/init [--force]` | 生成或重建 `OPENNOVA.md` |
| `/config` | 显示当前配置 |
| `/permissions [tool allow\|deny\|ask]` | 查看或更新工具权限规则 |
| `/plugins [trust\|untrust\|test name\|lock\|drift\|audit [--policy strict]]` | 管理、锁定、校验和审计本地项目插件 |
| `/hooks` | 查看已加载 hooks |
| `/automations` | 查看本地自动化任务 |
| `/automations once <name> <run_at> <prompt>` | 创建一次性本地自动化任务 |
| `/automations interval <name> <seconds> <prompt>` | 创建周期本地自动化任务 |
| `/automations pause\|resume\|delete\|run-now <id>` | 管理本地自动化任务 |
| `/automations daemon start\|stop\|status\|tick\|run` | 控制本地 automation daemon |
| `/diagnostics [path]` | 运行 Python 诊断 |
| `/status` | 查看运行时状态 |
| `/todos` | 查看 TodoWrite 任务板 |
| `/checkpoint` | 查看 checkpoint/rollback 状态 |
| `/checkpoint list\|diff\|restore [--preview] <id>` | 列出、预览或恢复 checkpoint 快照 |
| `/checkpoint diff --from-transcript <path> <id>` | 从导出的 transcript 反查 checkpoint diff |
| `write_file` checkpoint metadata | 覆盖已有文件时会自动创建 checkpoint 并返回 `checkpoint_id` |
| `edit_file` checkpoint metadata | edit 和 multi-edit 操作也会为已有文件创建可恢复 checkpoint |
| `/export [dir]` | 导出当前 transcript 为 Markdown，并包含工具 checkpoint/diff 详情 |
| automation retry/archive | 本地 daemon retry 事件可通过注入 callback 归档 |
| automation backoff/archive summary | 提供 retry delay 和 archive 摘要能力 |
| transcript checkpoint lookup | 导出的 transcript 可按 `checkpoint_id` 建索引用于后续 diff 反查 |
| diagnostics events | diagnostics、hover、definition、references 可包装成统一事件 payload |
| `/history [n]` | 显示最近的会话历史 |
| `/resume <id>` | 恢复之前的会话 |
| `/sessions` | 列出已保存的会话 |
| `/clear` | 清空当前会话状态 |
| `/help` | 显示帮助信息 |
| `/exit` | 退出 |

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
| `git_commit` | 创建 git 提交 |
| `git_status` | 显示工作树状态 |
| `git_diff` | 显示提交或工作树之间的差异 |
| `git_log` | 显示提交历史 |
| `git_branch` | 列出或管理分支 |
| `task_create` | 在任务列表中创建新任务 |
| `task_list` | 列出所有跟踪的任务 |
| `task_get` | 按 ID 获取任务详情 |
| `task_update` | 更新任务状态或属性 |
| `todo_write` | 替换当前结构化任务板 |
| `glob_files` | 按 glob 模式搜索文件 |
| `grep_code` | 搜索代码内容 |
| `python_diagnostics` | 运行 Python 语法诊断 |
| `python_symbols` | 列出 Python 符号及 qualified name |
| `python_definition` | 查找 Python 符号定义 |
| `python_references` | 查找 Python 符号引用 |
| `task_stop` | 停止正在运行的后台任务 |
| `task_output` | 获取已完成任务的输出 |
| `enter_plan_mode` | 进入计划模式进行架构设计 |
| `exit_plan_mode` | 计划批准后退出计划模式 |
| `agent` | 将工作委托给子 Agent |
| `send_message` | 向运行中的子 Agent 发送消息 |
| `skill` | 按名称调用已加载的 Skill |
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

## 上下文压缩

当 token 使用量超过模型上下文窗口的 55% 时，OpenNova 会自动压缩对话上下文：

- **LLM 驱动的总结**：使用当前 LLM 提供商将旧消息总结为简洁的段落
- **安全的切割点**：压缩永远不会拆分不完整的 assistant+tool 配对
- **会话持久化**：压缩标记保存到 JSONL，支持紧凑的会话恢复
- **工具结果截断**：大型工具输出（>8000 tokens）会被截断（保留头 20% + 尾 80%）
- **可配置**：可在配置中调整阈值、保留轮次数和截断限制

恢复会话时，只会加载最后一个压缩边界之后的消息 — 更早的上下文由摘要替代。

## 会话管理

对话会自动持久化到 `~/.opennova/sessions/` 作为 JSONL 文件：

```bash
# 在 REPL 或 TUI 中
/resume <session_id>   # 恢复之前的会话
/sessions              # 列出所有已保存的会话
```

每个会话文件记录了每条消息、工具调用和压缩边界。恢复时，压缩标记使 Agent 能够紧凑地恢复上下文。

## 架构

```text
opennova/
├── providers/         # LLM provider 实现
├── tools/             # 内置工具和工具注册表（17 个工具）
├── runtime/           # Agent runtime、loop 和 state
├── cli/               # REPL（prompt_toolkit）和 TUI（Textual）
├── diff/              # Diff/Patch 系统
├── memory/            # 上下文管理、压缩、工作/项目记忆
├── planning/          # 计划数据结构和 planner
├── security/          # 安全护栏和路径沙箱
├── session/           # 会话持久化（JSONL）
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
- **持久化权限规则**：支持项目级 allow/deny/ask 工具策略
- **敏感文件检测**：访问 `.env`、`.pem` 等文件时给出提醒

## 开发

```bash
# 运行测试
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest

# 运行带覆盖率的测试
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest --cov=opennova

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
