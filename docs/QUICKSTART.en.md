# OpenNova Quickstart Guide

Get up and running with the current version of OpenNova in 5 minutes.

> This document is written from the perspective of a repository developer and defaults to `uv run opennova ...`. If you've already installed the global CLI via `uv tool install .`, you can drop `uv run` from the commands below.

## Step 1: Install

```bash
# Clone the repository
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova

# Install dependencies
uv sync

# Initialize global configuration (creates ~/.opennova/config.yaml)
uv run opennova init
```

## Step 2: Configure a model

Current default configuration:

```yaml
default_provider: deepseek
default_model: deepseek-v4-pro
```

The recommended way to supply an API key is via an environment variable:

```bash
export DEEPSEEK_API_KEY="sk-your-deepseek-key"
```

If you'd like to switch to a different provider, you can also edit `~/.opennova/config.yaml`:

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
```

## Step 3: Start using it

```bash
# Launch the Textual TUI (default interactive interface, including Windows IME compatibility handling)
uv run opennova

# Explicitly launch the Textual TUI
uv run opennova run --tui

# Execute a single task directly
uv run opennova run "Read the README.md file"
```

## Step 4: Initialize project memory

Once inside a project, you can have OpenNova generate a long-term project guide for the current repository:

```text
/init
```

This creates `OPENNOVA.md` in the project root. The model automatically reads this file to help future tasks understand the codebase, directory structure, workflows, and important notes more quickly.

If the file already exists but you want to regenerate it:

```text
/init --force
```

## Example tasks

```text
# File operations
opennova> Read src/main.py
opennova> Create a test.py file
opennova> List the directory structure

# Code generation
opennova> Write a function that computes the Fibonacci sequence

# Shell commands
opennova> Run pytest tests/

# Plan mode
opennova> /plan Refactor the authentication module

# Project initialization
opennova> /init
```

## Common commands

| Command | Description |
|------|------|
| `uv run opennova` | Launch the Textual TUI |
| `uv run opennova run "task"` | Execute a single task |
| `uv run opennova run --tui` | Explicitly launch the Textual TUI |
| `uv run opennova --permission-mode request\|auto\|full` | Choose the approval mode for this run |
| `uv run opennova --version` | Show version |
| `uv run opennova init` | Initialize global configuration |
| `/init [--force]` | Generate or regenerate `OPENNOVA.md` |
| `/permissions mode request\|auto\|full` | View or switch the current approval mode |
| `/permissions <tool> allow\|deny\|ask` | View or update a tool permission rule |
| `/plugins [trust\|untrust\|test name\|lock\|drift\|warnings\|audit [--policy strict]]` | Manage, lock, validate, warn about, and audit local project plugins |
| `/automations` | View local automation tasks |
| `/automations once <name> <run_at> <prompt>` | Create a one-shot automation task |
| `/automations interval <name> <seconds> <prompt>` | Create a recurring automation task |
| `/automations pause\|resume\|delete\|run-now <id>` | Manage automation tasks |
| `/automations daemon start\|stop\|status\|tick\|run` | Control the local automation daemon |
| `/diagnostics [path]` | Run Python diagnostics |
| `/status` | View current runtime status |
| `/todos` | View the TodoWrite task board |
| `/checkpoint list\|diff\|restore [--preview] <id>` | Manage checkpoint snapshots |
| `/checkpoint diff --session <session> <id>` | Look up a checkpoint diff from `.opennova/exports/<session>.md` |
| `/checkpoint diff --from-transcript <path> <id>` | Look up a checkpoint diff from a transcript |
| `write_file` checkpoint metadata | Automatically creates a checkpoint when overwriting an existing file |
| `edit_file` checkpoint metadata | `edit` and `multi-edit` also automatically create checkpoints |
| `/export [dir]` | Export the current transcript, including tool checkpoint/diff details |
| automation retry/archive | Local daemon retry events can be archived via a callback |
| automation backoff/archive summary | Provides retry delay and archive summary capability |
| transcript checkpoint lookup | Exported transcripts can be indexed by `checkpoint_id` |
| transcript session lookup | `/checkpoint diff --session` can resolve checkpoint diffs by session id |
| plugin startup warnings | `/plugins warnings --policy strict` can report lockfile drift and policy risks |
| diagnostics events | diagnostics, hover, definition, and references can be wrapped into a unified event payload |
| diagnostics server manager | A lightweight server lifecycle facade records pyright/ruff argv and process metadata |
| plugin startup warnings | Can generate drift and strict policy startup warnings |
| automation status archive | daemon status can include an archive summary |
| `/help` | View interactive command help |
| `/exit` | Exit the current session |

> If your project path contains Chinese characters or other non-ASCII characters, it's recommended to use:
> `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest -q`

## Getting help

- [Full Tutorial](TUTORIAL.md)
- [API Documentation](API.md)
- [Report an issue](https://github.com/Wardell-Stephen-CurryII/OpenNova/issues)
- [GitHub Discussions](https://github.com/Wardell-Stephen-CurryII/OpenNova/discussions)
