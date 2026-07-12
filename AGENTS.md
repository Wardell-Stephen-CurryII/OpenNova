# AGENTS.md

This file describes the current OpenNova repository for coding agents and contributors.

## Project

OpenNova 0.4.1 is a Python 3.11+ terminal AI coding agent. Textual is the only interactive interface; the legacy interactive CLI and standalone `opennova tui` command have been removed. The Click entry point remains for launching the TUI, setup/inspection commands, and one-shot non-interactive tasks.

Core capabilities include multi-provider LLM access, a ReAct runtime, 39 built-in tools, Plan/Act workflows, persistent sessions, transcript replay, context compression, layered memory, Skills, trusted plugins, hooks, MCP, worktrees, local automation, checkpoints, and security policies.

## Setup and commands

```bash
uv sync
uv sync --dev
uv run opennova init

# Textual TUI
uv run opennova
uv run opennova --resume
uv run opennova --continue

# One-shot tasks
uv run opennova run "Read README.md"
uv run opennova run --plan "Refactor session persistence"
uv run opennova run --provider deepseek -m deepseek-v4-pro "Review src/"

# Inspection
uv run opennova --version
uv run opennova list-tools
uv run opennova config
```

Do not document `opennova tui`; it is not a command. Do not describe the removed line-oriented interactive CLI as a current interface.

## Verification

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/opennova
```

For focused work, run the smallest relevant pytest file first. The UTF-8 environment avoids Python startup failures when the repository path contains Chinese characters.

## Architecture

- `src/opennova/main.py`: Click entry point for TUI startup, one-shot tasks, setup, and inspection
- `src/opennova/cli/`: Textual workbench, dialogs, message blocks, tool cards, slash commands, and Windows driver support
- `src/opennova/runtime/`: Agent orchestration, ReAct loop, canonical events, state, and persistence bridge
- `src/opennova/providers/`: OpenAI, Anthropic, and DeepSeek providers plus factory
- `src/opennova/tools/`: tool base/registry and 39 built-in tools
- `src/opennova/session/`: JSONL v2 snapshots, runtime events, legacy loading, and resume metadata
- `src/opennova/memory/`: context compression, working/project memory, retrieval, and layered memory
- `src/opennova/planning/`: plan data and planner
- `src/opennova/security/`: guardrails, permission rules, command/network policy, redaction, audit, file sandbox, and process sandbox
- `src/opennova/skills/`: directory-based `SKILL.md` discovery, namespacing, activation, and bundled skills
- `src/opennova/mcp/`: stdio/SSE connectors, remote tools, and resources
- `src/opennova/plugins.py` and `src/opennova/hooks.py`: trusted project extensions
- `src/opennova/automation.py`: local scheduler and daemon support
- `src/opennova/checkpoints.py` and `src/opennova/transcript.py`: rollback metadata and exports
- `src/opennova/sdk.py`: headless `OpenNovaClient` and normalized `SDKEvent`

## Runtime and tools

`AgentRuntime` owns configuration, providers, context, state, sessions, plugins, Skills, MCP, and the tool registry. `AgentLoop` performs model/tool iterations and emits callbacks plus canonical `ToolEvent` values.

Built-in tools are grouped as follows:

- file, shell, glob/grep, and Python diagnostics/symbol navigation
- background tasks, TodoWrite, Plan mode, sub-agents, and user questions
- Skills, web, project guide, and MCP resources
- Git and isolated worktrees

New tools inherit `BaseTool`, return `ToolResult`, use typed `execute()` parameters, and are registered in `AgentRuntime._register_builtin_tools()` only when they should ship globally. Tools should accurately report read-only, destructive, permission, concurrency, interruption, and open-world behavior when defaults are insufficient.

## TUI behavior

The message log supports direct mouse selection. Copy uses `Ctrl+Shift+C` and, where delivered by the terminal, `Cmd+C`; `Ctrl+C` cancels the active run. Clipboard writing combines Textual/OSC 52 with `pbcopy`, `clip`, `wl-copy`, or `xclip` fallbacks.

`--resume` and `/resume` open `SessionPickerDialog`; `--continue` loads the newest session. Resume must restore backend context, replay visible transcript events, restore runtime/plan state, and keep writing to the original session id.

Slash commands are declared in `src/opennova/cli/commands.py`. Add command metadata there and implement the matching handler in the TUI. Long-running or modal commands must be marked `sync=False`.

## Sessions and state

Session persistence uses an atomic JSONL v2 snapshot plus appendable state events. Snapshots include messages, compression summary, replayable transcript events, plan state, runtime state, and revision data. Legacy sessions are loaded best-effort and exact duplicate messages are collapsed.

Never resume by creating a new session id. Bind the `SessionManager` and runtime state store to the selected existing id before subsequent writes.

## Configuration

Configuration layering is defaults, global config, project config, then environment expansion. Important groups:

- `providers`
- `agent.compression`
- `session.persistence`
- `security`, including permission, network, secrets, audit, and process sandbox settings
- `mcp`
- `skills`

When adding configuration, update `DEFAULT_CONFIG`, loading/validation, and user documentation together.

## Security

Permission modes are `request`, `auto`, and `full`. Full mode skips ordinary approval prompts but cannot bypass hard blocks, explicit deny rules, Plan approval, path/network restrictions, secret policy, or process sandboxing.

File operations must use the shared sandbox. Shell execution must pass through command policy and the process sandbox plan. External tool and MCP calls must preserve security context and audit metadata. Never log unredacted secrets.

## Skills, plugins, and MCP

Skills use `~/.opennova/skills/<name>/SKILL.md`, `.opennova/skills/<name>/SKILL.md`, or configured roots. Preserve namespaced resolution, invocation permissions, argument substitution, path activation, and ranking.

Project plugins are untrusted until explicitly trusted. Keep lockfile, drift, warning, and audit behavior intact when extending plugin loading. MCP currently supports stdio and SSE; WebSocket is represented in types but intentionally rejected as unsupported.

## Tests and style

- use `pytest-asyncio` for async behavior
- mock provider/network/process boundaries where practical
- add regression tests for persistence, permission, and TUI state changes
- keep line length at 100 and target Python 3.11+
- use type hints for new public functions
- preserve existing changes in a dirty worktree
- prefer small scoped edits over unrelated refactors

`docs/develop/` contains archived implementation plans. They are historical records and must not be treated as current command or product documentation.
