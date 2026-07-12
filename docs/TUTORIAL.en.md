# OpenNova Installation and Usage Guide

This tutorial will help you get up to speed with the current version of the OpenNova CLI AI Coding Agent.

---

## Table of Contents

1. [Environment Setup](#1-environment-setup)
2. [Installation](#2-installation)
3. [Configuring API Keys](#3-configuring-api-keys)
4. [Basic Usage](#4-basic-usage)
5. [Advanced Features](#5-advanced-features)
6. [FAQ](#6-faq)

---

## 1. Environment Setup

### 1.1 Check your Python version

OpenNova requires Python 3.11 or higher:

```bash
python3 --version
```

If your version is below 3.11, install a newer one first:

**macOS (using Homebrew):**
```bash
brew install python@3.11
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-dev
```

**Windows:**
Download and install from [python.org](https://www.python.org/downloads/).

### 1.2 Install the uv package manager

`uv` is a fast Python package manager:

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify the install:
```bash
uv --version
```

---

## 2. Installation

### 2.1 Clone the repository

```bash
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova
```

### 2.2 Install dependencies

```bash
uv sync
```

This automatically installs the project's dependencies, including:
- `openai`
- `anthropic`
- `rich`
- `textual`
- `click`
- `httpx`
- and other runtime dependencies

### 2.3 Initialize configuration

```bash
uv run opennova init
```

If you'd like to install it as a global command, you can additionally run:

```bash
uv tool install .
```

This lets you use `opennova` directly afterward. This document still defaults to `uv run opennova ...` to ensure commands match the current source tree.

---

## 3. Configuring API Keys

There are two ways to configure an API key. The current default provider is `deepseek`, with a default model of `deepseek-v4-pro`.

### Option 1: Environment variables (recommended)

```bash
# Add to ~/.zshrc or ~/.bashrc
export DEEPSEEK_API_KEY="sk-your-deepseek-api-key"

# If you plan to switch providers, you can configure these too
export OPENAI_API_KEY="sk-your-openai-api-key"
export ANTHROPIC_API_KEY="sk-ant-your-anthropic-api-key"
```

Then reload your shell config:
```bash
source ~/.zshrc  # or source ~/.bashrc
```

### Option 2: Edit the config file

```bash
nano ~/.opennova/config.yaml
```

Update it to:
```yaml
default_provider: deepseek
default_model: deepseek-v4-pro

providers:
  openai:
    api_key: "sk-your-actual-openai-api-key"
    default_model: gpt-4o

  anthropic:
    api_key: "sk-ant-your-actual-anthropic-api-key"
    default_model: claude-sonnet-4

  deepseek:
    api_key: "sk-your-actual-deepseek-api-key"
    default_model: deepseek-v4-pro
```

### Getting API keys

| Provider | Where to get it |
|--------|----------|
| OpenAI | https://platform.openai.com/api-keys |
| Anthropic | https://console.anthropic.com/ |
| DeepSeek | https://platform.deepseek.com/ |

---

## 4. Basic Usage

### 4.1 Verify the installation

```bash
uv run opennova --version
```

Output:
```text
OpenNova v0.4.0
```

### 4.2 Interactive mode

Start an interactive session:

```bash
uv run opennova
```

The default interactive mode launches the Textual TUI. On Windows, a dedicated TUI input driver is used to support Unicode characters submitted via Chinese IMEs.

If you want to explicitly enter the Textual TUI, run:

```bash
uv run opennova run --tui
```

If you're on Windows and still run into issues where the Chinese IME can't submit text, you can temporarily turn on TUI input diagnostic logging:

```powershell
$env:OPENNOVA_TUI_INPUT_DEBUG="$env:TEMP\opennova-tui-input.jsonl"
uv run opennova
```

Reproduce the issue, then exit, and use this JSONL file for troubleshooting. The log only records the character, code point, virtual key, and control-key state of keyboard input events.

You'll see a welcome screen similar to:
```text
╭────────────────────────────────────────╮
│ OpenNova - AI Coding Agent            │
│ Type /help for commands, Ctrl+D to exit│
╰────────────────────────────────────────╯

opennova>
```

### 4.3 Your first task

In the interactive interface, type:
```text
opennova> Read the README.md file
```

OpenNova will:
1. Think through how to complete the task
2. Call tools as needed
3. Output the result, or ask a follow-up question

### 4.4 Common task examples

**Read a file:**
```text
opennova> Read the first 50 lines of src/main.py
```

**Create a file:**
```text
opennova> Create a hello.py file that prints Hello World
```

**Execute a command:**
```text
opennova> Run python hello.py
```

**List a directory:**
```text
opennova> List the current directory structure
```

### 4.5 Single-task mode

Execute a single task directly without entering the interactive interface:

```bash
# Execute a task directly
uv run opennova run "Read README.md"

# Use plan mode
uv run opennova run --plan "Refactor the authentication module"

# Specify a model
uv run opennova run -m deepseek-v4-pro "Analyze the project structure"

# Use DeepSeek
uv run opennova run --provider deepseek "Write a test case"
```

### 4.6 Built-in TUI commands

| Command | Description | Example |
|------|------|------|
| `/help` | Show help | `/help` |
| `/plan <task>` | Generate a plan first, then confirm whether to execute it | `/plan Refactor the code` |
| `/act <task>` | Execute directly | `/act Read a file` |
| `/tools` | List available tools | `/tools` |
| `/skills` | List loaded skills | `/skills` |
| `/skill <name> [args]` | Invoke a skill directly | `/skill analyze_project src` |
| `/reload-skills` | Reload skills from disk | `/reload-skills` |
| `/model` | Show current model | `/model` |
| `/init [--force]` | Generate or regenerate `OPENNOVA.md` | `/init --force` |
| `/config` | Show configuration | `/config` |
| `/permissions mode request\|auto\|full` | View or switch the current approval mode | `/permissions mode auto` |
| `/permissions <tool> allow\|deny\|ask` | View or update a tool permission rule | `/permissions execute_command ask` |
| `/plugins [trust\|untrust\|test name\|lock\|drift\|warnings\|audit [--policy strict]]` | Manage, lock, validate, warn about, and audit local project plugins | `/plugins warnings --policy strict` |
| `/hooks` | View loaded hooks | `/hooks` |
| `/automations` | View local automation tasks | `/automations` |
| `/automations once <name> <run_at> <prompt>` | Create a one-shot automation task | `/automations once docs 200 Review docs` |
| `/automations interval <name> <seconds> <prompt>` | Create a recurring automation task | `/automations interval docs 3600 Review docs` |
| `/automations pause\|resume\|delete\|run-now <id>` | Manage automation tasks | `/automations pause abc123` |
| `/automations daemon start\|stop\|status\|tick\|run` | Control the local automation daemon | `/automations daemon run` |
| `/diagnostics [path]` | Run Python diagnostics | `/diagnostics src` |
| `/status` | View runtime status | `/status` |
| `/todos` | View the TodoWrite task board | `/todos` |
| `/checkpoint` | View checkpoint/rollback status | `/checkpoint` |
| `/checkpoint list\|diff\|restore [--preview] <id>` | List, preview, or restore a checkpoint | `/checkpoint restore --preview abc123` |
| `/checkpoint diff --session <session> <id>` | Look up a checkpoint diff from a session transcript | `/checkpoint diff --session session-1 abc123` |
| `/checkpoint diff --from-transcript <path> <id>` | Look up a checkpoint diff from a transcript | `/checkpoint diff --from-transcript session.md abc123` |
| `write_file` checkpoint metadata | Automatically creates a checkpoint when overwriting an existing file | Check `checkpoint_id` in the tool result |
| `edit_file` checkpoint metadata | `edit` and `multi-edit` also automatically create checkpoints | `/checkpoint restore abc123` |
| `/export [dir]` | Export the current transcript, including checkpoint/diff details | `/export .opennova/exports` |
| automation retry/archive | Local daemon retry events can be archived via a callback | `run_with_retry(...)` |
| automation backoff/archive summary | View retry delay and archive summaries | `archive.summary()` |
| transcript checkpoint lookup | Look up a diff from a transcript by `checkpoint_id` | `extract_checkpoint_index(path)` |
| transcript session lookup | Look up a checkpoint diff from an export directory by session id | `/checkpoint diff --session session-1 abc123` |
| plugin startup warnings | Reports plugin lockfile drift and policy risks | `/plugins warnings --policy strict` |
| diagnostics events | Wraps multiple types of Python analysis results into a unified event payload | `event_for_definition(path, symbol)` |
| diagnostics server manager | Manages the lifecycle of a lightweight analysis server | `PythonAnalysisServerManager().status()` |
| plugin startup warnings | Generates drift and strict policy startup warnings | `startup_warnings(...)` |
| automation status archive | daemon status includes an archive summary | `daemon_status(daemon, archive)` |
| `/history [n]` | Show recent session history | `/history 5` |
| `/resume [id]` | Resume a past session | `/resume abc123` |
| `/sessions` | List past sessions | `/sessions` |
| `/clear` | Clear the current session state | `/clear` |
| `/exit` | Exit OpenNova | `/exit` |

### 4.7 Initialize project memory

If you'd like OpenNova to understand the current project more quickly, run this in the project root:

```text
opennova> /init
```

This generates `OPENNOVA.md`. The model automatically reads this file in future tasks as long-term project memory.

If the file already exists but you'd like to regenerate it:

```text
opennova> /init --force
```

---

## 5. Advanced Features

### 5.1 Plan Mode

For complex tasks, use plan mode to have the AI draw up a plan first:

```bash
uv run opennova run --plan "Add unit tests for the user management module"
```

Or in the TUI:
```text
opennova> /plan Add unit tests for the user management module
```

After the plan is generated, the TUI displays it and asks whether to execute it right away — giving complex tasks a manual confirmation step before anything actually happens.

### 5.2 Switching between models

```bash
# Use OpenAI GPT-4o
uv run opennova run --provider openai "task"

# Use Anthropic Claude
uv run opennova run --provider anthropic "task"

# Use DeepSeek
uv run opennova run --provider deepseek "task"
```

Or set a default in your configuration:
```yaml
default_provider: deepseek
default_model: deepseek-v4-pro
```

### 5.3 Using Skills

Loaded skills can be invoked directly:

```text
opennova> Use the code_review skill to review main.py
```

**Built-in example skills:**
- `code_review` — code review
- `generate_docs` — generate documentation
- `git_helper` — Git assistance
- `analyze_project` — project analysis

### 5.4 Creating a custom Skill

Create the directory `~/.opennova/skills/my_skill/`, then add a `SKILL.md` file inside it:

```markdown
---
name: my_skill
description: Analyze and summarize a specified target.
when_to_use: Use when you want to turn a frequently-used prompt into a reusable skill.
allowed-tools: read_file, list_directory
arguments: [target]
argument-hint: <file-or-area>
---
Analyze the target content.

Target: $ARGUMENTS

Output:
- Feature overview
- Key dependencies
- Risk points
```

After restarting OpenNova, new skills are automatically loaded following the `~/.opennova/skills/<skill-name>/SKILL.md` layout.

### 5.5 MCP server integration

Configure MCP servers to extend functionality.

Edit `~/.opennova/config.yaml`:

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/your/project"]

    - name: github
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
```

OpenNova currently supports:
- `stdio`: launches a subprocess and communicates over stdin/stdout
- `sse`: connects to an HTTP SSE endpoint

### 5.6 About the web tools

- `web_fetch` makes a real request to an HTTP/HTTPS page and returns the extracted content.
- `web_search` is currently kept as a unified tool interface; if no search backend is configured, it explicitly reports that it's unconfigured rather than fabricating results.

### 5.7 Security configuration

```yaml
security:
  permission_mode: auto
  sandbox_mode: true
  command_timeout: 30
  allow_network: true
  auto_confirm_safe: true
  allowed_paths:
    - "./src"
    - "./tests"
  blocked_commands: []
  strict_shell_parsing: false
  read_only: false
  max_file_size: 104857600
```

Notes on the security policy:
- `request` asks for approval on every allowed tool call; `auto` only asks for high-risk calls; `full` skips tool approval prompts entirely.
- `full` does not disable hard blocks, explicit deny rules, plan approval, network/path restrictions, or the OS process sandbox.
- File tools all go through a sandbox check that enforces the working directory, protected paths, and read-only mode.
- `execute_command` defaults to running plain commands with `shell=False`.
- When a command contains shell features like pipes or redirection, it falls back to a compatibility path and goes through the Guardrails confirmation flow.
- When `allow_network: false`, HTTP tools and common networking commands are blocked.
- When `strict_shell_parsing: true`, commands containing shell features are rejected outright, with no fallback.

---

## 6. FAQ

### Q1: It says the API key isn't configured

**Problem:**
```text
Configuration errors:
  • API key not configured for provider 'deepseek'
```

**Fix:**
```bash
# Check the environment variable
echo $DEEPSEEK_API_KEY

# Or edit the config file
nano ~/.opennova/config.yaml
```

### Q2: Wrong Python version

**Problem:**
```text
Requires-Python >=3.11
```

**Fix:**
```bash
uv venv --python 3.11
source .venv/bin/activate
uv sync
```

### Q3: A tool call fails

**Problem:**
```text
Error: Permission denied
```

**Fix:**
Check the file permissions:
```bash
chmod +x your_script.sh
```

### Q4: Garbled streaming output

**Problem:** Output displays incorrectly.

**Fix:**
Make sure your terminal supports UTF-8:
```bash
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
export PYTHONUTF8=1
```

### Q5: How do I update?

```bash
cd OpenNova
git pull
uv sync
```

### Q6: How do I view logs?

Checking current configuration and history through the app is more reliable than depending on internal file paths directly — if you need to debug further, first confirm whether the project has the relevant logging output enabled.

---

## Appendix: Keyboard shortcuts

| Shortcut | Function |
|--------|------|
| `Tab` | Autocomplete suggestions |
| `↑` / `↓` | Browse command history |
| `Ctrl+C` | Clear current input |
| `Ctrl+D` | Exit OpenNova |
| `Enter` | Execute command |

---

## Next steps

1. Run a few file-reading, command-execution, and plan-mode tasks on a real project
2. Create your own Skills to extend functionality
3. Configure MCP servers to connect more tools
4. Report issues via [GitHub Issues](https://github.com/Wardell-Stephen-CurryII/OpenNova/issues)
