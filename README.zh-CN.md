# OpenNova

OpenNova v0.4.1 是一个基于 Python 和 Textual TUI 构建的终端 AI 编码 Agent。

**简体中文** | **[English](README.md)**

**[快速开始](docs/QUICKSTART.md)** | **[完整教程](docs/TUTORIAL.md)** | **[API 文档](docs/API.md)**

## 概览

OpenNova 将 Agent 运行时和全屏终端工作台组合在一起：

- 支持 OpenAI、Anthropic 和 DeepSeek
- 流式 Textual TUI，包含 Chat、Context、Tasks 和 Activity 面板
- 会话持久化、选择器恢复和完整对话记录重放
- Plan/Act、TodoWrite、子 Agent 和 Git Worktree 工作流
- 39 个内置工具，以及 Skills、可信项目插件、Hooks 和 MCP 扩展
- 上下文压缩和分层项目记忆
- 三档权限、参数规则、敏感信息脱敏、审计日志和沙箱
- 可用于脚本和服务的无界面 Python SDK

旧的交互式命令行界面和独立 `opennova tui` 命令已不再使用。直接运行 `opennova` 即可进入 Textual TUI；命令参数仍用于初始化、检查和单次非交互任务。

## v0.4.1 更新

0.4.1 将全部文档和版本元数据同步到当前实现：

- 明确 Textual TUI 是唯一交互界面
- 删除旧的 `opennova tui`、17 个工具和复制弹窗描述
- 补充消息区原地选区复制及系统剪贴板行为
- 补充会话选择器、完整记录恢复和继承原会话机制
- 更新命令、slash command、安全、SDK 与架构说明

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova
uv sync
uv run opennova init
```

安装为全局命令：

```bash
uv tool install .
opennova
```

## 配置

配置覆盖顺序为：默认配置、`~/.opennova/config.yaml`、项目 `.opennova/config.yaml`、环境变量。

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
    base_url: https://api.deepseek.com/v1
    default_model: deepseek-v4-pro

agent:
  max_iterations: 20
  compression:
    enabled: true
    threshold: 0.55
    keep_last_pairs: 6
    max_tool_result_tokens: 8000

security:
  permission_mode: auto  # request | auto | full
  sandbox_mode: true
  allow_network: true
  strict_shell_parsing: false
  read_only: false

mcp:
  enabled: true
  servers: []

skills:
  enabled: true
  dirs: []
  exclude: []
```

API Key 也可以通过 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 和 `DEEPSEEK_API_KEY` 提供。

## 使用方式

```bash
# 打开 Textual TUI
uv run opennova

# 打开会话选择器
uv run opennova --resume

# 直接继续最近会话
uv run opennova --continue

# 选择本次运行的审批模式
uv run opennova --permission-mode request

# 不打开 TUI，执行单次任务
uv run opennova run "读取 README.md"

# 单次任务使用计划模式
uv run opennova run --plan "重构认证模块"

# 指定 provider 或模型
uv run opennova run --provider deepseek -m deepseek-v4-pro "检查 src/"
```

其他初始化与检查命令包括 `opennova init`、`opennova list-tools`、`opennova config` 和 `opennova --version`。

## TUI 操作

| 操作 | 功能 |
|---|---|
| `Enter` | 提交输入 |
| `Shift+Enter` | 输入换行 |
| `Ctrl+C` | 取消正在运行的任务 |
| `Ctrl+Shift+C` | 复制消息区选中的文字 |
| `Cmd+C` | 在能传递该按键的 macOS 终端中复制选区 |
| 鼠标拖动 | 直接在消息区选择文字 |
| `Tab` / `Shift+Tab` | 切换焦点 |

复制时会先尝试 Textual/OSC 52，再按系统调用 `pbcopy`、`clip`、`wl-copy` 或 `xclip`。

## Slash Commands

TUI 内主要命令如下：

| 命令 | 用途 |
|---|---|
| `/act <task>` | 直接执行任务 |
| `/plan <task>` | 生成计划并请求确认 |
| `/tools`、`/skills`、`/skill <name> [args]` | 查看工具或调用 Skill |
| `/init [--force]` | 生成或重建 `OPENNOVA.md` |
| `/resume [id]`、`/sessions` | 选择或查看持久化会话 |
| `/permissions ...` | 查看或修改权限模式和规则 |
| `/plugins ...`、`/hooks` | 管理可信项目扩展 |
| `/automations ...` | 管理本地计划任务与 daemon |
| `/diagnostics [path]` | 运行 Python 诊断 |
| `/todos`、`/status` | 查看运行状态 |
| `/checkpoint ...` | 查看、预览、比较或恢复检查点 |
| `/export [dir]` | 导出当前 Markdown 对话记录 |
| `/history [n]`、`/clear`、`/help`、`/exit` | 管理当前 TUI 会话 |

以当前安装版本的 `/help` 输出为最终准确信息。

## 内置能力

OpenNova 当前注册 39 个内置工具，分为：

- 文件：读取、写入、创建、编辑、批量编辑、删除和目录浏览
- 搜索与诊断：Glob、Grep、Python 语法、符号、定义和引用
- Shell 与 Git：受保护的命令执行、status、diff、log、branch 和 commit
- 任务：后台任务、TodoWrite、计划、子 Agent 和用户提问
- 集成：Skills、Web、项目指南、MCP 资源和 Worktree

`web_search` 在未配置搜索后端时会明确返回不可用，不会伪造搜索结果。

## 会话与记忆

会话保存在 `~/.opennova/sessions/`。`--resume` 和 `/resume` 会打开按时间倒序排列的选择器，会话标题来自第一条用户消息。恢复时会同时恢复后台上下文和消息区记录，并继续写入原会话，不会创建重复会话。

上下文默认在使用率达到 55% 时压缩。旧的完整消息对会被总结，最近消息、工具调用边界和压缩标记仍可恢复。

## 扩展机制

Skills 使用目录式 `SKILL.md`：

```text
~/.opennova/skills/<name>/SKILL.md
.opennova/skills/<name>/SKILL.md
```

MCP 支持 stdio 和 SSE。项目插件可以增加可信工具与 slash command，并通过 `/plugins` 完成锁定、漂移检查、警告和审计。

## 安全模型

- `request`：每个允许的工具调用都请求确认
- `auto`：安全调用自动执行，风险调用请求确认
- `full`：跳过审批弹窗，但不会绕过硬性限制

无论使用哪种模式，hard block、显式 deny、Plan 审批、路径/网络策略、敏感信息保护和可选进程沙箱都继续生效。

## Python SDK

```python
import asyncio

from opennova import OpenNovaClient
from opennova.config import load_config

async def main() -> None:
    client = OpenNovaClient(load_config())
    session_id = client.create_session()
    result = await client.submit_message(session_id, "总结这个项目")
    print(result)

asyncio.run(main())
```

## 开发

```bash
uv sync --dev
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest
uv run ruff check src/ tests/
uv run mypy src/opennova
```

当前架构和贡献流程见 [AGENTS.md](AGENTS.md)。`docs/develop/` 保存的是历史实施计划，只作为设计记录，不代表当前用户用法。

## 许可证

OpenNova 使用 [MIT License](LICENSE)。
