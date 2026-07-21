# OpenNova 06 计划：Checkpoint Preview、Tool Card 渲染、插件 Lockfile 与本地 Automation Daemon

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.3 的当前命令或功能状态。

## Summary
06 计划接续 `05_plan.md` 的后续建议，把已经存在的数据层能力推进到更可操作的产品基础。本轮目标不是一次性重写 TUI 或引入常驻 LSP，而是完成可测试、低风险、能继续向 Claude Code 体验靠近的增量：checkpoint 恢复预览、工具卡片渲染数据、Python 分析后端状态、插件 lockfile/test 基础，以及本地自动化 daemon 的单步运行循环。

## Key Changes
- Checkpoint restore preview：`/checkpoint restore --preview <id>` 只输出即将恢复的 unified diff，不写文件；`/checkpoint restore <id>` 保持真实恢复。
- Tool Card 渲染基础：在 `ToolCardStore` 之上新增纯文本渲染器，支持折叠长输出、diff preview、审批状态和取消状态，为后续 Textual 卡片 UI 提供稳定数据。
- Python backend status：新增轻量后端状态结构，暴露 AST fallback、pyright、ruff 是否可用，供 diagnostics/LSP 后续升级复用。
- 插件 lockfile/test 基础：为 trusted 插件生成本地 lockfile 条目，并提供插件测试辅助，校验 manifest、工具 schema 和权限声明。
- Automation daemon 基础：在 `LocalAutomationMonitor` 之上增加本地 daemon 单步运行循环，支持 start/stop 状态、tick 事件和最近运行事件缓存。

## Implementation Plan
- 在 `opennova.cli.checkpoint_commands` 中扩展 restore 解析，支持 `--preview` 与普通 restore 共用 diff 生成逻辑。
- 在 `opennova.cli.tool_cards` 中新增 `render_tool_card()` 与 `render_tool_cards()`，保持无 Textual 依赖，TUI 后续直接消费字符串或 Rich/Textual widget。
- 在 `opennova.tools.diagnostics_tools` 中新增 `get_python_backend_status()`，返回 backend、pyright_available、ruff_available、fallback。
- 在 `opennova.plugins` 中新增 plugin lockfile 数据模型和 manager 方法：`build_lockfile()`、`test_plugin()`。
- 在 `opennova.automation` 中新增 `LocalAutomationDaemon`，复用 `LocalAutomationMonitor.tick()`，不创建系统后台服务。

## Test Plan
- Checkpoint：`restore --preview <id>` 输出 diff 且不修改文件；普通 restore 仍修改文件。
- Tool Cards：渲染 start/running/success/error/cancelled 状态；长输出折叠；diff preview 保留。
- Python backend：状态结构在无外部依赖时返回 AST fallback，并暴露 pyright/ruff 可用性布尔值。
- Plugin lock/test：trusted plugin lockfile 记录 name/path/tools/hooks/permissions；坏 tool manifest 测试失败并给出错误；合法插件测试通过。
- Automation daemon：start/stop 状态正确；run_once 调用 monitor 并缓存最近事件；停止后 run_once 不执行 runner。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 07 Plan 建议
- 将 Tool Card 渲染真正接入 Textual UI，提供展开/折叠快捷键、diff panel、审批按钮和取消按钮。
- 为 checkpoint 增加自动写入绑定：write/edit/multi-edit/git 操作前自动创建 checkpoint，并在 transcript 中关联 diff。
- 接入真实 pyright/ruff server：提供 hover、definition、references、diagnostics 的高精度后端，AST 继续作为 fallback。
- 插件系统继续增强：lockfile drift 检测、插件权限审核 UI、`/plugins test <name>` 命令、可选签名校验。
- Automation daemon 产品化：增加 CLI/TUI 控制入口、通知、线程唤醒、失败重试策略和 transcript 自动归档。

## Assumptions
- 06 仍保持本地优先，不引入远程服务或系统级后台安装器。
- Tool Card 本轮先做可测试渲染层，不重写 Textual 布局。
- Python 分析后端本轮只暴露状态，不启动常驻 LSP server。
- 插件 lockfile 是项目本地信任快照，不做远程签名分发。
