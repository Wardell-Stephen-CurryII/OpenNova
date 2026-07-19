# OpenNova 03 计划：运行时协议、权限持久化与产品化补强

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.2 的当前命令或功能状态。

## Summary
03 计划在 01/02 已完成的工具协议、权限模式、插件 manifest、TUI 进度、符号工具和自动化基础上继续产品化。目标是把“能力骨架”升级为更接近 Claude Code 的稳定体验：统一 runtime tool event、持久化权限决策、插件 trust model、自动化运行状态、Python qualified symbol、共享 slash command registry、TodoWrite、checkpoint 和 transcript export。

## 已完成
- [x] 修复中文路径运行体验：新增 UTF-8 环境 helper，并让 shell 工具执行时默认注入 `LC_ALL`、`LANG`、`PYTHONUTF8=1`。
- [x] 新增 canonical runtime tool events：`tool_start`、`permission_request`、`tool_result`、`tool_error`、`tool_cancelled`。
- [x] 新增 `ToolUseContext`，统一携带 `tool_id`、参数、risk level、diff、耗时、结果折叠等元信息。
- [x] SDK/TUI 接入 runtime tool event，同时保留旧 `action/result` 回调兼容。
- [x] 新增 `PermissionStore`，支持项目 `.opennova/permissions.json` 持久化 allow/deny/ask 规则。
- [x] Guardrails 接入持久化权限，并确保 hard block 不会被 always allow 绕过。
- [x] 插件系统默认只发现 manifest；skills、MCP、hooks、commands 需显式 trust 后才加载。
- [x] 插件 commands 进入共享 slash command registry。
- [x] 自动化调度器支持 list、pause、resume、delete、run_now 和运行历史。
- [x] Python AST 符号工具支持 `qualified_name`、parent scope 和更精确 definition 查询。
- [x] 新增共享 slash command registry，并暴露 `/permissions`、`/plugins`、`/hooks`、`/automations`、`/diagnostics`、`/status`、`/todos`、`/checkpoint`。
- [x] 新增 `todo_write` 工具，为模型提供 Claude Code TodoWrite 风格任务板。
- [x] 新增轻量 checkpoint snapshot 与 transcript Markdown export 基础模块。
- [x] 补齐 03 回归测试，新增 `tests/test_03_productization.py`。

## 后续建议
- [ ] 将 checkpoint 自动接入 `write_file`、`edit_file`、`multi_edit_file` 和 worktree/git 工具，做到写入前自动创建可恢复快照。
- [ ] 将 transcript export 接入 `/sessions` 或新增 `/export` 命令，导出真实 session JSONL、tool events、diff 和审批记录。
- [ ] 将 `/automations` 从列表入口扩展为 create/pause/resume/delete/run-now 完整交互命令。
- [ ] 将插件 tools 从 manifest 声明推进到受 trust 保护的真实动态工具加载。
- [ ] 为 Python 符号理解增加 imports graph、跨文件跳转和可选 pyright/ruff server 后端。
- [ ] 为 TUI 增加真正可折叠工具卡片、diff panel、审批按钮和 interruptible tool 取消。

## Test Plan
- 新增测试：`tests/test_03_productization.py` 覆盖 tool events、permission store、plugin trust、automation history、qualified symbols、UTF-8 helper、slash command registry、todo_write、checkpoint、transcript export。
- 回归测试：插件、自动化、Python symbols、SDK/worktree、TUI tool progress、Claude alignment foundation 均需通过。
- 全量验收：使用 UTF-8 locale 运行 `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest -q`。

## Assumptions
- 继续保持现有工具名、TUI slash 命令语义和配置兼容。
- 插件系统仍只支持本地项目插件，不做远程市场、签名分发或自动下载。
- AST 符号理解保持无依赖可用，LSP/pyright 作为后续增强。
- 自动化先做本地运行面，不引入远程调度服务。
