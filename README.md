# OpenNova

OpenNova v0.2.3 is a lightweight CLI AI coding agent built from scratch in Python.

**English** | **[简体中文](README.zh-CN.md)**

**[Quickstart (Chinese)](docs/QUICKSTART.md)** | **[Tutorial (Chinese)](docs/TUTORIAL.md)** | **[API Reference (Chinese)](docs/API.md)**

## Overview

OpenNova runs in your terminal and combines a small core with practical coding-agent workflows:
- **Multi-provider runtime** for OpenAI, Anthropic, and DeepSeek
- **Dual interface**: prompt_toolkit REPL and Textual TUI with split-pane chat
- **Session management**: save, resume, and list sessions (JSONL persistence)
- **Context compression**: LLM-driven summarization for long conversations
- **Plan + act workflows** for decomposing larger tasks before execution
- **Tool + skill extensibility** for local tools and user-defined plugins (17 built-in tools)
- **MCP integration** for external tool servers
- **Built-in safety guardrails** for risky commands and protected paths

## What’s in v0.2.3

The 0.2.3 release adds session management, context compression, and Textual TUI:
- **Session management**: `/resume <id>`, `/sessions` — conversations persist to JSONL
- **Context compression**: LLM summarizes old messages when context exceeds 55% token utilization, keeping long conversations within budget
- **Textual TUI**: Split-pane chat interface with copy overlay, history navigation, and real-time streaming
- **17 built-in tools**: file ops, shell execution, git, task tracking, plan mode, sub-agents, skills, web
- ReAct runtime with streaming responses and tool execution
- Plan mode with approval flow inside the REPL and TUI
- Diff/patch editing pipeline
- Context, working memory, and project memory components
- MCP stdio and SSE transport support
- Skill auto-discovery and bundled example skills
- Interactive user-question prompts in REPL and TUI runs
- Real HTTP-backed `web_fetch` behavior

Note: `web_search` is present as a tool surface, but in this runtime it reports that search is not configured instead of fabricating results.

## Installation

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

### Local development setup

```bash
# Clone the repository
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova

# Install dependencies into the project environment
uv sync

# Initialize configuration
uv run opennova init
```

If you want an installed CLI instead of the local development flow, you can also run `uv tool install .` and then use `opennova` directly. The examples below use `uv run opennova ...` so they always match the checked-out source tree.

## Configuration

Edit `~/.opennova/config.yaml` or set environment variables:

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
    default_model: deepseek-v4-pro

agent:
  max_iterations: 200
  auto_confirm: false
  show_thinking: true
  compression:
    enabled: true
    threshold: 0.55
    keep_last_pairs: 6
    max_tool_result_tokens: 8000

mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./src"]

skills:
  enabled: true
  dirs: []
  exclude: []
```

Or use environment variables:

```bash
export OPENAI_API_KEY=your_key_here
export ANTHROPIC_API_KEY=your_key_here
export DEEPSEEK_API_KEY=your_key_here
```

## Usage

### Interactive modes

```bash
# REPL mode (prompt_toolkit, default)
uv run opennova

# Textual TUI mode (split-pane chat interface)
uv run opennova tui
```

### Session management

```bash
# Resume a previous session
uv run opennova resume <session_id>

# List all sessions
uv run opennova sessions
```

### Single task mode

```bash
# Execute a task directly
uv run opennova run "Read the README.md file"

# Run in plan mode
uv run opennova run --plan "Refactor the authentication module"

# Use a specific model
uv run opennova run -m gpt-4o "Create a new Python module"
```

### REPL commands

Inside the interactive REPL:

| Command | Description |
|---------|-------------|
| `/plan <task>` | Generate a plan, show it, and ask whether to execute it now |
| `/act <task>` | Execute directly (default mode) |
| `/tools` | List available tools |
| `/skills` | List loaded skills |
| `/skill <name> [args]` | Invoke a loaded skill directly |
| `/reload-skills` | Reload skills from disk |
| `/model` | Show current model info |
| `/config` | Show current configuration |
| `/history [n]` | Show recent conversation history |
| `/resume <id>` | Resume a previous session |
| `/sessions` | List saved sessions |
| `/clear` | Clear current conversation state |
| `/help` | Show help message |
| `/exit` | Exit |

## Built-in tools

OpenNova ships with a broader tool surface than the original README listed.

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents with optional line range |
| `write_file` | Write content to a file |
| `create_file` | Create a new file |
| `delete_file` | Delete a file with confirmation |
| `list_directory` | List directory contents |
| `execute_command` | Execute shell commands through guardrails |
| `git_commit` | Create a git commit with staged changes |
| `git_status` | Show working tree status |
| `git_diff` | Show changes between commits or working tree |
| `git_log` | Show commit history |
| `git_branch` | List or manage branches |
| `task_create` | Create a new task in the task list |
| `task_list` | List all tracked tasks |
| `task_get` | Get task details by ID |
| `task_update` | Update task status or properties |
| `task_stop` | Stop a running background task |
| `task_output` | Get output from a completed task |
| `enter_plan_mode` | Enter plan mode for architectural design |
| `exit_plan_mode` | Exit plan mode after plan approval |
| `agent` | Delegate work to a sub-agent |
| `send_message` | Send a message to a running sub-agent |
| `skill` | Invoke a loaded skill by name |
| `ask_user_question` | Ask the user to choose from 2-4 options during a run |
| `web_fetch` | Fetch a real HTTP/HTTPS page and return extracted content |
| `web_search` | Search interface placeholder; reports unconfigured when no backend is available |

## Built-in skills

OpenNova includes several example skills:

| Skill | Description |
|-------|-------------|
| `code_review` | Review code for quality and best practices |
| `generate_docs` | Generate documentation/docstrings |
| `git_helper` | Git command assistance |
| `analyze_project` | Analyze project structure |

## Creating custom skills

Custom skills now use the Claude Code-style markdown package format. Create a directory such as `~/.opennova/skills/my_skill/` and put a `SKILL.md` file inside it:

```markdown
---
name: my_skill
description: Summarize a target file or feature area.
when_to_use: Use when the user wants a reusable project-specific analysis prompt.
allowed-tools: read_file, list_directory
arguments: [target]
argument-hint: <file-or-area>
---
Analyze the requested target carefully.

Target: $ARGUMENTS

Summarize:
- what it does
- key risks
- likely extension points
```

Skills are markdown prompts loaded from these directory layouts:
- `~/.opennova/skills/<skill-name>/SKILL.md`
- `.opennova/skills/<skill-name>/SKILL.md`
- configured skill directories with the same `<skill-name>/SKILL.md` structure

## MCP integration

OpenNova supports Model Context Protocol (MCP) servers for extended capabilities:

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
```

Supported transports:
- **stdio**: launch a subprocess and communicate over stdin/stdout
- **sse**: connect to an HTTP SSE endpoint

## Context compression

OpenNova automatically compresses conversation context when token usage exceeds 55% of the model's context window:

- **LLM-driven summarization**: Old messages are summarized into a concise paragraph using the active LLM provider
- **Safe cut points**: Compression never splits incomplete assistant+tool pairs
- **Session persistence**: Compression markers are saved to JSONL, enabling compact session resume
- **Tool result truncation**: Large tool outputs (>8000 tokens) are truncated (head 20% + tail 80%)
- **Configurable**: Adjust threshold, keep-last-pairs count, and truncation limits in config

When a session resumes, only messages after the last compression boundary are loaded — older context is replaced by the summary.

## Session management

Conversations are automatically persisted to `~/.opennova/sessions/` as JSONL files:

```bash
# Inside REPL or TUI
/resume <session_id>   # Resume a previous session
/sessions              # List all saved sessions
```

Each session file records every message, tool call, and compression boundary. When resuming, compression markers allow the agent to restore context compactly.

## Architecture

```text
opennova/
├── providers/         # LLM provider implementations
├── tools/             # Built-in tools and tool registry (17 tools)
├── runtime/           # Agent runtime, loop, and state
├── cli/               # REPL (prompt_toolkit) and TUI (Textual)
├── diff/              # Diff/patch system
├── memory/            # Context management, compression, working/project memory
├── planning/          # Plan data structures and planner
├── security/          # Guardrails and sandboxing
├── session/           # Session persistence (JSONL)
├── mcp/               # MCP transports and connectors
├── skills/            # Skill system and examples
└── main.py            # CLI entry point
```

## Security features

OpenNova includes several safety mechanisms:
- **Dangerous command detection** for destructive shell commands
- **Path sandboxing** for file access limits
- **Protected paths** for system directories such as `/etc` and `/usr`
- **Confirmation prompts** for risky operations
- **Sensitive file detection** for files like `.env` and `.pem`

## Development

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=opennova

# Type check
uv run mypy src/opennova

# Format code
uv run ruff format src/

# Lint
uv run ruff check src/
```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for the full text.

## Author

Xingwang Lin ([@Wardell-Stephen-CurryII](https://github.com/Wardell-Stephen-CurryII))
