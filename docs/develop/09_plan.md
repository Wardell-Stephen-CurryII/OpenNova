# OpenNova 09 计划：Tool Card 操作状态、Transcript Checkpoints、插件 Trust 命令与 Automation Retry/Archive

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.2 的当前命令或功能状态。

## Summary
09 计划接续 `08_plan.md` 的后续建议，把 08 已有的 UI-ready state、checkpoint metadata、插件 lock/drift 和 automation run-loop 串成更完整的产品链路。本轮仍不重写完整 Textual UI、不引入常驻远程服务，而是实现可测试的面板导航/动作状态、checkpoint/diff transcript 导出、插件 trust/untrust 共享命令，以及 automation retry/archive 基础。

## Key Changes
- Tool Card 操作状态：在 `ToolCardStore` 上新增选中、展开/折叠、取消和审批状态变更 helper，为后续 Textual 快捷键和按钮提供稳定状态机。
- Transcript checkpoints：transcript export 展示 tool event 的 diff 与 checkpoint_id，让 checkpoint/diff 能随会话导出被审查。
- 插件 trust 命令统一：把 `/plugins trust <name>`、`/plugins untrust <name>` 接入共享 `handle_plugin_command()`，保持 TUI 行为一致。
- Automation retry/archive：为 `LocalAutomationDaemon` 增加失败重试事件和 transcript archive hook 参数，保留本地同步执行模型。
- 文档同步：更新 README/Quickstart/Tutorial 的插件、transcript 和 automation 能力说明。

## Implementation Plan
- 在 `opennova.cli.tool_cards` 中新增 `ToolCardInteractionState` 与 store 方法：`select_next()`、`toggle_expanded()`、`apply_approval()`。
- 在 `opennova.transcript.TranscriptExporter` 中扩展 tool event 输出，包含 `checkpoint_id`、`diff`、`duration_ms` 和 error。
- 在 `opennova.cli.plugin_commands` 中新增 trust/untrust 子命令，并让 TUI 复用共享 handler。
- 在 `opennova.automation.LocalAutomationDaemon` 中新增 `run_with_retry()`，支持一次失败重试和可注入 `archive_callback`。
- 补充 09 测试和文档，确保所有行为可独立验证。

## Test Plan
- Tool Card：选择下一张卡、展开/折叠、审批状态变更后 panel state 正确反映。
- Transcript：导出的 Markdown 包含 tool event 的 checkpoint_id、duration、error 和 diff fenced block。
- Plugins：`handle_plugin_command("trust demo")` 和 `untrust demo` 更新 trust store；TUI 可继续列表展示。
- Automation：`run_with_retry()` 在第一次失败、第二次成功时输出 retry event，并调用 archive callback；停止态不运行。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 10 Plan 建议
- 将 `ToolCardInteractionState` 接入真实 Textual widget，支持键盘导航、展开/折叠、diff panel、审批按钮和取消按钮。
- 将 transcript checkpoint/diff 与每次 tool event 的 runtime emission 深度绑定，支持 `/checkpoint diff` 从 transcript 反查。
- 插件系统继续推进权限审核 UI、lockfile drift 启动警告和可选签名校验。
- Automation daemon 增加真实后台循环、通知、线程唤醒、失败退避策略和自动 transcript 归档目录管理。
- Diagnostics 继续向 server 化演进，增加 hover/definition/references/diagnostics 的统一高精度事件。

## Assumptions
- 09 继续保持向后兼容，不破坏现有命令、工具名和配置字段。
- Tool Card 本轮仍做状态机与 adapter，不直接重写 Textual 布局。
- Automation retry/archive 仍是本地同步薄层，不启动系统 daemon。
- Transcript 导出只增加结构化信息，不改变现有消息导出格式。
