# OpenNova 08 计划：Tool Card 面板、Edit Checkpoints、Diagnostics 执行、插件 Lock/Drift 与 Automation Run Loop

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.2 的当前命令或功能状态。

## Summary
08 计划接续 `07_plan.md` 的后续建议，把 07 的 adapter 和命令基础继续推进为可操作的产品薄层。本轮仍保持本地优先和低风险增量，不重写完整 Textual 布局、不启动常驻 LSP server，而是实现可测试的面板状态、edit/multi-edit checkpoint 绑定、外部 diagnostics subprocess 执行、插件 lock/drift 命令、automation daemon run-loop。

## Key Changes
- Tool Card 面板模型：新增 `ToolCardPanelState`，支持当前选中卡片、展开/折叠、diff panel、approval/cancel action 标记，作为后续 Textual widget 的稳定输入。
- Edit checkpoints：将 07 的 write checkpoint 绑定扩展到 `edit_file` 和 `multi_edit_file`，在写入前创建 checkpoint 并返回 metadata。
- Diagnostics 执行薄层：在 `PythonExternalAnalyzer` 中新增一次性 subprocess diagnostics 执行；pyright/ruff 可用时运行命令，不可用时返回 AST fallback 结果。
- 插件 lock/drift 命令：扩展 `/plugins lock` 与 `/plugins drift`，支持生成 lockfile JSON 和比较当前 manifest 变化。
- Automation daemon run-loop：新增可测试的 `run_until_idle()`，支持 tick 上限、最近事件缓存和停止态保护。

## Implementation Plan
- 在 `opennova.cli.tool_cards` 中新增 `ToolCardPanelState` 和 `build_tool_card_panel()`，不引入 Textual widget 依赖。
- 在 `opennova.tools.file_tools.EditFileTool` 与 `MultiEditFileTool` 中复用 checkpoint 创建逻辑，覆盖已有文件写入前记录快照。
- 在 `opennova.tools.diagnostics_tools.PythonExternalAnalyzer` 中新增 `run_diagnostics()`，使用注入式 runner，便于测试和未来替换为 server 后端。
- 在 `opennova.cli.plugin_commands` 中扩展 `lock`、`drift` 子命令，lockfile 默认路径为 `.opennova/plugins/lock.json`。
- 在 `opennova.automation.LocalAutomationDaemon` 中新增 `run_until_idle()`，并扩展 `/automations daemon run` 命令。

## Test Plan
- Tool Card panel：多卡片状态下选中指定 card，展开输出、diff panel、approval/cancel action 标记正确。
- Edit checkpoints：`edit_file` 和 `multi_edit_file` 修改已有文件时返回 `checkpoint_id`，可 restore 旧内容。
- Diagnostics：pyright/ruff runner 成功时返回 backend/output/argv；无外部 backend 时返回 AST fallback metadata。
- Plugins：`/plugins lock` 写入 lockfile；修改权限后 `/plugins drift` 输出 changed。
- Automation：`run_until_idle()` 在 daemon running 时运行 due tasks，停止态不运行；`daemon run` 命令返回事件摘要。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 09 Plan 建议
- 将 `ToolCardPanelState` 真正接入 Textual UI 布局，提供快捷键导航、展开/折叠、diff panel、审批按钮和取消按钮。
- 将 checkpoint 自动绑定扩展到 `delete_file`、git/worktree 工具，并把 checkpoint/diff 写入 transcript export。
- 将 diagnostics 执行升级为可选 pyright/ruff server 管理器，支持 hover、definition、references、diagnostics 的统一高精度事件。
- 完善插件命令：`/plugins trust`、`/plugins untrust`、权限审核 UI、lockfile drift 警告和可选签名校验。
- Automation daemon 产品化：加入本地后台循环、通知、线程唤醒、失败重试策略和 transcript 自动归档。

## Assumptions
- 08 继续保持向后兼容，不破坏现有工具名、配置字段和交互命令语义。
- Tool Card 本轮仍做 UI-ready state，不直接大改 Textual 布局。
- Diagnostics 本轮只做一次性 subprocess 执行，不启动常驻 server。
- 插件 lockfile 仍是本地可信快照，不做远程签名分发。
