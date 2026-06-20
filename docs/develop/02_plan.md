# OpenNova 02 深化计划：TUI、插件系统与代码理解

## Summary
本轮在 01 计划已完成的基础能力上继续深化三条主线：TUI 工具体验、插件声明系统、代码理解工具。目标是让 OpenNova 更接近 Claude Code 的可视化执行体验、可扩展能力模型和符号级代码理解能力。

## Key Changes
- 深化 TUI：基于现有 `ToolProgressTracker` 增加可折叠工具结果、diff preview、统一审批弹窗和工具耗时展示；保持现有 REPL/TUI 命令兼容。
- 深化插件系统：新增本地插件 manifest，插件可声明 commands、tools、skills、MCP servers 和 hooks；先只支持项目内 `.opennova/plugins/`，不做远程市场。
- 深化代码理解：在 `python_diagnostics` 基础上新增 Python 符号工具，包括 `python_definition`、`python_references`、`python_symbols`；优先使用静态 AST，后续再接 pyright/ruff server。
- 统一事件模型：扩展 SDK/TUI 可消费的 tool event metadata，包括 `tool_id`、`started_at`、`duration_ms`、`risk_level`、`diff`、`collapsible`。

## Implementation Plan
- TUI 体验：
  - 为每次工具调用生成稳定 `tool_id`，在 action/result 回调中关联。
  - 工具结果默认折叠长输出，错误、diff、审批结果保持醒目展示。
  - 审批弹窗复用 Guardrails/interaction metadata，不新增第二套确认逻辑。
  - diff preview 对 `write_file`、`edit_file`、`multi_edit_file`、worktree/git 相关工具优先展示。
- 插件系统：
  - 新增 `PluginManifest` 数据结构，读取 `.opennova/plugins/*/plugin.yaml`。
  - 插件 manifest 支持 `name`、`description`、`commands`、`skills`、`mcp_servers`、`hooks`、`enabled`。
  - 插件加载只允许项目目录内路径，默认 disabled 字段为 false 时跳过。
  - 插件 hooks 复用现有 `HookManager`，MCP 配置复用现有 `MCPServerConfig`。
- 代码理解：
  - 新增 AST 索引器，按文件返回 classes/functions/imports/top-level assignments。
  - `python_definition(symbol, path=".")` 返回定义文件、行号、类型和上下文片段。
  - `python_references(symbol, path=".")` 返回引用列表，限制 max results。
  - `python_symbols(path=".")` 返回结构化符号树，尊重 sandbox 和忽略目录。

## Test Plan
- TUI：测试 `ToolProgressTracker` 关联 tool_id、折叠策略、diff preview metadata、审批状态切换。
- 插件：测试 manifest 解析、disabled 跳过、skills/MCP/hooks 注册、非法路径拒绝、坏 manifest 友好报错。
- 代码理解：测试 definitions、references、symbols 对函数/类/import 的识别，测试 max results 和 sandbox 越界拒绝。
- 回归：运行 targeted ruff 覆盖新增模块，并运行 `uv run pytest -q` 全量通过。

## Assumptions
- 插件系统先做本地项目级插件，不做安装器、市场、签名或远程下载。
- Python 符号理解先用 AST 静态能力，避免引入 pyright 常驻进程和额外依赖。
- TUI 改造不改变现有命令语义，只增强展示和审批交互。
