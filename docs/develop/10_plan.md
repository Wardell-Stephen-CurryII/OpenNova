# OpenNova 10 计划：Tool Card Keymap、Transcript Checkpoint Lookup、插件 Audit/Signature 与 Automation Archive

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.3 的当前命令或功能状态。

## Summary
10 计划接续 `09_plan.md` 的后续建议，把已有状态机继续推进为更靠近 Claude Code 的可操作产品薄层。本轮继续避免一次性重写完整 Textual UI 或引入常驻服务，而是实现可测试的小能力：Tool Card 键盘动作适配、transcript checkpoint 反查、插件权限审计与签名占位、automation 归档目录管理，以及 diagnostics 统一事件模型。

## Key Changes
- Tool Card keymap：新增 `apply_tool_card_key()`，把 `j/k/enter/a/d/c` 映射到选择、展开、审批、拒绝和取消动作，后续 Textual widget 可直接复用。
- Transcript checkpoint lookup：新增 transcript checkpoint index，支持从导出的 transcript 中反查 `checkpoint_id`、tool id 和 diff。
- 插件 audit/signature：新增插件权限审计报告和可选签名字段展示，`/plugins audit` 输出 trusted 插件的 tools/hooks/MCP 权限风险摘要。
- Automation archive：新增本地 archive directory helper，把 daemon retry/run 事件写入 JSONL，作为后续自动 transcript 归档基础。
- Diagnostics event：新增 `PythonAnalysisEvent`，统一 diagnostics/hover/definition/references 的事件形状，为后续 server 化做接口地基。

## Implementation Plan
- 在 `opennova.cli.tool_cards` 中新增 `apply_tool_card_key(store, key)`，只改数据层，不依赖 Textual。
- 在 `opennova.transcript` 中新增 `build_checkpoint_index()` 与 `extract_checkpoint_index(path)`。
- 在 `opennova.plugins` 中新增 `audit_permissions()`，在 `opennova.cli.plugin_commands` 中新增 `audit` 子命令。
- 在 `opennova.automation` 中新增 `AutomationArchive`，提供 `append_event()` 与 `read_events()`。
- 在 `opennova.tools.diagnostics_tools` 中新增 `PythonAnalysisEvent` 与 `PythonExternalAnalyzer.event_for_diagnostics()`。

## Test Plan
- Tool Card：`j/k/enter/a/d/c` 能更新 selection、expanded、approval 和 cancel 状态。
- Transcript：给定 tool events 能构建 checkpoint index；从导出的 Markdown 中能提取 checkpoint/diff。
- Plugins：audit 能标记 trusted 插件的 command/edit 权限、hooks 和 MCP server；`/plugins audit` 输出摘要。
- Automation：archive helper 写入 JSONL 并读回事件；daemon retry 事件可通过 archive callback 保存。
- Diagnostics：diagnostics event 统一包含 kind、backend、path、success、payload。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 11 Plan 建议
- 将 `apply_tool_card_key()` 接入真实 Textual widget，提供快捷键导航、diff panel、审批按钮和取消按钮。
- 将 transcript checkpoint lookup 接入 `/checkpoint diff --from-transcript <id>`。
- 插件系统继续推进签名校验、lockfile drift 启动警告、权限审核 UI 和 trusted plugin policy。
- Automation archive 与 transcript export 合流，支持自动归档目录、失败退避策略、通知和线程唤醒。
- Diagnostics event 接入 pyright/ruff server manager，增加 hover/definition/references 的真实后端执行。

## Assumptions
- 10 继续保持向后兼容，不改变现有 CLI/TUI 命令语义。
- Tool Card 本轮仍在数据层实现 keymap，不重写 Textual 布局。
- 插件 signature 本轮只做 manifest 字段和审计展示，不做加密签名验证。
- Automation archive 是本地 JSONL，不引入远程服务。
