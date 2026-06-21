# OpenNova 11 计划：Tool Card Action Bindings、Checkpoint Transcript Diff、插件 Policy、Automation Backoff 与 Analysis Events

## Summary
11 计划接续 `10_plan.md` 的后续建议，把 keymap、transcript lookup、插件审计、automation archive 和 diagnostics event 继续推进一步。本轮继续保持低风险、可测试的本地实现：提供 Tool Card action binding 描述、`/checkpoint diff --from-transcript`、插件 trusted policy 和 drift warning、automation archive summary/backoff，以及 hover/definition/references 统一 analysis event 工厂。

## Key Changes
- Tool Card action bindings：新增 `tool_card_key_bindings()`，声明 TUI 可绑定的 key/action/description，供真实 Textual widget 复用。
- Checkpoint transcript diff：扩展 checkpoint 命令，支持 `diff --from-transcript <path> <checkpoint_id>` 从导出的 transcript 反查 diff。
- 插件 policy/drift warning：新增 `PluginPolicy`，可根据 require_signature/allow_hooks/allow_mcp 审计 trusted 插件；`/plugins audit --policy strict` 输出策略风险。
- Automation backoff/archive summary：新增 retry backoff 计算和 archive summary，支持查看归档事件数量、失败数和最近事件。
- Analysis event 扩展：为 hover、definition、references 提供统一 `PythonAnalysisEvent` 工厂，不执行真实 server，但固定事件契约。

## Implementation Plan
- 在 `opennova.cli.tool_cards` 中新增 `tool_card_key_bindings()`。
- 在 `opennova.cli.checkpoint_commands` 中解析 `diff --from-transcript <path> <id>`，复用 `extract_checkpoint_index()`。
- 在 `opennova.plugins` 中新增 `PluginPolicy` 与 `audit_policy()`，在 plugin command 中支持 `audit --policy strict`。
- 在 `opennova.automation` 中新增 `compute_retry_delay()` 与 `AutomationArchive.summary()`。
- 在 `opennova.tools.diagnostics_tools.PythonExternalAnalyzer` 中新增 `event_for_hover()`、`event_for_definition()`、`event_for_references()`。

## Test Plan
- Tool Card：key binding 列表包含 `j/k/enter/a/d/c`，描述稳定。
- Checkpoint：从 transcript 文件按 checkpoint id 反查 diff，缺失 id 返回友好错误。
- Plugins：strict policy 标记缺失签名、hooks 和 MCP 风险；命令输出包含 policy violation。
- Automation：backoff delay 可预测；archive summary 返回 total、failed、last_event。
- Diagnostics：hover/definition/references event 统一包含 kind、backend、path、success、payload。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 12 Plan 建议
- 将 Tool Card action bindings 接入真实 Textual widget，支持键盘导航和按钮触发。
- 将 `/checkpoint diff --from-transcript` 接入 transcript export 的 UI 链路，支持按 session 自动定位导出文件。
- 插件 policy 继续扩展为启动时 drift warning、签名校验和权限审核 UI。
- Automation archive summary 接入 `/automations daemon status`，并增加失败退避实际调度。
- Diagnostics events 接入真实 pyright/ruff server manager，提供 hover/definition/references 后端实现。

## Assumptions
- 11 继续保持向后兼容，不改变现有命令语义。
- 插件 strict policy 本轮只做报告，不自动阻断插件加载。
- Diagnostics hover/definition/references 本轮只固定事件契约，不启动常驻 server。
- Automation backoff 本轮只提供计算与归档摘要，不改变现有调度行为。
