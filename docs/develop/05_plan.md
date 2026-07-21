# OpenNova 05 计划：Checkpoint Restore、TUI Tool Cards、插件校验与自动化 Monitor

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.3 的当前命令或功能状态。

## Summary
05 计划接续 `04_plan.md` 的后续建议，把 checkpoint restore、TUI 工具展示、Python 代码理解、插件安全校验和本地自动化 monitor 继续推进。目标是让 04 已经可用的基础能力更接近 Claude Code 的“可审查、可恢复、可监控”的工程体验。

## Key Changes
- Checkpoint 命令完整化：新增共享 `/checkpoint list|diff|restore <id>` 处理器，支持查看 checkpoint、预览当前文件与快照 diff、恢复文件。
- TUI Tool Cards 数据模型：新增可折叠 `ToolCard`/`ToolCardStore`，基于 canonical `ToolEvent` 记录工具状态、diff preview、审批状态和取消标记，为后续真实 UI 卡片渲染打地基。
- Python 代码理解可选后端：新增 `PythonAnalysisBackend`，优先使用 AST fallback；当检测到 pyright/ruff server 可用时返回 backend metadata，保持无依赖可用。
- 插件 schema/权限校验：插件 manifest tools 增加 schema 校验、permission 字段和友好错误；不合法 plugin tool 不注册并进入 manager errors。
- 自动化 monitor：新增 `LocalAutomationMonitor`，支持 tick/run_due、记录 history、输出 monitor event；为后续 daemon/notification 做准备。

## Implementation Plan
- 在 `opennova.cli.checkpoint_commands` 中实现 checkpoint 子命令，TUI `/checkpoint` 复用同一处理器。
- 在 `opennova.cli.tool_cards` 中实现纯数据层，不直接改复杂 Textual UI；TUI callback 将 tool events 同步进 store。
- 在 diagnostics 工具 metadata 中暴露 `backend`、`backend_available` 和 import graph；新增 backend 探测 helper。
- 在插件 manager 中校验 trusted plugin tools：必须有 name/description/command，args 必须是字符串数组，permission 只能是 `read`/`edit`/`command`。
- 在 `opennova.automation` 中新增 monitor 类，复用现有 scheduler，不引入远程服务或后台常驻进程。

## Test Plan
- Checkpoint：`list` 返回 checkpoint；`diff` 输出 unified diff；`restore` 可恢复原文件。
- Tool Cards：start/request/result/cancel events 更新同一 card，长输出折叠，diff preview 保留。
- Python backend：无 pyright/ruff 时使用 AST fallback；metadata 显示 backend；import graph 仍可用。
- Plugin validation：非法 tool manifest 不注册并记录 errors；合法 trusted tool 带 permission metadata。
- Automation monitor：tick 运行 due tasks，返回事件并持久化 history。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 06 Plan 建议
- 将 `ToolCardStore` 真正渲染为 Textual 可折叠工具卡片、diff panel、审批按钮和取消按钮。
- 将 checkpoint 与每次 edit/write 的 unified diff 强绑定，并提供 `/checkpoint restore --preview`。
- 接入真实 pyright/ruff server 进程，提供 hover、definition、references、diagnostics 的高精度 LSP 工具。
- 为插件增加本地 lockfile、签名校验、`/plugins test` 和插件权限审核界面。
- 将 automation monitor 升级为本地 daemon，支持通知、线程唤醒、自动 transcript 归档。

## Assumptions
- 05 仍保持本地优先，不接远程服务。
- TUI 本轮先做数据模型和 callback 接入，不重写 Textual 布局。
- Python LSP 后端本轮只做可选探测和 metadata，不启动常驻 server。
- 插件权限校验默认兼容已 trusted 且合法的本地 command-backed tool。
