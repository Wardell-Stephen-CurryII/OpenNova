# OpenNova 0.4.2 使用教程

本教程面向直接使用 OpenNova 的开发者。当前交互入口只有 Textual TUI；旧交互式 CLI 和 `opennova tui` 子命令已经移除。

## 安装与配置

```bash
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova
uv sync
uv run opennova init
```

配置来源按“默认值 → 全局配置 → 项目配置 → 环境变量”覆盖。全局配置位于 `~/.opennova/config.yaml`，项目配置位于 `.opennova/config.yaml`。

```bash
export DEEPSEEK_API_KEY="sk-your-key"
uv run opennova
```

支持的原生 provider 为 `openai`、`anthropic` 和 `deepseek`。可以在单次任务中使用 `--provider` 和 `--model` 覆盖默认值。

## TUI 工作台

主界面由消息区、输入框以及 Context、Tasks、Activity 侧栏组成。Context 展示 token、压缩、活跃文件和决策，Tasks 合并计划与 todos，Activity 保留完整工具输出。同一回合的工具调用会在消息区折叠为一条摘要。

常用按键：

| 按键 | 功能 |
|---|---|
| `Enter` | 发送消息 |
| `Shift+Enter` | 输入换行 |
| `Ctrl+C` | 取消当前运行 |
| `Ctrl+Shift+C` | 复制消息区选中的文字 |
| `Cmd+C` | macOS 终端能传递快捷键时复制选区 |
| `Tab` / `Shift+Tab` | 切换焦点 |
| `Alt+1` / `Alt+2` / `Alt+3` | 切换 Context / Tasks / Activity |
| `Alt+T` | 显示或隐藏右侧工作台 |

复制无需弹出文本窗口：在消息区用鼠标拖选，再按复制快捷键即可。系统会组合使用 OSC 52 与平台剪贴板命令。

## 对话与工具

直接描述目标即可，例如：

```text
阅读 src/opennova/runtime/agent.py，总结运行流程，不要修改文件。
```

需要修改时可以说明验证标准：

```text
修复用户模块的边界条件，并运行相关 pytest 和 ruff 检查。
```

当前内置工具覆盖文件读写与编辑、代码搜索、Python 诊断、Shell、Git、后台任务、TodoWrite、Plan、子 Agent、Skills、Web、项目指南、MCP 资源和 Worktree。运行 `/tools` 查看当前实例的最终列表。

## Plan 与 Act

普通输入和 `/act` 会直接进入执行模式：

```text
/act 修复配置加载错误并补测试
```

复杂任务可以先规划：

```text
/plan 重构会话持久化，兼容旧格式并避免重复写入
```

计划会保存并显示在 Plan 面板。用户确认后才执行，步骤状态也会同步到 Todos。

## 会话恢复

OpenNova 自动把会话保存到 `~/.opennova/sessions/`。

```bash
# 启动后打开会话选择器
uv run opennova --resume

# 继续最近修改的会话
uv run opennova --continue
```

在 TUI 内输入 `/resume` 同样会打开选择器；`/resume <id>` 可以直接恢复指定会话。列表按修改时间倒序排列，标题来自第一条用户消息。恢复会重放消息区中的用户、助手和工具事件，并继续写入原 session，因此不会为同一次对话生成重复会话。

## 项目指南与 Skills

使用 `/init` 让模型分析仓库并创建 `OPENNOVA.md`：

```text
/init
/init --force
```

OpenNova 会在后续任务中自动读取该文件。自定义 Skill 使用如下结构：

```text
.opennova/skills/my_skill/SKILL.md
~/.opennova/skills/my_skill/SKILL.md
```

```markdown
---
name: my_skill
description: Review a requested module.
when_to_use: Use when the user asks for a focused module review.
allowed-tools: read_file, grep_code
arguments: [target]
---
Review $ARGUMENTS and report correctness risks.
```

使用 `/skills` 查看、`/skill my_skill src/` 调用、`/reload-skills` 重新加载。

## 权限与沙箱

权限模式可以在启动时设置：

```bash
uv run opennova --permission-mode request
uv run opennova --permission-mode auto
uv run opennova --permission-mode full
```

也可以在 TUI 中切换：

```text
/permissions
/permissions mode auto
/permissions execute_command ask
```

`request` 每次询问；`auto` 自动执行日常开发操作，只对删除、强制 Git 操作、内网访问、
敏感信息写入和不受信任 MCP 等高风险动作询问；`full` 跳过普通审批。`full` 仍不能绕过
hard block、deny 规则、Plan 确认、路径/网络限制和进程沙箱。

## MCP、插件与 Hooks

MCP server 在配置中声明，支持 stdio 和 SSE：

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./src"]
```

项目插件可以增加工具和 slash command，但只有工作区路径和内容摘要均匹配信任记录时才会
加载。通过 `/plugins` 查看、信任、锁定、检查漂移和审计。项目 Python hooks 默认不执行；
使用 `/hooks trust` 信任当前摘要，代码变化后需要重新信任，使用 `/hooks untrust` 可撤销。

## 检查点与导出

覆盖或编辑已有文件时，文件工具会生成 checkpoint 元数据。

```text
/checkpoint list
/checkpoint diff <id>
/checkpoint restore --preview <id>
/checkpoint restore <id>
/checkpoint rewind <id>
/checkpoint rewind --apply <id>
/export
```

`rewind` 默认只预览，显式使用 `--apply` 才会恢复。分层记忆可以通过 `/memory list|add|delete` 管理，会自动忽略过期记忆并对重复段落去重。

导出的 Markdown transcript 包含工具结果及 checkpoint/diff 信息，默认写入项目 `.opennova/exports/`。

## 自动化与诊断

```text
/automations
/automations once nightly 2026-07-13T01:00:00 检查测试失败
/automations interval health 3600 运行健康检查
/automations daemon status
/diagnostics src/
```

自动化是本地调度机制。Python 诊断还可以通过 `python_symbols`、`python_definition` 和 `python_references` 工具提供符号信息。

## 单次非交互任务

TUI 是唯一交互界面，但仍保留适合脚本的单次任务入口：

```bash
uv run opennova run "读取 README.md"
uv run opennova run --plan "为配置模块增加测试"
uv run opennova run --provider anthropic -m claude-sonnet-4 "检查 src/"
```

`--resume` 和 `--continue` 只用于 TUI 启动，不能与直接任务组合。

## 故障排查

- `Missing API key`：确认环境变量或 YAML 中的 key 已设置。
- Windows 中文无法输入：确保直接启动当前 Textual TUI，并使用支持 Unicode 的终端。
- 无法复制：先拖选文字，再使用 `Ctrl+Shift+C`；Linux 需要 `wl-copy` 或 `xclip` 作为原生 fallback。
- 恢复列表为空：确认当前项目对应的 `~/.opennova/sessions/` 目录中存在会话。
- MCP 不可用：检查 command、args、transport 和服务端日志。
- 中文路径测试报编码错误：使用 `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1`。

更多内部接口见 [API 文档](API.md)，架构与开发命令见项目根目录 [AGENTS.md](../AGENTS.md)。
