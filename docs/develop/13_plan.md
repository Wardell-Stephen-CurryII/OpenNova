# OpenNova 13 计划：Tool Card Binding Help、Checkpoint Session CLI、Plugin Startup Warnings、Automation Archive Status 与 Diagnostics Server Lifecycle

## Summary
13 计划接续 `12_plan.md` 的后续建议，把 12 中已经完成的轻量骨架接到可直接使用的命令与状态输出中。目标仍然是小步、兼容、可测试：让 TUI 可以渲染快捷键禁用原因，让 `/checkpoint` 能按 session 查询 transcript diff，让 `/plugins` 能展示启动警告，让 `/automations daemon status` 包含 archive 摘要，并让 diagnostics server manager 具备可测试的 pyright/ruff subprocess 生命周期元数据。

## Key Changes
- Tool Card binding help：为 `build_tool_card_binding_plan()` 增加禁用原因，并新增可渲染的 binding help 文本，供真实 Textual widget 展示。
- Checkpoint session CLI：`/checkpoint diff --session <session_id> <checkpoint_id>` 读取项目 `.opennova/exports/<session_id>.md` 并返回 checkpoint diff。
- Plugin startup warnings CLI：新增 `/plugins warnings [--policy strict]`，汇总 lockfile drift 和可配置 policy 风险，作为 CLI/TUI 启动提示的共用入口。
- Automation archive status：`/automations daemon status` 接入 `daemon_status()`，输出 running、last events 和 archive summary。
- Diagnostics server lifecycle：`PythonAnalysisServerManager` 暴露 pyright/ruff server argv、runner 注入、process metadata 和 running 状态，并把 server 状态注入 hover/definition/references events。

## Implementation Plan
- 更新 `opennova.cli.tool_cards`，为 binding plan 加 `disabled_reason`，并新增 `render_tool_card_binding_help(store)`。
- 更新 `opennova.cli.checkpoint_commands`，把 session transcript lookup 接入 `/checkpoint diff --session`。
- 更新 `opennova.cli.plugin_commands`，新增 `warnings` 子命令并复用 `PluginManager.startup_warnings()`。
- 更新 `opennova.cli.automation_commands`，允许传入 `AutomationArchive` 并在 `daemon status` 输出 archive summary。
- 更新 `opennova.tools.diagnostics_tools`，让 `PythonAnalysisServerManager` 管理可测试的 backend command lifecycle。

## Test Plan
- Tool Card：binding plan 对不可用 action 返回禁用原因，binding help 可展示 enabled/disabled。
- Checkpoint：`/checkpoint diff --session <id> <checkpoint_id>` 可从 `.opennova/exports` 解析对应 diff，缺失时返回友好错误。
- Plugins：`/plugins warnings --policy strict` 同时报告 drift 和 strict policy violations。
- Automation：`/automations daemon status` 输出并返回 archive summary metadata。
- Diagnostics：pyright/ruff manager start 使用 runner，status 包含 argv/process，hover/definition/references event 包含 server_running。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 14 Plan 建议
- 将 `render_tool_card_binding_help()` 真正接入 Textual Tool Card widget 的底部快捷键栏，支持动态刷新和焦点态样式。
- 为 `/plugins warnings` 增加配置项，例如 `plugins.warning_policy = default|strict|silent`，并在 TUI 启动时自动显示一次。
- 为 automation daemon 增加真实后台 loop、通知事件和最近运行历史详情面板。
- 将 diagnostics server lifecycle 从 runner metadata 推进到真实 pyright-langserver/ruff server subprocess，补 JSON-RPC hover/definition/references 解析。
- 把 checkpoint session lookup 接入 transcript export/list 命令，支持用户从 session 列表直接选择 checkpoint diff。

## Assumptions
- 13 继续保持向后兼容，不改变现有命令的旧参数语义。
- Diagnostics server lifecycle 本轮仍通过 runner 注入测试，不强制启动真实常驻进程。
- Plugin warnings 本轮是显式命令入口，自动启动展示留给 14 计划。
