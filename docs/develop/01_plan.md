# OpenNova 后续开发计划：向 Claude Code 对齐

> 归档说明：这是历史实施计划，不代表 OpenNova 0.4.1 的当前命令或功能状态。

## Summary
本轮只读审查覆盖了 OpenNova 当前 `master`、全量测试、重点 runtime/tool/security/session 代码，并抽样对照了 `/Users/linxiaohai/资料/北航/claude_code/claude_code_src` 的 Claude Code 架构与工具接口。当前 OpenNova 的基础能力已经跑通，`uv run pytest -q` 结果为 `130 passed`；主要差距集中在工具协议元信息、权限/审批模型、会话恢复、子代理隔离、搜索/编辑工具、TUI 体验和 SDK/headless 能力。

## Bugs / 必须修复
- P0：修复 `ToolRegistry` 单例污染。当前 `ToolRegistry` 是全局单例，多个 `AgentRuntime`、子 agent、测试或 MCP 初始化会共享工具表，容易串 runtime/config/tool 实例。改为默认实例级 registry，仅保留可选全局 registry 用于明确场景，并补多 runtime 隔离测试。
- P0：补齐工具参数 schema 对复杂类型的推断。刚修了 `int | None`，但 `list[str]`、`dict[str, Any]`、`Literal`、`Enum`、`Union` 多类型仍会退化，容易继续误导模型。实现稳定的 JSON Schema 转换层，并给 `execute_command`、`ask_user_question`、`agent`、`create_file` 等关键工具加 schema snapshot 测试。
- P0：统一 shell 同步/异步执行校验。`execute()` 会校验传入 `working_dir` 是否存在，`execute_async()` 没有同等校验；两者错误语义、metadata、timeout 处理也不完全一致。抽出共享 `_prepare_command_execution()`，让 sync/async 路径使用同一 guard、cwd、argv/shell fallback、metadata 逻辑。
- P1：清理 `runtime/loop.py`、`session/manager.py` 等明确 lint 问题。targeted ruff 发现未用导入、重复 `traceback` 导入、`asyncio.TimeoutError` 过时别名、import 顺序问题。先修 targeted 文件，再逐步扩大 ruff 范围。
- P1：修复会话压缩恢复的完整性风险。`SessionManager.load_session(apply_compression=True)` 只返回 marker 后消息，调用方必须另行注入 summary，容易遗漏。提供 `load_session_with_summary()` 或返回结构化对象，确保 resume 自动恢复压缩摘要。
- P1：修复文件工具遗留路径校验死代码和错误语义不一致。`file_tools._validate_path()` 已基本被 Sandbox 替代但仍保留，容易让后续开发误用。移除或改成 Sandbox 包装，并统一 “outside working dir / protected path / read-only” 的错误文案。

## 功能优化 / Claude Code 对齐
- P1：升级工具接口为“声明式工具协议”。在 `BaseTool` 增加 `is_read_only()`、`is_destructive()`、`requires_permission()`、`is_concurrency_safe()`、`max_result_chars`、`search_hint`、`aliases`、`progress metadata`。运行时根据这些元信息统一做权限、并发、结果截断、UI 展示，而不是每个工具各管一段。
- P1：把 Guardrails 升级为权限决策系统。参考 Claude Code 的 `canUseTool`/permission context，新增 `PermissionMode`：`default`、`ask`、`allowEdits`、`readOnly`、`bypass`。支持 session 级 always allow/deny/ask 规则，并在 TUI 里持久化用户选择。
- P1：引入 `Read`/`Edit`/`MultiEdit` 风格的精细编辑工具。当前 `write_file` 偏覆盖式，Claude Code 的核心体验更依赖可审查的局部编辑。新增 `edit_file` 与 `multi_edit_file`，要求 old/new 精确匹配，失败时返回上下文建议，所有写入输出 unified diff。
- P1：补齐 `grep` / `glob` / `list` 搜索工具。不要让模型靠 shell `rg` 才能搜索代码。实现 `glob_files`、`grep_code`，支持 max results、ignore hidden、respect `.gitignore`，输出结构化结果，便于模型稳定引用。
- P2：增强 TUI 的工具进度和审批体验。展示 in-progress tool id、耗时、可取消状态、工具结果折叠、diff 预览、确认弹窗。`/init`、shell fallback、文件写入、delete、敏感文件访问都走统一确认组件。
- P2：加强子 agent 隔离。子 runtime 应拥有独立 tool registry、working memory、session id、permission context；只继承必要配置和安全策略。为 background agent 增加取消、输出 tail、父会话 follow-up 注入的明确状态机。
- P2：让 `OPENNOVA.md` 长期记忆变成分层记忆系统。保留项目根 `OPENNOVA.md`，再增加可选 `.opennova/memory/`，支持手动记忆、自动摘要、技能发现、已读文件缓存和重复注入去重。
- P2：完善 MCP 能力面。补 `list_mcp_resources`、`read_mcp_resource`、MCP auth/elicitation 流程、MCP tool collapse/metadata，确保外部工具和内置工具共享同一权限与 UI 管道。

## 新功能开发 / 中长期方向
- P2：SDK/headless 模式。提供 Python API：创建 session、提交消息、流式读取事件、列会话、恢复会话、读取 transcript、注入自定义工具。事件格式对齐 tool_start/tool_delta/tool_result/permission_request/compact_boundary。
- P2：工作树开发流。新增 `enter_worktree` / `exit_worktree` 工具或命令，支持为大改动创建隔离 worktree，完成后可 merge/PR/cleanup。和当前 git 工具、session、sandbox allowed paths 联动。
- P3：Hook 与插件系统。支持 session_start、pre_tool_use、post_tool_use、pre_compact、post_compact hooks；插件可以提供命令、工具、MCP 配置和 skills。先做本地目录插件，不急着做市场化安装。
- P3：LSP/符号级代码理解。新增 `lsp_hover`、`lsp_definition`、`lsp_references`、`diagnostics` 工具，优先支持 Python，通过 pyright 或 ruff server 获取诊断，减少纯文本搜索误判。
- P3：远程/自动化任务。参考 Claude Code 的 cron/schedule/remote session，先做本地定时任务与后台 monitor，后续再考虑 WebSocket/remote bridge。

## Test Plan
- P0 阶段必须新增：多 runtime 工具隔离测试、复杂 schema 类型测试、sync/async shell parity 测试、会话压缩 resume 测试、Sandbox 文件工具错误语义测试。
- P1 阶段必须新增：权限模式矩阵测试、read-only/destructive 工具分类测试、edit/multi-edit 成功失败测试、grep/glob `.gitignore` 和 max results 测试、TUI interaction callback 单元测试。
- P2 阶段必须新增：子 agent 独立 registry/session 测试、background agent cancel/output/follow-up 测试、MCP resource tool mock 测试、SDK event stream contract 测试。
- 每个阶段验收命令：`uv run pytest -q` 必须全绿；targeted ruff 从 touched files 开始，逐步推进到 `src/opennova/runtime src/opennova/tools src/opennova/security src/opennova/session`。

## Assumptions
- 优先目标是“向 Claude Code 的工程体验和稳定性看齐”，不是复制其 TypeScript/React/Ink 技术栈。
- 近期以不破坏现有 CLI/TUI、工具名和配置兼容为默认策略。
- 先修真实 bug 和工具协议基础，再做大功能；工具协议和权限模型是后续功能的地基。
- 当前测试全绿说明可按增量方式推进，不需要重写核心架构。
# OpenNova 向 Claude Code 对齐开发计划

本文档跟踪 OpenNova 向 Claude Code 工程体验对齐的后续改造。状态会随代码推进更新。

## 当前状态

- 分支：`master`
- 验收基线：每批改造后运行 targeted ruff 与 `uv run pytest -q`
- 策略：优先修真实 bug 和工具协议地基，再推进中长期能力

## P0 / P1 已完成

- [x] 修复 `ToolRegistry` 单例污染，改为 runtime 实例级 registry。
- [x] 补齐工具参数 JSON Schema 推断：`list`、`dict`、`Literal`、`Enum`、`Union/Optional`。
- [x] 统一 `execute_command` 同步/异步执行前校验与 timeout 类型语义。
- [x] 清理 targeted lint 问题：`runtime/loop.py`、`session/manager.py` 等。
- [x] 修复 session 压缩恢复风险，新增 `load_session_with_summary()`。
- [x] 文件工具切到统一 Sandbox 错误语义，并移除遗留 `_validate_path()`。
- [x] 新增声明式工具元信息基础：只读、破坏性、权限、并发安全、搜索提示、结果上限字段。
- [x] Guardrails 增加权限模式与工具规则：`default`、`ask`、`allowEdits`、`readOnly`、`bypass`。
- [x] 新增精细编辑工具：`edit_file`、`multi_edit_file`。
- [x] 新增结构化搜索工具：`glob_files`、`grep_code`。

## P2 已推进

- [x] SDK/headless 初版：`OpenNovaClient`、session runtime 管理、事件流输出。
- [x] 工作树开发流初版：`enter_worktree`、`exit_worktree` 工具。
- [x] TUI 工具进度与审批体验增强：新增 `ToolProgressTracker` 并接入状态栏/确认等待。
- [x] 子 agent 隔离增强：独立 registry、working memory、session id、permission context。
- [x] 分层长期记忆：`.opennova/memory/**/*.md` 手动记忆读取、截断、去重注入。
- [x] MCP resources：`list_mcp_resources`、`read_mcp_resource`、metadata/权限统一。

## P3 待做

- [x] Hook 与本地插件系统基础：`.opennova/hooks/*.py`、`pre_tool_use`、`post_tool_use`。
- [x] LSP/符号级代码理解基础：新增 `python_diagnostics`，后续再扩展 hover/definition/references。
- [x] 远程/自动化任务基础：新增 `LocalAutomationScheduler`，后续再接 CLI/TUI/remote bridge。

## 验收测试清单

- [x] 多 runtime 工具隔离测试。
- [x] 复杂 schema 类型测试。
- [x] sync/async shell parity 测试。
- [x] 会话压缩 resume 测试。
- [x] Sandbox 文件工具错误语义测试。
- [x] edit/multi-edit 成功失败测试。
- [x] grep/glob `.gitignore` 与 max results 测试。
- [x] SDK event stream contract 测试。
- [x] worktree tool contract 测试。
- [x] 子 runtime registry/session/permission 隔离测试。
- [x] MCP resource connector/manager/tool 测试。
- [x] layered memory 注入与截断测试。
- [x] TUI tool progress 状态测试。
- [x] hooks 加载与 pre/post tool use 测试。
- [x] python diagnostics 工具测试。
- [x] local automation scheduler 测试。

## 下一批建议

1. 深化 TUI：把 ToolProgressTracker 扩展为可折叠工具结果面板、diff preview 和统一审批弹窗。
2. 深化插件系统：让插件声明 tools、commands、skills、MCP server，并提供安全清单。
3. 深化代码理解：在 `python_diagnostics` 基础上接 pyright/ruff server，补 hover/definition/references。
