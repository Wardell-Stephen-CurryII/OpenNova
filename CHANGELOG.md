# Changelog

All notable changes to OpenNova are recorded here. Versions and dates are reconstructed from
the commits that changed the package version in `pyproject.toml` and `src/opennova/__init__.py`.

## [Unreleased]

## [0.4.2] - 2026-07-15

### Added

- Added context-line support to `grep_code`.
- Added GitHub Actions quality checks and Python 3.11/3.12 test jobs for pushes and pull requests.
- Added runtime-owned cancellation handles spanning shell processes, MCP, web requests, sub-agents,
  automation, the SDK, and the TUI.
- Added workspace- and content-digest-bound trust for project hooks and plugin contributions.
- Added centralized model profiles and a side-effect-free tool inspection catalog.

### Changed

- Expanded the TUI workbench with richer context, task, activity, file, decision, and source
  presentation.
- Reduced unnecessary approval prompts in `auto` permission mode while preserving hard blocks,
  explicit policy rules, sandbox restrictions, and audit records.
- Limited permission prompts to the actionable approve-or-cancel choices.
- Isolated task managers, tool identifiers, event listeners, and cleanup ownership by runtime and
  session.
- Strengthened session path identity, process sandbox read boundaries, provider error handling,
  plugin contribution refresh, and SDK resource lifecycle management.

### Fixed

- Fixed semantic Plan/Act workflow routing and provider handling for routing-only model responses.
- Fixed persistent memory records being routed to the wrong memory category.
- Fixed handling of multiple tool calls returned in a single model response.
- Restored mouse selection and clipboard copying in the right-side workbench.
- Resolved repository-wide Ruff violations and added code-quality regression coverage.
- Corrected the pinned `setup-uv` action version in CI.
- Fixed automatic context compression, repeated-summary interpolation, atomic assistant/tool
  message insertion, and Anthropic tool-only serialization.
- Prevented configuration and tool-event output from exposing expanded secrets.
- Prevented untrusted, revoked, or content-drifted project extensions from remaining active in the
  current runtime.

## [0.4.1] - 2026-07-12

### Added

- Added canonical runtime state projections and subscriptions for consistent TUI and SDK updates.
- Added atomic JSONL session schema v2 snapshots with replayable transcript events, plan/runtime
  state, revision data, legacy loading, and exact duplicate-message collapse.
- Added `request`, `auto`, and `full` permission modes with explicit hard-block and policy limits.
- Added English translations of the quickstart and tutorial.

### Changed

- Updated all current user and developer documentation for the Textual-only interactive
  experience.
- Documented in-place message selection, clipboard fallbacks, Windows IME support, the session
  picker, transcript replay, same-session resume persistence, 39 built-in tools, security,
  extensions, SDK events, and current architecture.
- Marked development plans 01-13 as archived design records rather than current product
  documentation.
- Improved plan decisions and synchronization, plan step reindexing, saved-plan parsing, Option
  shortcuts, progress presentation, and manual scroll preservation.

### Fixed

- Fixed plan execution state transitions and blocked implementation tools while Plan mode is
  active.
- Fixed interrupted approved plans so they can resume from persisted state.
- Restored Windows TUI mouse-wheel scrolling.
- Updated the generated project-guide command example so it no longer recommends the removed
  `opennova tui` command.

## [0.4.0] - 2026-07-01

### Added

- Added the right-side TUI workbench for context, tasks, plans, todos, and tool activity.
- Added the complete plan-execution feature set: interactive tool cards, approvals, cancellation,
  checkpoints, automation commands, plugin commands, diagnostics, and transcript integration.
- Added a session picker with visible transcript replay and same-session resume persistence.
- Added namespaced Skills loading, conditional discovery, skill hooks, and invocation controls.
- Added security audit logging, network and secret policies, MCP security context, and OS process
  sandbox support.
- Added inline TUI text selection and platform-aware copy shortcuts.

### Changed

- Made Textual the only interactive interface and removed the legacy line-oriented REPL and
  standalone `opennova tui` command.
- Refined tool-call presentation, plan progress tracking, workspace layout, and TUI message
  styling.
- Expanded automation, plugin trust/lock/drift handling, checkpoint workflows, and diagnostic
  reporting.

### Fixed

- Fixed duplicate streamed answers and several selection/copy shortcut edge cases.
- Fixed resume-picker input handling and ensured resumed conversations continue writing to their
  original session id.
- Improved plan execution tracking across approval, execution, interruption, and completion.

## [0.3.0] - 2026-06-20

### Added

- Added the headless `OpenNovaClient` SDK and normalized SDK/runtime events.
- Added trusted project plugins, hooks, local automation, checkpoints, transcript export, layered
  memory, worktree tools, MCP resources, Python diagnostics, and symbol navigation.
- Added `TodoWrite`, canonical tool events, permission rules, richer tool metadata, and background
  activity presentation.
- Added multi-question `ask_user_question` dialogs with free-text support and content redaction.
- Added LLM-driven `/init` project-guide generation with automatic project-memory injection.
- Added native Windows TUI input handling, IME support, key mapping, and diagnostics.
- Added native system clipboard fallbacks through `pbcopy`, `clip`, `wl-copy`, and `xclip`.

### Changed

- Switched the default provider/model settings to DeepSeek v4 Pro.
- Hardened file sandbox guardrails, shell execution paths, plugin trust, and external integration
  boundaries.
- Refreshed the README, quickstart, tutorial, and API documentation for the expanded runtime.

### Fixed

- Fixed optional-integer tool schema generation and improved `execute_command` schemas.
- Fixed Windows interactive input and several REPL/TUI configuration inconsistencies.

## [0.2.3] - 2026-05-18

### Added

- Added JSONL conversation sessions with `/resume` and `/sessions` commands.
- Added automatic context compression for long conversations.
- Added selectable and copyable TUI message text while retaining Rich-rendered colored output.
- Added real-time TUI completion hints for slash commands, Skills, and history.

### Changed

- Improved TUI layout, streaming buffering, tool-result presentation, and user-message styling.
- Suppressed noisy raw command output while retaining useful result status.

### Fixed

- Fixed session messages not being persisted.
- Fixed conversation context being lost between turns.
- Fixed TUI selection overlays, clipboard behavior, message/input overlap, and completion cycling.

## [0.2.2] - 2026-05-17

### Added

- Added the Textual split-pane TUI and its slash-command handlers.
- Added DeepSeek v4 Pro/Flash support and thinking-mode `reasoning_content` passthrough.
- Added progressive disclosure and system-prompt integration for Markdown-based Skills.
- Added REPL history/slash completion, elapsed-time spinners, colored diffs, model/provider
  presentation, and double-Ctrl+C exit handling.

### Changed

- Rebuilt Skills around Claude Code-style directory packages and preserved context during TUI
  Skill execution.
- Increased the one-shot task iteration allowance for longer agent runs.

### Fixed

- Fixed TUI focus, Enter submission, background task refresh, duplicate submission, busy-state,
  and application lifecycle deadlocks.
- Fixed Skill discovery and invocation flow and hardened interactive question option parsing.

## [0.2.0] - 2026-04-15

### Added

- Added the diff engine, project/working memory, context compression foundations, planner, and
  security guardrails.
- Added MCP stdio/SSE integration and the first directory-based Skills registry.
- Added task management, foreground/background sub-agents, follow-up message delivery, dependency
  graphs, and persistent plans.
- Added `AskUserQuestion`, Plan mode, web search/fetch, and Git integration tools.
- Added approval-gated plan execution with unified runtime state and memory integration.
- Added slash-command completion plus comprehensive quickstart, tutorial, and API documentation.

### Changed

- Consolidated Skill loading and improved installed CLI documentation and interactive commands.
- Stabilized MCP configuration, validation, diagnostics, and runtime integration.

### Fixed

- Fixed REPL scrolling, submission, Ctrl+D handling, error reporting, tool-call argument parsing,
  and finish-reason compatibility.
- Fixed SOCKS proxy dependencies and several MCP and Skill edge cases.

## [0.1.0] - 2026-03-28

### Added

- Introduced the OpenNova package, configuration system, Click entry point, and interactive CLI.
- Added OpenAI, Anthropic, and DeepSeek providers behind a shared provider interface.
- Added the initial ReAct agent runtime, conversation context, tool registry, and streaming support.
- Added the foundational file, shell, search, and editing tools.

[Unreleased]: https://github.com/Wardell-Stephen-CurryII/OpenNova/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/Wardell-Stephen-CurryII/OpenNova/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Wardell-Stephen-CurryII/OpenNova/releases/tag/v0.4.1
[0.4.0]: https://github.com/Wardell-Stephen-CurryII/OpenNova/compare/075ab4b...6865b4b
[0.3.0]: https://github.com/Wardell-Stephen-CurryII/OpenNova/compare/f577ef6...075ab4b
[0.2.3]: https://github.com/Wardell-Stephen-CurryII/OpenNova/compare/ddb16b7...f577ef6
[0.2.2]: https://github.com/Wardell-Stephen-CurryII/OpenNova/compare/9ed9dd4...ddb16b7
[0.2.0]: https://github.com/Wardell-Stephen-CurryII/OpenNova/compare/347565a...9ed9dd4
[0.1.0]: https://github.com/Wardell-Stephen-CurryII/OpenNova/commit/347565a
