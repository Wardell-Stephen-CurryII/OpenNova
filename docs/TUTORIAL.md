# OpenNova 用户安装和使用指南

本教程将帮助你快速上手当前版本的 OpenNova CLI AI Coding Agent。

---

## 目录

1. [环境准备](#1-环境准备)
2. [安装步骤](#2-安装步骤)
3. [配置 API Key](#3-配置-api-key)
4. [基本使用](#4-基本使用)
5. [高级功能](#5-高级功能)
6. [常见问题](#6-常见问题)

---

## 1. 环境准备

### 1.1 检查 Python 版本

OpenNova 需要 Python 3.11 或更高版本：

```bash
python3 --version
```

如果版本低于 3.11，请先安装：

**macOS (使用 Homebrew):**
```bash
brew install python@3.11
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-dev
```

**Windows:**
从 [python.org](https://www.python.org/downloads/) 下载安装。

### 1.2 安装 uv 包管理器

`uv` 是一个快速的 Python 包管理器：

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

验证安装：
```bash
uv --version
```

---

## 2. 安装步骤

### 2.1 克隆仓库

```bash
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova
```

### 2.2 安装依赖

```bash
uv sync
```

这会自动安装项目依赖，包括：
- `openai`
- `anthropic`
- `rich`
- `prompt-toolkit`
- `click`
- `httpx`
- 以及其他运行时依赖

### 2.3 初始化配置

```bash
uv run opennova init
```

如果你希望把它安装成全局命令，也可以额外执行：

```bash
uv tool install .
```

这样之后可直接使用 `opennova`。本文档默认仍使用 `uv run opennova ...`，确保命令对应当前源码目录。

---

## 3. 配置 API Key

你有两种方式配置 API Key。当前默认 provider 是 `deepseek`，默认模型是 `deepseek-v4-pro`。

### 方式一：环境变量（推荐）

```bash
# 在 ~/.zshrc 或 ~/.bashrc 中添加
export DEEPSEEK_API_KEY="sk-your-deepseek-api-key"

# 如果你计划切换 provider，也可以额外配置
export OPENAI_API_KEY="sk-your-openai-api-key"
export ANTHROPIC_API_KEY="sk-ant-your-anthropic-api-key"
```

然后重新加载配置：
```bash
source ~/.zshrc  # 或 source ~/.bashrc
```

### 方式二：编辑配置文件

```bash
nano ~/.opennova/config.yaml
```

修改为：
```yaml
default_provider: deepseek
default_model: deepseek-v4-pro

providers:
  openai:
    api_key: "sk-your-actual-openai-api-key"
    default_model: gpt-4o

  anthropic:
    api_key: "sk-ant-your-actual-anthropic-api-key"
    default_model: claude-sonnet-4

  deepseek:
    api_key: "sk-your-actual-deepseek-api-key"
    default_model: deepseek-v4-pro
```

### 获取 API Key

| 提供商 | 获取地址 |
|--------|----------|
| OpenAI | https://platform.openai.com/api-keys |
| Anthropic | https://console.anthropic.com/ |
| DeepSeek | https://platform.deepseek.com/ |

---

## 4. 基本使用

### 4.1 验证安装

```bash
uv run opennova --version
```

输出：
```text
OpenNova v0.2.3
```

### 4.2 交互式模式

启动交互式会话：

```bash
uv run opennova
```

默认交互模式会进入 Textual TUI。Windows 上会使用专门的 TUI 输入驱动，以兼容中文输入法提交的 Unicode 字符。
如果你想显式使用经典 REPL，可以执行：

```bash
uv run opennova run --no-tui
```

如果你想显式进入 Textual TUI，可以执行：

```bash
uv run opennova run --tui
```

你会看到类似欢迎界面：
```text
╭────────────────────────────────────────╮
│ OpenNova - AI Coding Agent            │
│ Type /help for commands, Ctrl+D to exit│
╰────────────────────────────────────────╯

opennova>
```

### 4.3 你的第一个任务

在交互界面中输入：
```text
opennova> 读取 README.md 文件
```

OpenNova 会：
1. 思考如何完成任务
2. 在需要时调用工具
3. 输出结果或继续追问

### 4.4 常用任务示例

**读取文件：**
```text
opennova> 读取 src/main.py 的前 50 行
```

**创建文件：**
```text
opennova> 创建一个 hello.py 文件，内容是打印 Hello World
```

**执行命令：**
```text
opennova> 运行 python hello.py
```

**列出目录：**
```text
opennova> 列出当前目录结构
```

### 4.5 单次任务模式

不进入交互界面，直接执行单个任务：

```bash
# 直接执行任务
uv run opennova run "读取 README.md"

# 使用计划模式
uv run opennova run --plan "重构 authentication 模块"

# 指定模型
uv run opennova run -m deepseek-v4-pro "分析项目结构"

# 使用 DeepSeek
uv run opennova run --provider deepseek "写一个测试用例"
```

### 4.6 REPL 内置命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/help` | 显示帮助 | `/help` |
| `/plan <task>` | 先生成计划，再确认是否执行 | `/plan 重构代码` |
| `/act <task>` | 直接执行 | `/act 读取文件` |
| `/tools` | 列出可用工具 | `/tools` |
| `/skills` | 列出已加载技能 | `/skills` |
| `/skill <name> [args]` | 直接调用一个技能 | `/skill analyze_project src` |
| `/reload-skills` | 从磁盘重新加载技能 | `/reload-skills` |
| `/model` | 显示当前模型 | `/model` |
| `/init [--force]` | 生成或重建 `OPENNOVA.md` | `/init --force` |
| `/config` | 显示配置 | `/config` |
| `/permissions [tool allow\|deny\|ask]` | 查看或更新工具权限规则 | `/permissions execute_command ask` |
| `/plugins [trust\|untrust name]` | 查看或信任本地项目插件 | `/plugins trust demo` |
| `/hooks` | 查看已加载 hooks | `/hooks` |
| `/automations` | 查看本地自动化任务 | `/automations` |
| `/diagnostics [path]` | 运行 Python 诊断 | `/diagnostics src` |
| `/status` | 查看运行时状态 | `/status` |
| `/todos` | 查看 TodoWrite 任务板 | `/todos` |
| `/checkpoint` | 查看 checkpoint/rollback 状态 | `/checkpoint` |
| `/history [n]` | 显示最近会话历史 | `/history 5` |
| `/resume [id]` | 恢复历史会话 | `/resume abc123` |
| `/sessions` | 列出历史会话 | `/sessions` |
| `/clear` | 清空当前会话状态 | `/clear` |
| `/exit` | 退出 REPL | `/exit` |

### 4.7 初始化项目记忆

如果你希望 OpenNova 更快理解当前项目，可以在项目根目录执行：

```text
opennova> /init
```

这会生成 `OPENNOVA.md`。该文件会被模型在后续任务中自动读取，用作长期项目记忆。

如果文件已经存在但你希望重新生成：

```text
opennova> /init --force
```

---

## 5. 高级功能

### 5.1 计划模式（Plan Mode）

对于复杂任务，使用计划模式让 AI 先制定计划：

```bash
uv run opennova run --plan "为用户管理模块添加单元测试"
```

或在 REPL 中：
```text
opennova> /plan 为用户管理模块添加单元测试
```

生成计划后，REPL 会展示计划内容并询问是否立即执行。这让复杂任务在落地前先经过一次人工确认。

### 5.2 多模型切换

```bash
# 使用 OpenAI GPT-4o
uv run opennova run --provider openai "任务"

# 使用 Anthropic Claude
uv run opennova run --provider anthropic "任务"

# 使用 DeepSeek
uv run opennova run --provider deepseek "任务"
```

或在配置中设置默认：
```yaml
default_provider: deepseek
default_model: deepseek-v4-pro
```

### 5.3 使用 Skills（技能）

加载的技能可以直接调用：

```text
opennova> 使用 code_review 技能审查 main.py
```

**内置示例技能：**
- `code_review` - 代码审查
- `generate_docs` - 生成文档
- `git_helper` - Git 辅助
- `analyze_project` - 项目分析

### 5.4 创建自定义 Skill

创建目录 `~/.opennova/skills/my_skill/`，然后新增文件 `SKILL.md`：

```markdown
---
name: my_skill
description: 对指定目标做分析总结。
when_to_use: 当你希望把一段常用提示词固化成可复用技能时使用。
allowed-tools: read_file, list_directory
arguments: [target]
argument-hint: <file-or-area>
---
分析目标内容。

Target: $ARGUMENTS

输出：
- 功能概述
- 关键依赖
- 风险点
```

重启 OpenNova 后会按 `~/.opennova/skills/<skill-name>/SKILL.md` 结构自动加载新技能。

### 5.5 MCP 服务器集成

配置 MCP 服务器扩展功能：

编辑 `~/.opennova/config.yaml`：

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/your/project"]

    - name: github
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
```

OpenNova 当前支持：
- `stdio`：启动子进程，通过 stdin/stdout 通信
- `sse`：连接 HTTP SSE endpoint

### 5.6 Web 工具说明

- `web_fetch` 会对真实 HTTP/HTTPS 页面发起请求，并返回提取后的内容。
- `web_search` 当前保留为统一工具接口；如果没有配置搜索后端，会明确返回未配置，而不是伪造结果。

### 5.7 安全配置

```yaml
security:
  sandbox_mode: true
  command_timeout: 30
  allow_network: true
  auto_confirm_safe: true
  allowed_paths:
    - "./src"
    - "./tests"
  blocked_commands: []
  strict_shell_parsing: false
  read_only: false
  max_file_size: 104857600
```

安全策略说明：
- 文件工具会统一经过 Sandbox 检查，限制工作目录、保护路径和只读模式。
- `execute_command` 默认优先使用 `shell=False` 执行普通命令。
- 当命令包含管道、重定向等 shell 特性时，会进入兼容 fallback，并通过 Guardrails 走确认逻辑。
- `allow_network: false` 时，HTTP 工具和常见联网命令会被阻断。
- `strict_shell_parsing: true` 时，包含 shell 特性的命令会直接拒绝，不再 fallback。

---

## 6. 常见问题

### Q1: 提示 API Key 未配置

**问题：**
```text
Configuration errors:
  • API key not configured for provider 'deepseek'
```

**解决：**
```bash
# 检查环境变量
echo $DEEPSEEK_API_KEY

# 或编辑配置文件
nano ~/.opennova/config.yaml
```

### Q2: Python 版本不对

**问题：**
```text
Requires-Python >=3.11
```

**解决：**
```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync
```

### Q3: 工具执行失败

**问题：**
```text
Error: Permission denied
```

**解决：**
检查文件权限：
```bash
chmod +x your_script.sh
```

### Q4: 流式输出乱码

**问题：** 输出显示异常

**解决：**
确保终端支持 UTF-8：
```bash
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
export PYTHONUTF8=1
```

### Q5: 如何更新

```bash
cd OpenNova
git pull
uv sync
```

### Q6: 如何查看日志

查看当前配置和历史比依赖内部文件路径更稳妥；如果需要调试，请先确认项目是否启用了对应日志输出配置。

---

## 附录：快捷键

| 快捷键 | 功能 |
|--------|------|
| `Tab` | 自动补全建议 |
| `↑` / `↓` | 浏览历史命令 |
| `Ctrl+C` | 清空当前输入 |
| `Ctrl+D` | 退出 REPL |
| `Enter` | 执行命令 |

---

## 下一步

1. 在真实项目中运行几个文件读取、命令执行和计划模式任务
2. 创建自己的 Skills 扩展功能
3. 配置 MCP 服务器连接更多工具
4. 通过 [GitHub Issues](https://github.com/Wardell-Stephen-CurryII/OpenNova/issues) 反馈问题
