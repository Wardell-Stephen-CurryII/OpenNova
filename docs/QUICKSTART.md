# OpenNova 快速开始指南

5 分钟快速上手当前版本的 OpenNova。

> 本文档以仓库开发者视角为主，默认使用 `uv run opennova ...`。如果你已经通过 `uv tool install .` 安装了全局 CLI，可以把命令里的 `uv run` 去掉。

## 第一步：安装

```bash
# 克隆仓库
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova

# 安装依赖
uv sync

# 初始化全局配置（生成 ~/.opennova/config.yaml）
uv run opennova init
```

## 第二步：配置模型

当前默认配置：

```yaml
default_provider: deepseek
default_model: deepseek-v4-pro
```

推荐直接使用环境变量提供 API Key：

```bash
export DEEPSEEK_API_KEY="sk-your-deepseek-key"
```

如果你想改成其他 provider，也可以编辑 `~/.opennova/config.yaml`：

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
```

## 第三步：开始使用

```bash
# 启动 Textual TUI（默认交互界面，包含 Windows 中文输入法兼容处理）
uv run opennova

# 显式使用 Textual TUI
uv run opennova run --tui

# 直接执行单个任务
uv run opennova run "读取 README.md"
```

## 第四步：初始化项目记忆

进入项目后，可以让 OpenNova 为当前仓库生成一份长期项目说明：

```text
/init
```

它会在项目根目录创建 `OPENNOVA.md`。这个文件会被模型自动读取，用来帮助后续任务更快理解代码库、目录结构、工作流和注意事项。

如果文件已经存在但你想重建：

```text
/init --force
```

## 示例任务

```text
# 文件操作
opennova> 读取 src/main.py
opennova> 创建 test.py 文件
opennova> 列出目录结构

# 代码生成
opennova> 写一个计算斐波那契数列的函数

# Shell 命令
opennova> 运行 pytest tests/

# 计划模式
opennova> /plan 重构认证模块

# 项目初始化
opennova> /init
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `uv run opennova` | 启动 Textual TUI |
| `uv run opennova run "task"` | 执行单次任务 |
| `uv run opennova run --tui` | 显式使用 Textual TUI |
| `uv run opennova --permission-mode request\|auto\|full` | 选择本次运行的审批模式 |
| `uv run opennova --version` | 查看版本 |
| `uv run opennova init` | 初始化全局配置 |
| `/init [--force]` | 生成或重建 `OPENNOVA.md` |
| `/permissions mode request\|auto\|full` | 查看或切换当前审批模式 |
| `/permissions <tool> allow\|deny\|ask` | 查看或更新工具权限规则 |
| `/plugins [trust\|untrust\|test name\|lock\|drift\|warnings\|audit [--policy strict]]` | 管理、锁定、校验、启动警告和审计本地项目插件 |
| `/automations` | 查看本地自动化任务 |
| `/automations once <name> <run_at> <prompt>` | 创建一次性自动化任务 |
| `/automations interval <name> <seconds> <prompt>` | 创建周期自动化任务 |
| `/automations pause\|resume\|delete\|run-now <id>` | 管理自动化任务 |
| `/automations daemon start\|stop\|status\|tick\|run` | 控制本地 automation daemon |
| `/diagnostics [path]` | 运行 Python 诊断 |
| `/status` | 查看当前运行时状态 |
| `/todos` | 查看 TodoWrite 任务板 |
| `/checkpoint list\|diff\|restore [--preview] <id>` | 管理 checkpoint 快照 |
| `/checkpoint diff --session <session> <id>` | 从 `.opennova/exports/<session>.md` 反查 checkpoint diff |
| `/checkpoint diff --from-transcript <path> <id>` | 从 transcript 反查 checkpoint diff |
| `write_file` checkpoint metadata | 覆盖已有文件时自动创建 checkpoint |
| `edit_file` checkpoint metadata | edit 和 multi-edit 也会自动创建 checkpoint |
| `/export [dir]` | 导出当前 transcript，并包含工具 checkpoint/diff 详情 |
| automation retry/archive | 本地 daemon retry 事件可通过 callback 归档 |
| automation backoff/archive summary | 提供 retry delay 和 archive 摘要能力 |
| transcript checkpoint lookup | 导出的 transcript 可按 `checkpoint_id` 建索引 |
| transcript session lookup | `/checkpoint diff --session` 可按 session id 解析 checkpoint diff |
| plugin startup warnings | `/plugins warnings --policy strict` 可报告 lockfile drift 和策略风险 |
| diagnostics events | diagnostics、hover、definition、references 可包装成统一事件 payload |
| diagnostics server manager | 轻量 server 生命周期门面记录 pyright/ruff argv 和 process metadata |
| plugin startup warnings | 可生成 drift 和 strict policy 启动警告 |
| automation status archive | daemon status 可包含 archive 摘要 |
| `/help` | 查看交互命令帮助 |
| `/exit` | 退出当前会话 |

> 如果项目路径包含中文或其他非 ASCII 字符，建议使用：
> `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest -q`

## 获取帮助

- [完整教程](TUTORIAL.md)
- [API 文档](API.md)
- [报告问题](https://github.com/Wardell-Stephen-CurryII/OpenNova/issues)
- [GitHub Discussions](https://github.com/Wardell-Stephen-CurryII/OpenNova/discussions)
