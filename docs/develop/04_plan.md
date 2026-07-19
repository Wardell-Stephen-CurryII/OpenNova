# OpenNova 04 计划：Checkpoint 自动化、导出、自动化命令与代码理解深化

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.2 的当前命令或功能状态。

## Summary
04 计划接续 `03_plan.md` 的后续建议，把已有基础模块接到真实工作流里。目标是让 checkpoint、transcript、automations、plugin tools 和 Python symbol graph 从“可测试模块”升级为用户和模型都能使用的能力。

## Key Changes
- Checkpoint 自动接入：`write_file`、`edit_file`、`multi_edit_file`、`delete_file`、`enter_worktree`、`exit_worktree` 执行前自动创建 checkpoint，并在工具结果 metadata 中返回 `checkpoint_id`。
- Transcript export：新增 `/export [path]`，导出当前 session messages、tool events、diff/审批元信息到 Markdown。
- Automations 命令产品化：扩展 `/automations` 支持 `list`、`once`、`interval`、`pause`、`resume`、`delete`、`run-now`。
- Plugin tools：受 trust 保护地加载 manifest `tools` 声明，支持本地 command-backed plugin tool，默认不 trust 不注册。
- Python symbol graph：AST indexer 增加 imports graph、跨文件 definition、import alias 解析，并保持无依赖 fallback。
- TUI/SDK metadata：继续复用 canonical `ToolEvent`，为 checkpoint/export/automation/plugin tool 添加稳定 metadata。

## Implementation Plan
- 新增 checkpoint runtime hook：在 ReActLoop 工具执行前根据工具名和文件参数创建快照；失败不阻断主工具，但会写入 metadata warning。
- 新增 transcript event buffer：AgentRuntime 记录 canonical tool events，`TranscriptExporter` 支持从 runtime 直接导出。
- 扩展 slash command registry：增加 `/export`，完善 `/automations` 参数解析；TUI 使用同一命令语义。
- 新增 `PluginCommandTool`：manifest 中 `tools` 支持 `name`、`description`、`command`、`args`、`read_only`；只在 plugin trusted 后注册。
- 增强 `PythonASTIndexer`：记录 `imports` metadata，definition 查找先匹配 qualified name，再解析 import alias 到跨文件符号。

## Test Plan
- Checkpoint：文件修改/删除工具执行前创建 checkpoint，restore 后可恢复原文件；工具 metadata 带 `checkpoint_id`。
- Export：`/export` 和 `TranscriptExporter` 输出包含 session id、messages、tool events、diff/permission metadata。
- Automations：命令层覆盖 once、interval、pause、resume、delete、run-now 和 history。
- Plugin tools：未 trust 不注册；trust 后注册并可执行 command-backed tool；非法 command/path 返回友好错误。
- Python symbols：跨文件 import alias definition、imports graph、max results 和 sandbox 约束。
- 回归：targeted ruff touched files；`LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 .venv/bin/python -m pytest -q` 全绿。

## 05 Plan 建议
- 把 checkpoint restore 做成完整 `/checkpoint list|diff|restore`，并自动关联每次写入 diff。
- 为 TUI 实现真正可折叠工具卡片、diff panel、审批按钮和可取消工具任务。
- 将 Python 代码理解接入可选 pyright/ruff server，提供 hover、diagnostics、references 的更高精度后端。
- 为插件系统增加工具 schema 校验、插件测试命令、插件权限声明和本地签名/锁文件。
- 将 automations 扩展为后台 daemon/monitor，并支持线程唤醒、通知和 transcript 自动归档。

## Assumptions
- 保持现有工具名、TUI slash 命令和配置兼容。
- 04 仍只做本地能力，不接远程服务。
- Plugin tools 只支持 trusted 本地插件声明的 command-backed tool，不执行远程下载。
- Checkpoint 自动创建是 best-effort；checkpoint 失败会记录 warning，但不阻断原工具。
