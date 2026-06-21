# OpenNova 07 计划：Tool Card UI Adapter、Checkpoint 写入绑定、插件 Drift 与 Automation 控制入口

## Summary
07 计划接续 `06_plan.md` 的后续建议，把 06 已经完成的数据层能力继续推进为更接近 Claude Code 的可操作体验。本轮仍保持本地优先和低风险增量，不重写 Textual 布局、不引入常驻 LSP 进程，而是实现：Tool Card UI adapter、文件写入前 checkpoint 绑定、Python 外部分析命令基础、插件 lockfile drift 检测与 `/plugins test` 支撑、automation daemon 命令入口。

## Key Changes
- Tool Card UI adapter：新增轻量 `ToolCardViewState`，支持展开/折叠、diff panel 文本、审批状态和取消标记，TUI 可直接复用而不依赖复杂 widget。
- Checkpoint 写入绑定：为 `write_file` 写入已有文件前自动创建 checkpoint，并在结果 metadata 中记录 `checkpoint_id` 与 diff，后续可串到 transcript。
- Python 外部分析基础：新增 `PythonExternalAnalyzer`，根据 backend status 构造 pyright/ruff diagnostics 命令，AST fallback 仍作为默认无依赖路径。
- 插件 drift/test：插件 manager 支持对比当前 manifest 与 lockfile，返回新增、移除和权限变化；新增共享 `/plugins test <name>` 命令处理器。
- Automation daemon 命令入口：`/automations daemon start|stop|tick|status` 复用 `LocalAutomationDaemon`，支持本地控制和最近事件输出。

## Implementation Plan
- 在 `opennova.cli.tool_cards` 中新增 `ToolCardViewState` 和 `build_tool_card_view()`，保持纯数据/纯文本输出。
- 在 `opennova.tools.file_tools.WriteFileTool` 中，若目标文件已存在且启用 checkpoint，则写入前使用 `CheckpointManager.create()` 生成快照。
- 在 `opennova.tools.diagnostics_tools` 中新增 `PythonExternalAnalyzer`，只负责命令规划，不直接启动长驻 server。
- 在 `opennova.plugins` 中新增 `compare_lockfile()`，在 `opennova.cli.plugin_commands` 中新增 `/plugins` 共享处理器。
- 在 `opennova.cli.automation_commands` 中扩展 daemon 子命令，保持现有 scheduler 命令兼容。

## Test Plan
- Tool Card：构造成功/失败/审批/取消事件，验证 view state、折叠切换、diff panel 和渲染文本。
- Checkpoint：`WriteFileTool` 覆盖已有文件时自动创建 checkpoint，metadata 返回 checkpoint_id，restore 可恢复旧内容；新文件不创建 checkpoint。
- Python analyzer：在 pyright/ruff 可用和不可用场景下，命令规划稳定返回 backend、argv 或 fallback reason。
- Plugin drift/test：lockfile 与当前 manifest 一致时无 drift；permission 改变时报告 changed；`handle_plugin_command("test <name>")` 返回测试报告。
- Automation daemon：`daemon start|status|tick|stop` 状态正确，tick 运行 due task 并输出事件。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 08 Plan 建议
- 将 `ToolCardViewState` 接入真实 Textual widget，提供快捷键展开/折叠、diff panel、审批按钮和取消按钮。
- 将 checkpoint 写入绑定扩展到 `edit_file`、`multi_edit_file`、delete、git/worktree 操作，并在 transcript 中记录 checkpoint/diff 对应关系。
- 启动真实 pyright/ruff server 或 subprocess diagnostics，增加 hover、definition、references、diagnostics 的高精度路径。
- 完善 `/plugins` 命令面：`lock`、`drift`、`trust`、`untrust`、权限审核 UI 和可选签名校验。
- Automation daemon 产品化：增加本地后台循环、通知、线程唤醒、失败重试策略和 transcript 自动归档。

## Assumptions
- 07 继续保持向后兼容，不破坏现有工具名、配置字段和 REPL/TUI 命令语义。
- Tool Card 本轮做 adapter，不直接重写 Textual 布局。
- Python 外部分析本轮只做命令规划，不启动常驻进程。
- Checkpoint 自动绑定默认只对已有文件覆盖生效，避免为全新文件生成空快照。
