# OpenNova

OpenNova v0.4.1 is a terminal AI coding agent built in Python around a Textual TUI.

**English** | **[简体中文](README.zh-CN.md)**

**[Quickstart](docs/QUICKSTART.en.md)** | **[Tutorial](docs/TUTORIAL.en.md)** | **[API Reference](docs/API.en.md)**

## Overview

OpenNova combines a focused agent runtime with a full-screen terminal workbench:

- OpenAI, Anthropic, and DeepSeek providers
- Streaming Textual TUI with chat and Context, Tasks, and Activity panels
- Persistent sessions with a resume picker and complete transcript replay
- Plan/Act workflows, TodoWrite, sub-agents, and worktrees
- 39 built-in tools plus Skills, trusted project plugins, hooks, and MCP
- Context compression and layered project memory
- Three permission modes, parameter rules, secret redaction, audit logs, and sandboxes
- A headless Python SDK for scripts and services

The legacy interactive command-line interface and standalone `opennova tui` command are not part of the current product. Run `opennova` with no subcommand to open the Textual TUI. Command options remain available for setup, automation, and one-shot tasks.

## What is new in v0.4.1

Version 0.4.1 aligns the documentation and package metadata with the current application:

- documents the Textual TUI as the only interactive interface
- removes stale `opennova tui`, 17-tool, and copy-overlay references
- documents in-place text selection and system clipboard shortcuts
- documents the session picker, full transcript replay, and same-session resume behavior
- refreshes the command, slash-command, security, SDK, and architecture references

## Installation

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova
uv sync
uv run opennova init
```

For a globally available command:

```bash
uv tool install .
opennova
```

## Configuration

Configuration is merged in this order: defaults, `~/.opennova/config.yaml`, project `.opennova/config.yaml`, then environment variables.

```yaml
default_provider: deepseek
default_model: deepseek-v4-pro

providers:
  openai:
    api_key: ${OPENAI_API_KEY}
    default_model: gpt-4o
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    default_model: claude-sonnet-4
  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com/v1
    default_model: deepseek-v4-pro

agent:
  max_iterations: 20
  compression:
    enabled: true
    threshold: 0.55
    keep_last_pairs: 6
    max_tool_result_tokens: 8000

security:
  permission_mode: auto  # request | auto | full
  sandbox_mode: true
  allow_network: true
  strict_shell_parsing: false
  read_only: false

mcp:
  enabled: true
  servers: []

skills:
  enabled: true
  dirs: []
  exclude: []
```

API keys may also be supplied through `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `DEEPSEEK_API_KEY`.

## Usage

```bash
# Open the Textual TUI
uv run opennova

# Open the session picker
uv run opennova --resume

# Continue the newest session
uv run opennova --continue

# Choose the approval mode for this run
uv run opennova --permission-mode request

# Execute a one-shot task without opening the TUI
uv run opennova run "Read README.md"

# Generate a plan for a one-shot task
uv run opennova run --plan "Refactor the authentication module"

# Select a provider or model for a one-shot task
uv run opennova run --provider deepseek -m deepseek-v4-pro "Review src/"
```

Other setup and inspection commands are `opennova init`, `opennova list-tools`, `opennova config`, and `opennova --version`.

## TUI controls

| Control | Action |
|---|---|
| `Enter` | Submit the prompt |
| `Shift+Enter` | Insert a newline |
| `Ctrl+C` | Cancel the active run |
| `Ctrl+Shift+C` | Copy the selected message text |
| `Cmd+C` | Copy selected text on macOS terminals that deliver the binding |
| mouse drag | Select text directly in the message log |
| `Tab` / `Shift+Tab` | Move focus |

Clipboard copying first uses Textual/OSC 52 and then the native platform command (`pbcopy`, `clip`, `wl-copy`, or `xclip`) when available.

## Slash commands

The main commands available inside the TUI are:

| Command | Purpose |
|---|---|
| `/act <task>` | Execute directly |
| `/plan <task>` | Generate a plan and request approval |
| `/tools`, `/skills`, `/skill <name> [args]` | Inspect tools or invoke a Skill |
| `/init [--force]` | Generate or rebuild `OPENNOVA.md` |
| `/resume [id]`, `/sessions` | Pick or inspect persisted sessions |
| `/permissions ...` | Inspect or update permission mode and rules |
| `/plugins ...`, `/hooks` | Manage trusted project extensions |
| `/automations ...` | Manage local scheduled tasks and the daemon |
| `/diagnostics [path]` | Run Python diagnostics |
| `/todos`, `/status` | Inspect runtime state |
| `/checkpoint ...` | List, preview, diff, or restore checkpoints |
| `/export [dir]` | Export the current transcript to Markdown |
| `/history [n]`, `/clear`, `/help`, `/exit` | Manage the current TUI session |

Run `/help` for the registry generated by the installed version.

## Built-in capabilities

OpenNova currently registers 39 built-in tools across these groups:

- files: read, write, create, edit, multi-edit, delete, and directory listing
- search and diagnostics: glob, grep, Python syntax, symbols, definitions, and references
- shell and Git: guarded command execution, status, diff, log, branch, and commit
- tasks: background tasks, TodoWrite, planning, sub-agents, and user questions
- integrations: Skills, web fetch/search surface, project guide, MCP resources, and worktrees

`web_search` intentionally returns an unconfigured result unless a search backend is provided; it does not fabricate search results.

## Sessions and memory

Sessions are persisted under `~/.opennova/sessions/`. `--resume` and `/resume` open a newest-first picker whose titles come from the first user message. Resuming restores both backend context and the visible TUI transcript, then continues writing to the original session instead of creating a duplicate.

Context compression starts at 55% utilization by default. Older complete message pairs are summarized while recent messages, tool-call boundaries, and compression markers remain recoverable.

## Extensions

Skills use a directory-based `SKILL.md` format:

```text
~/.opennova/skills/<name>/SKILL.md
.opennova/skills/<name>/SKILL.md
```

MCP supports stdio and SSE transports. Project plugins can add trusted tools and slash commands;
plugin lock, drift, warning, and audit operations are exposed through `/plugins`. Plugin trust is
stored outside the repository and is bound to the workspace path and plugin content digest.
Project Python hooks are disabled until the current hook digest is approved with `/hooks trust`.

## Security model

- `request`: asks before every otherwise allowed tool call
- `auto`: automatically runs routine development calls, including elevated-risk commands, and
  asks only for high-risk actions such as deletion, force operations, private-network access,
  secret writes, or untrusted MCP tools
- `full`: skips approval prompts but does not bypass hard blocks

Hard blocks, explicit deny rules, plan approval, path/network policy, secret handling, and the optional OS process sandbox remain active in every mode.

Configuration display, canonical tool events, tool observations, and persisted transcripts redact
detected secrets by default. The process sandbox limits reads to system/runtime roots and explicit
project paths; when an optional backend is unavailable and enforcement is disabled, command output
shows a visible fallback warning. Use `security.process_sandbox.enforce: true` to fail closed.

## Python SDK

```python
import asyncio

from opennova import OpenNovaClient
from opennova.config import load_config

async def main() -> None:
    async with OpenNovaClient(load_config()) as client:
        session_id = client.create_session()
        result = await client.submit_message(session_id, "Summarize this project")
        print(result)

asyncio.run(main())
```

## Development

```bash
uv sync --dev
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest
uv run ruff check src/ tests/
uv run mypy src/opennova
```

See [AGENTS.md](AGENTS.md) for the current architecture and contributor workflow. Historical implementation plans under `docs/develop/` are retained as archived design records rather than user documentation.

## License

OpenNova is released under the [MIT License](LICENSE).
