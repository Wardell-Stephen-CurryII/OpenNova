# OpenNova 12 计划：Tool Card Binding Plan、Transcript Session Lookup、Plugin Startup Warnings、Automation Status Archive 与 Diagnostics Server Stub

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.2 的当前命令或功能状态。

## Summary
12 计划接续 `11_plan.md` 的后续建议，把已有 key binding、checkpoint transcript lookup、plugin policy、automation archive 和 diagnostics event 再推进一层。本轮仍保持低风险和可测试，不重写完整 TUI、不启动常驻 server，而是实现：Tool Card 绑定执行计划、按 session 自动定位 transcript checkpoint diff、插件启动警告、automation daemon status archive summary，以及 diagnostics server manager 占位。

## Key Changes
- Tool Card binding plan：新增 `build_tool_card_binding_plan()`，把 key bindings 与当前 panel actions 合并为 TUI 可渲染/启用禁用的 action 列表。
- Transcript session lookup：新增 checkpoint transcript resolver，支持从导出目录按 session id 自动定位 transcript，再按 checkpoint id 反查 diff。
- Plugin startup warnings：新增 `startup_warnings()`，汇总 drift 和 strict policy 风险，供启动时提示但不阻断。
- Automation status archive：daemon status helper 返回 running、last_events 和 archive summary，供 `/automations daemon status` 后续展示。
- Diagnostics server stub：新增 `PythonAnalysisServerManager`，提供可测试的 start/stop/status 与事件转发接口，为后续真实 pyright/ruff server 打基础。

## Implementation Plan
- 在 `opennova.cli.tool_cards` 中新增 `build_tool_card_binding_plan(store)`。
- 在 `opennova.transcript` 中新增 `resolve_checkpoint_diff_from_session(export_dir, session_id, checkpoint_id)`。
- 在 `opennova.plugins` 中新增 `startup_warnings(lockfile=None, policy=None)`。
- 在 `opennova.automation` 中新增 `daemon_status(daemon, archive=None)`。
- 在 `opennova.tools.diagnostics_tools` 中新增 `PythonAnalysisServerManager`。

## Test Plan
- Tool Card：binding plan 根据当前 selected card 的 actions 标记 enabled/disabled。
- Transcript：给定 export dir + session id + checkpoint id 可返回 diff；缺失时返回空字符串。
- Plugins：startup warnings 能同时报告 lockfile drift 和 strict policy violation。
- Automation：daemon status 包含 running、last_events_count、archive summary。
- Diagnostics：server manager start/stop/status 正确，diagnostics/hover event 通过统一事件接口返回。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 13 Plan 建议
- 将 `build_tool_card_binding_plan()` 接入真实 Textual widget，显示可用快捷键和禁用原因。
- 将 session transcript checkpoint lookup 接入 `/checkpoint diff --session <id> <checkpoint_id>`。
- 插件 startup warnings 接入 CLI/TUI 启动流程，并提供可配置的 warning policy。
- Automation status archive 接入 `/automations daemon status` 输出，并继续推进后台循环与通知。
- Diagnostics server manager 接入真实 pyright/ruff subprocess 生命周期，补 hover/definition/references 后端解析。

## Assumptions
- 12 继续保持向后兼容，不改变现有 CLI/TUI 命令语义。
- Diagnostics server manager 本轮仍是轻量状态管理，不启动真实常驻进程。
- Plugin startup warnings 本轮只生成报告，不阻断插件加载。
- Automation status helper 不改变现有调度行为。
