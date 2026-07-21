# OpenNova 14 计划：运行可靠性、隔离执行与 Agent Runtime 收口

> 归档说明：这是基于 OpenNova 0.4.1 当前代码重新审查后形成的实施计划，不代表当前用户命令或已交付功能。

## Summary

14 计划不沿用 `13_plan.md` 的后续建议，而是重新审查当前 `master`，并独立抽样对照本地 Claude Code 源码中的 Query Engine、ToolUseContext、AbortController、权限、压缩、会话、SDK 和工具调度设计。

当前基线具有较好的功能测试数量，但“测试全绿”尚未等价于运行可靠：

- `495 passed`，覆盖率约 `73%`，`ruff check` 通过。
- `ruff format --check` 报告 74 个文件需要格式化。
- UTF-8 环境下 `mypy src/opennova` 仍有 256 个错误；默认非 UTF-8 环境下 mypy 会先在中文路径启动失败。
- 核心模块过于集中：`runtime/agent.py` 约 1600 行、`runtime/loop.py` 约 1500 行、`cli/tui.py` 约 2480 行。
- 现有测试对 `mcp/connector.py`、provider、config、TUI 主流程和自动压缩等关键边界覆盖不足。

本轮目标不是继续堆叠表面功能，而是把 OpenNova 收口为一个可持续演进的 Agent Runtime：每个会话有独立状态和资源所有权，工具调用可以安全取消、并行和恢复，配置与扩展有明确的信任边界，SDK/TUI 消费同一套事件协议，长会话不会因压缩或结果过大破坏模型消息协议。

## Fresh Audit Findings

### P0：必须修复的真实缺陷

1. **自动上下文压缩实际上不会执行**
   - `ContextManager._maybe_compress()` 先把 `_compressing` 设为 `True`，随后 `compress()` 因 `_compressing` 为真立即返回 `False`。
   - `ContextCompressor.build_compression_prompt()` 把 `{previous_summary}` 保留成了字面量，重复压缩不会携带真实历史摘要。
   - `ContextManager.add_message()` 失败时调用方大多忽略返回值，极端情况下可能丢掉 assistant/tool 配对消息并破坏 provider 协议。

2. **会话目录在中文项目路径下会碰撞**
   - `_sanitize_path()` 把所有非 ASCII 字符替换为下划线，例如不同的中文项目路径可以映射到同一个目录。
   - `load_session()`、`resume_session()` 等入口没有统一验证 UUID 和最终路径 confinement。
   - 需要改为“可读 slug + 规范化绝对路径哈希”，并兼容迁移旧目录。

3. **配置命令会泄露展开后的密钥**
   - Click `opennova config` 和 TUI `/config` 直接 dump `config.data`。
   - 环境变量已在加载阶段展开，因此 API Key 会原样出现在终端、滚屏或录屏中。
   - 所有配置展示、诊断和日志必须经过统一 redactor，默认只显示来源和掩码值。

4. **配置对象类型不统一会破坏受信插件启动**
   - 主入口向 `AgentRuntime` 传入 `Config`，但插件加载路径按 `dict` 调用 `setdefault()`。
   - 受信插件声明 MCP server 等贡献时，正常 CLI/TUI 启动可能触发 `AttributeError`。
   - `Config()` 还使用 `DEFAULT_CONFIG.copy()` 浅复制，嵌套 `set()` 会污染进程内全局默认配置。

5. **runtime 和子 agent 仍共享进程级任务状态**
   - `task_tools.py` 使用 `_global_task_manager`，每个 `AgentRuntime` 初始化都会覆盖它。
   - 多 SDK session、子 agent 或并行 runtime 会读写错误的任务表和 agent 消息队列。
   - `TaskManager`、任务输出目录和 background handle 必须由 session/runtime 显式注入。

6. **canonical tool id 跨轮次重复**
   - 每次创建 `ReActLoop` 都把序号重置为 0，TUI 的 `ToolCardStore` 却跨轮次保留。
   - 第二轮的 `tool_0001` 会覆盖第一轮卡片，SDK/transcript 也无法稳定关联调用。
   - ID 应包含 session/run/model tool-call identity，并在同一会话内全局唯一。

7. **取消没有贯穿到底层执行资源**
   - `ToolUseContext.abort_signal` 尚未被工具使用。
   - TUI `Ctrl+C` 取消 agent coroutine，但 `execute_command` 的异步子进程及其后代可能继续运行。
   - runtime 没有保证发出真实 `tool_cancelled` 事件，Tool Card 的 cancel 目前主要是显示状态。
   - MCP 请求、web 请求、sub-agent 和 automation 也缺少统一取消语义。

8. **项目 hook 在信任确认前执行 Python 代码**
   - `AgentRuntime` 初始化时直接加载 `.opennova/hooks/*.py`。
   - 克隆陌生仓库后，仅运行 `opennova` 或某些检查命令就可能导入并执行项目代码。
   - plugin trust 只按名称持久化，插件内容变更后仍保持受信；manifest 的 `signature` 当前也未做密码学验证。

9. **macOS process sandbox 的读权限过宽**
   - Seatbelt profile 使用全局 `(allow file-read*)`，只能限制写入，不能防止命令读取项目外敏感文件。
   - profile 临时文件没有可靠清理；backend 不可用且 `enforce=false` 时只写 metadata，用户界面不一定看到降级。
   - 需要按系统只读根、项目根、显式 allowed paths 构建 read allowlist，并显式展示 fallback。

10. **provider 消息序列化存在协议风险**
    - Anthropic tool-only assistant message会生成空 text block，可能被 API 拒绝。
    - provider 对 malformed tool arguments、连续 tool result、max-output、rate limit 和 retry exhaustion 缺少统一错误类型与恢复策略。
    - 模型能力、context window 和输出预算分散在多个硬编码表中，容易不一致。

11. **SDK 重复提交会累积事件监听器**
    - `OpenNovaClient.stream_message()` 注册多个 callback，但没有在 generator 结束时调用 unsubscribe。
    - 同一 SDK session 多轮调用后可能重复收到事件并持续持有闭包。
    - SDK 还缺少 session close、run cancel、并发提交保护和资源关闭协议。

12. **长期记忆存在未接线且已损坏的旧实现**
    - `MemoryExtractor` 未进入 runtime 主链路，包含缺失 `reference` 容器、无捕获组 pattern 和 `FeedbackType.NEATIVE` 拼写错误。
    - 当前真正生效的是 `OPENNOVA.md`、`.opennova/memory/*.md` 和 `ProjectMemory`，应统一产品语义，避免同时维护两套互不相干的记忆系统。

### P1：功能与工程优化

1. **建立唯一的 ToolExecutionEngine**
   - 把 schema 校验、argument normalization、hook、guardrails、permission、checkpoint、执行、截断、audit 和 event emission 从 `ReActLoop` 抽成一条可测试 pipeline。
   - `ToolUseContext` 由 runtime 创建并传给所有内置、插件和 MCP 工具，不再只是未使用的 metadata 容器。

2. **按工具元信息执行有界并行**
   - 当前模型一次返回多个工具调用时仍逐个串行执行。
   - 对 `is_concurrency_safe()` 为真的 read/search/status 工具使用 bounded task group；写工具、交互工具和显式 barrier 保持有序。
   - tool results 必须按原始 tool-call 顺序回填模型上下文，而不是按完成顺序回填。

3. **增加文件版本缓存和乐观并发控制**
   - 读取文件时记录 canonical path、mtime、size 和 content hash。
   - `edit_file` / `multi_edit_file` 写入前验证文件未被用户、hook 或其他 agent 修改；冲突时返回新上下文和可重试建议。
   - 把 `ToolUseContext.read_file_cache` 变成真实 session-scoped file state cache。

4. **统一工具结果预算与 artifact offload**
   - `BaseTool.max_result_chars` 当前没有被 runtime 真正执行，各工具自行截断，行为不一致。
   - runtime 统一保留模型摘要、首尾片段和完整结果 artifact 引用；TUI 可展开完整结果，模型只接收有预算的内容。
   - 单轮多个并行结果还需要总预算，避免每个工具都达到独立上限后撑爆上下文。

5. **引入 deferred tools / Tool Search**
   - 39 个工具 schema 每轮全部注入会增加 token、延迟和误选概率。
   - 保留核心文件、shell、search 和 plan 工具常驻，其余按搜索提示、当前任务、Skill 和 MCP server 动态加载。
   - 工具发现结果必须进入 transcript，保证 resume 后可重放同一能力集合。

6. **完善搜索语义**
   - 当前 `.gitignore` 解析不支持 negation、anchored pattern、目录规则和嵌套 `.gitignore`。
   - 使用成熟 pathspec 语义或受控的 `git check-ignore`，并增加 binary/size/cancel limits。
   - Glob/Grep/Python indexer 共用同一 ignore service，避免每个模块维护一套排除规则。

7. **拆分运行时与 TUI 巨型模块**
   - `AgentRuntime` 拆为 bootstrap/session/extensions/execution facades。
   - `ReActLoop` 只保留模型迭代和状态转换，工具执行交给 engine。
   - `OpenNovaTUI` 拆为 session controller、run controller、command handlers 和独立 Textual widgets，减少通过 `getattr`/`suppress` 隐藏错误。

8. **增加纯检查启动模式**
   - `list-tools`、config inspection、doctor 等命令不应创建 provider、session、MCP 连接或执行项目 hook。
   - 增加 `RuntimeBootstrapProfile`：`inspect`、`bare`、`interactive`、`headless`，每种 profile 明确允许的副作用。

9. **升级 MCP 生命周期**
   - 使用包版本作为 client info，协商 protocol version。
   - 支持 tools/resources pagination、list_changed、resource templates、prompts、roots、elicitation 和请求取消。
   - transport、listener task、pending request 和动态 tool registration 都由 runtime close protocol 回收。

10. **将 checkpoint 升级为 turn-level file history**
    - 当前 checkpoint 不能完整撤销新建文件，删除后的 diff 也可能因目标文件不存在而失败。
    - 记录 create/modify/delete tombstone、文件 hash、对应 user message/run/tool id。
    - 提供 session fork、rewind preview 和冲突检测；默认不静默覆盖用户在 checkpoint 后的新修改。

11. **建立统一 ModelProfile 与预算控制**
    - 每个 provider 暴露 context window、max output、tool use、thinking、vision、structured output 和 token estimator 能力。
    - 支持 `max_turns`、token/cost budget、fallback model、retry/backoff 和 circuit breaker。
    - workflow routing 可使用轻量模型或本地高置信规则，避免每个普通 turn 固定多一次完整模型请求。

12. **收口记忆产品语义**
    - 保留显式、可审计的 `OPENNOVA.md` 与 `.opennova/memory/`。
    - 自动记忆必须有 provenance、scope、expiry、用户查看/删除入口和注入预算，不再使用脆弱正则静默推断偏好。
    - 对重复文件、嵌套项目说明和 resume 压缩摘要做统一去重。

### P2：向 Claude Code / Codex 对齐的新能力

1. **Headless CLI 与稳定 SDK contract**
   - 支持 text/json/stream-json 输出、structured output JSON Schema、partial messages、max turns、budget、allowed/disallowed tools、no-session-persistence。
   - SDK 提供 `aclose()`、`cancel_run()`、`fork_session()`、持久会话列表和严格 event schema version。

2. **workspace trust 与安全启动页**
   - 首次进入项目时明确展示将加载的 hooks、plugins、MCP、additional paths 和 process sandbox 状态。
   - trust 绑定 canonical project identity 与内容 digest；发生 drift 时降级为只解析、不执行。

3. **真实后台服务与任务通知**
   - 当前 automation daemon 只是进程内 running flag 和手动 tick。
   - 后续实现有锁的独立 runner、PID/lease、崩溃恢复、并发上限、run history、OS/TUI notification 和安全上下文继承。

4. **多语言 LSP 服务层**
   - 当前 Python analysis server manager 主要是生命周期占位，hover/definition/references 仍以浅 AST 为主。
   - 建立可选 JSON-RPC language server client，先支持 Python，再扩 TypeScript/Go/Rust；AST 保持无依赖 fallback。

5. **会话分叉、时间线与文件 rewind**
   - Resume 可选择原会话继续或 fork 新会话。
   - 时间线关联 user message、assistant response、tool events、permission decisions、file history 和 compact boundary。
   - TUI 支持选择一个 turn 预览并恢复文件，同时保留原 transcript 作为审计记录。

6. **可观测性与 doctor**
   - 新增无敏感信息的启动耗时、首 token、模型调用、工具耗时、压缩、重试和 sandbox fallback 指标。
   - `/doctor` 输出 provider、encoding、MCP、sandbox、hook/plugin trust、session storage 和 optional backend 状态，但永不打印密钥。

## Implementation Plan

### 14.1 Reliability Hotfix

- 修复自动压缩 reentrancy 和 previous summary 插值。
- 把消息插入改成显式结果：成功、已压缩后重试、不可容纳；禁止静默丢弃 assistant/tool message。
- 修复 Anthropic tool-only 序列化与 provider-neutral protocol fixtures。
- tool id 改为 session/run/tool-call 组合，保证跨轮次唯一。
- 修复 SDK callback cleanup。
- 为以上缺陷先写回归测试，再修改实现。

### 14.2 Identity, Config and Trust

- 引入不可变、深复制的 typed config snapshot，统一 `Config` 与 runtime 内部接口。
- 配置展示使用 central redactor；inspection profile 不初始化 provider 或执行扩展。
- 会话目录使用 slug + path hash，添加旧目录只读发现和原子迁移。
- 所有 session id 入口统一 UUID 校验和 path confinement。
- 新增 workspace trust store；project hooks 和 plugin active contributions 只有在 trust + digest 匹配时加载。
- 插件 manifest schema、lockfile、trust record 和 MCP contribution 使用同一 canonical digest。

### 14.3 Execution Ownership and Cancellation

- 删除全局 TaskManager，所有 task/agent tools 接收 runtime-owned manager。
- 新增 `RunHandle`、`ToolExecutionContext` 和 cancellation token；由 session runtime 统一拥有。
- shell 使用独立 process group，取消时先 terminate、限时后 kill，并清理 profile/temp artifacts。
- MCP/web/sub-agent/automation 接收同一 cancellation signal。
- 无论成功、失败、拒绝还是取消，每个 `tool_start` 必须恰好对应一个 terminal event。
- 收紧 Seatbelt read roots，并把 sandbox applied/fallback 作为用户可见状态。

## P0 实施状态（2026-07-15）

- [x] 修复自动压缩 reentrancy、previous summary 插值、失败熔断和 tool protocol 原子写入。
- [x] 会话目录改为 Unicode slug + 规范路径哈希，统一 UUID/confinement 校验并保守迁移旧会话。
- [x] 配置深复制、Config/dict 兼容和递归密钥脱敏已接入 CLI、TUI、tool event、observation 与 transcript。
- [x] 删除进程级 TaskManager；runtime、SDK session 和 child agent 分别拥有任务状态及输出命名空间。
- [x] canonical tool id 包含 run identity 和调用序号，同一 session 多轮不再覆盖。
- [x] RunHandle/CancellationToken 已贯穿 shell、MCP、web、sub-agent、automation 和 SDK/TUI 关闭链。
- [x] shell 使用独立进程组，取消/超时终止后代并清理 sandbox 临时 profile；cwd 受项目 confinement 约束。
- [x] project hooks 与 plugins 使用仓库外、workspace + content digest 绑定的信任记录；trust、untrust 或内容漂移时，当前 runtime 的 tools、hooks、skills、commands 与 MCP 配置立即同步。
- [x] Seatbelt 移除全局 file-read，改为系统运行时根、项目根和显式路径 allowlist；fallback 对用户可见。
- [x] provider tool-only、多 tool result、malformed args、stream cancellation、retry exhaustion 和模型能力统一处理。
- [x] SDK callback 每轮注销，支持 cancel/close/aclose、并发提交保护和异步 context manager。
- [x] 损坏且未接线的旧 MemoryExtractor 已修复并明确降级为显式兼容 API，主记忆语义保持单一。

P0 验证结果：UTF-8 中文路径环境下全量 `540 passed`，`ruff check src tests` 通过；runtime、provider、session、config、trust、sandbox、memory 和 SDK 的本轮涉及模块 targeted mypy 为 0 error。

14.4-14.6 仍是后续阶段，不包含在本次 P0 交付中。当前全仓 `ruff format --check` 仍有 42 个历史文件待格式化，全仓 mypy 为 193 errors / 26 files，主要集中在旧 `BaseTool.execute` override、TUI typing 和历史工具模块；需按 14.6 单独治理，不能把 targeted 检查误写成全仓门禁完成。

## P1 实施状态（2026-07-21）

- [x] 抽出 `ToolExecutionEngine`，hook、guardrails、permission、checkpoint、audit、redaction、working memory 和 canonical event 使用同一执行管道。
- [x] 基于 `is_concurrency_safe()` 实现有界并行调度，写工具和 barrier 保持串行，observation 按原 tool-call 顺序回填。
- [x] 引入 session-scoped `FileVersionCache`，读取后记录 path/mtime/size/hash，写入前阻止 stale edit 并返回重试建议。
- [x] 引入 per-tool/per-turn 结果预算和 `.opennova/artifacts/<session>/` 原始结果落盘。
- [x] 新增 `tool_search` 延迟工具发现，发现结果进入 transcript，resume 可从 tool observation 恢复暴露集合。
- [x] Glob/Grep/Python indexer 复用 `GitIgnoreService`，支持 nested rules、negation、anchored/directory pattern，并统一 binary、size 和 cancellation 边界。
- [x] 建立 `inspect/bare/interactive/headless` bootstrap policy，`list-tools` 和 `doctor` 不创建 provider/session，不加载项目扩展。
- [x] MCP 补齐客户端版本、分页、tools list-changed 动态注册、resource templates、prompts、roots、elicitation 和请求取消。
- [x] checkpoint 记录 create/modify/delete tombstone、before/after hash、run/user/tool identity；`rewind` 默认预览且冲突时拒绝覆盖。SessionManager、SDK 和 TUI 支持会话 fork。
- [x] `ModelProfile` 统一能力与 token estimate，ReAct run 支持 turn/token/cost/output 预算、retry/backoff、fallback provider、circuit breaker 和本地高置信 workflow routing。
- [x] `.opennova/memory/` 支持 provenance、scope、expiry、归一化段落去重与 `/memory list|add|delete`，旧的普通 Markdown 文件保持兼容。
- [x] 巨型模块已先拆出 execution engine、model policy、artifact/file-state service 和 slash-command dispatcher；完整 TUI widget/controller 重组保留为后续非兼容性 UI 专项。

P1 不包含 14.5 中的 automation 可恢复 runner 和真实 LSP client，也不代替 14.6 全仓 format/mypy/coverage 门禁治理。

P1 验证结果：UTF-8 中文路径环境下全量 `552 passed`，`ruff check src tests` 通过；本次新增的 execution/model-policy/file-state/artifact/tool-search/ignore/memory/command-dispatch 模块 targeted mypy 为 0 error。全仓 format 和 mypy 仍按 14.6 的历史债务状态单独治理。

### 14.4 Tool Engine and Context Efficiency

- 抽出 `ToolExecutionEngine`，统一执行 pipeline 和结果预算。
- 加 bounded parallel scheduler、batch barriers、写集合冲突检测和 ordered observations。
- 建立 file state cache 与 stale-write protection。
- 引入 artifact store 和 per-tool/per-turn output budget。
- 改造 deferred tool catalog / Tool Search，记录每轮实际暴露的工具集合。
- 统一 ignore/path service，修复 nested gitignore 语义。

### 14.5 Product Surfaces

- SDK/headless CLI 共享 schema-versioned event stream、cancel/close/fork/budget API。
- TUI 使用独立 run/session/tool-card controllers，不再自行推导 canonical identity。
- checkpoint 迁移为 turn-level file history，并提供 rewind preview/fork。
- MCP 增加 pagination、capability change、templates/prompts/elicitation 和 cancellation。
- automation 从手动 tick facade 升级为可恢复 runner；diagnostics 接真实可选 LSP client。

### 14.6 Engineering Gate

- 先修 core/runtime/security/session/provider 的 mypy，再扩到全部 `src/opennova`，最终 0 error。
- 统一 ruff format，并在 CI 增加 `ruff format --check` 和 mypy。
- 修复 `BaseTool.execute(**kwargs)` 造成的大量错误 override，改成 generic protocol 或 schema-first adapter。
- 为 macOS Seatbelt、Linux bubblewrap、Windows cancellation/driver 建立分平台测试。
- 总覆盖率提升到至少 80%，security/session/context/execution engine 的分支覆盖率至少 90%。

## Test Plan

### Regression Tests

- 自动压缩超过阈值后确实执行，重复压缩包含真实 previous summary，失败后有 circuit breaker。
- 超大 tool result 不会丢失协议配对；assistant tool call 和所有 tool result 始终完整成组。
- 同一 session 多轮调用的 tool id 不重复，SDK/TUI/transcript 使用同一 canonical id。
- SDK 连续提交 100 轮后 listener 数量稳定，关闭 session 后 provider/MCP/task 均释放。
- 两个 runtime 和 child agent 的 tasks、todos、callbacks、permissions、sessions 不互相污染。
- 取消 shell 会终止父进程和后代，发出一次 `tool_cancelled`，不留下 profile 或 pending task。
- 两个不同中文路径映射到不同会话目录；旧会话可发现和迁移；非法 session id 被拒绝。
- `opennova config`、TUI `/config`、doctor、audit 和错误信息不包含真实 API key。
- 未信任或 digest drift 的 hook/plugin 不执行；inspection commands 永不加载可执行项目扩展。
- Seatbelt profile 不再全局允许任意文件读取，backend fallback 可观测且 enforce 模式 fail closed。
- Anthropic/OpenAI/DeepSeek protocol fixtures 覆盖 tool-only、multiple tools、malformed args、length、retry 和 cancellation。

### Execution Engine Tests

- 独立 read/search 工具并行运行，写工具与 barrier 保持串行。
- 并行结果即使完成顺序不同，也按原 tool-call 顺序写回上下文。
- stale file hash 阻止覆盖用户新修改，并返回可重试上下文。
- 单工具和单轮总结果预算都生效，完整 artifact 可由 TUI/SDK 读取。
- deferred tool 只有被选中后进入下一次模型请求，resume 后工具集合可恢复。
- nested `.gitignore`、negation、hidden、binary、max results 和 cancellation 行为一致。

### Product and CI Tests

- stream-json 事件满足 versioned contract，支持 cancel/close/fork/no-persistence。
- MCP mock server 覆盖分页、list_changed、resource templates、prompts、elicitation 和断线重连。
- turn-level rewind 覆盖 create/modify/delete、冲突预览和 fork session。
- 中文路径下运行 pytest、ruff、format 和 mypy；Python 3.11/3.12 全绿。
- CI 至少执行：

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest --cov=opennova
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run mypy src/opennova
```

## Acceptance Criteria

- 自动压缩在真实多轮对话中可触发、可恢复且不会破坏 tool protocol。
- 取消运行后没有存活的 shell/MCP/sub-agent 执行资源，terminal tool event 完整闭合。
- 同一进程中的多个 runtime、SDK session 和 child agent 状态完全隔离。
- 配置、日志、诊断和 transcript 不因展示功能泄露已展开密钥。
- 未经 workspace trust 的项目 Python hook、plugin tool 和 plugin MCP 不会执行或连接。
- 中文路径会话不碰撞，旧会话迁移不丢数据，所有 session path 均受 confinement 保护。
- `ruff check`、`ruff format --check`、mypy 和全量 pytest 在 CI 中全部通过。
- `opennova list-tools` 等 inspection command 在无 API key、无网络、无 session side effect 下可运行。
- 核心模块不再依赖进程级可变单例；工具执行、事件和资源释放拥有清晰 owner。

## Assumptions

- 14 计划以当前 OpenNova 0.4.1 代码为依据，不把历史计划中的“下一步建议”当作输入。
- 优先修复安全性、数据完整性和运行可靠性，不以新增工具数量作为阶段完成标准。
- 保持现有工具名、主要 slash command 和 JSONL v2 会话可迁移兼容。
- 不要求复制 Claude Code 的 TypeScript/Ink 技术栈，只借鉴其 per-session engine、abort propagation、tool context、预算和协议边界。
- OS sandbox 仍以 Seatbelt/bubblewrap 为实现范围，不在本轮引入容器、VM 或远程执行平台。
- 新的 workspace trust、strict sandbox 和 headless options 需要同步更新产品文档，但 `docs/develop/` 继续只保存历史实施记录。
