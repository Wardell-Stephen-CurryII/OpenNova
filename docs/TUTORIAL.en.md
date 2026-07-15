# OpenNova 0.4.2 User Tutorial

This tutorial covers the current OpenNova user experience. Textual is the only interactive interface; the legacy interactive CLI and `opennova tui` subcommand have been removed.

## Installation and configuration

```bash
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova
uv sync
uv run opennova init
```

Configuration is merged from defaults, global configuration, project configuration, and environment variables in that order. Files live at `~/.opennova/config.yaml` and `.opennova/config.yaml`.

```bash
export DEEPSEEK_API_KEY="sk-your-key"
uv run opennova
```

Native providers are `openai`, `anthropic`, and `deepseek`. One-shot tasks can override them with `--provider` and `--model`.

## TUI workbench

The main screen contains the message log, prompt input, and Context, Tasks, and Activity panels. Context shows token usage, compression, active files, and decisions; Tasks combines plans and todos; Activity keeps full tool details. Tool calls from one turn are folded into a single chat summary.

| Key | Action |
|---|---|
| `Enter` | Submit the prompt |
| `Shift+Enter` | Insert a newline |
| `Ctrl+C` | Cancel the active run |
| `Ctrl+Shift+C` | Copy selected message text |
| `Cmd+C` | Copy on macOS terminals that forward the binding |
| `Tab` / `Shift+Tab` | Move focus |
| `Alt+1` / `Alt+2` / `Alt+3` | Switch Context / Tasks / Activity |
| `Alt+T` | Show or hide the workbench |

Copying no longer opens an overlay. Drag over the message text and use the copy binding; OpenNova combines OSC 52 with a native clipboard fallback.

## Conversations and tools

Describe the outcome and constraints directly:

```text
Read src/opennova/runtime/agent.py and summarize the runtime without editing files.
```

For changes, include verification expectations:

```text
Fix the user module edge case and run its pytest and ruff checks.
```

Built-in tools cover file operations, code search, Python diagnostics, shell, Git, background tasks, TodoWrite, planning, sub-agents, Skills, web access, project guides, MCP resources, and worktrees. Run `/tools` for the authoritative list in the current process.

## Plan and Act

Normal prompts and `/act` execute directly:

```text
/act Fix the configuration loading error and add a regression test.
```

Use Plan mode for larger changes:

```text
/plan Refactor session persistence while preserving legacy compatibility.
```

Plans are persisted and displayed in the Plan panel. Execution begins only after approval, and step state is mirrored to Todos.

## Session resume

Sessions are stored under `~/.opennova/sessions/`.

```bash
uv run opennova --resume    # Open the picker
uv run opennova --continue  # Continue the newest session
```

Inside the TUI, `/resume` opens the same picker and `/resume <id>` restores a specific session. Rows are ordered by modification time and titled from the first user message. Resume rebuilds the visible transcript and backend context, then continues the original session file instead of creating a duplicate.

## Project guides and Skills

Generate `OPENNOVA.md` with `/init` or rebuild it with `/init --force`. OpenNova automatically loads it for later work.

Custom Skills use either project or user scope:

```text
.opennova/skills/my_skill/SKILL.md
~/.opennova/skills/my_skill/SKILL.md
```

```markdown
---
name: my_skill
description: Review a requested module.
when_to_use: Use when the user asks for a focused module review.
allowed-tools: read_file, grep_code
arguments: [target]
---
Review $ARGUMENTS and report correctness risks.
```

Use `/skills`, `/skill my_skill src/`, and `/reload-skills` to manage them.

## Permissions and sandboxing

```bash
uv run opennova --permission-mode request
uv run opennova --permission-mode auto
uv run opennova --permission-mode full
```

```text
/permissions
/permissions mode auto
/permissions execute_command ask
```

`request` asks for every call. `auto` runs routine development operations automatically and asks
only for high-risk actions such as deletion, force operations, private-network access, secret
writes, or untrusted MCP tools. `full` skips ordinary approval prompts. Hard blocks, deny rules,
Plan approval, path/network restrictions, and process sandboxing still apply in `full` mode.

## MCP, plugins, and hooks

MCP supports stdio and SSE transports:

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./src"]
```

Project plugins may add tools and slash commands, but they load only when both the workspace path
and content digest match an external trust record. `/plugins` provides trust, lock, drift, warning,
and audit operations. Project Python hooks do not execute by default; use `/hooks trust` for the
current digest, re-trust after changes, and `/hooks untrust` to revoke access.

## Checkpoints and exports

Overwriting or editing existing files creates checkpoint metadata.

```text
/checkpoint list
/checkpoint diff <id>
/checkpoint restore --preview <id>
/checkpoint restore <id>
/export
```

Markdown exports include tool results and checkpoint/diff details and are written to `.opennova/exports/` by default.

## Automations and diagnostics

```text
/automations
/automations once nightly 2026-07-13T01:00:00 Check for failing tests
/automations interval health 3600 Run a health check
/automations daemon status
/diagnostics src/
```

Automations use the local scheduler. Python diagnostics are complemented by symbol, definition, and reference tools.

## One-shot non-interactive tasks

The TUI is the only interactive interface, while one-shot commands remain available for scripts:

```bash
uv run opennova run "Read README.md"
uv run opennova run --plan "Add tests for configuration"
uv run opennova run --provider anthropic -m claude-sonnet-4 "Review src/"
```

`--resume` and `--continue` are TUI startup options and cannot be combined with a direct task.

## Troubleshooting

- Missing API key: verify the environment variable or YAML value.
- Windows IME issues: launch the current Textual TUI directly in a Unicode-capable terminal.
- Clipboard failure: select text first and press `Ctrl+Shift+C`; Linux native fallback requires `wl-copy` or `xclip`.
- Empty resume picker: check that the current project's session directory contains saved sessions.
- MCP failure: inspect command, args, transport, and server logs.
- Encoding errors in non-ASCII paths: set `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1`.

See the [API reference](API.en.md) for internals and [AGENTS.md](../AGENTS.md) for architecture and development commands.
