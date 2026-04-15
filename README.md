# OpenNova

OpenNova v0.2.0 is a lightweight CLI AI coding agent built from scratch in Python.

**English** | **[简体中文](README.zh-CN.md)**

**[Quickstart (Chinese)](docs/QUICKSTART.md)** | **[Tutorial (Chinese)](docs/TUTORIAL.md)** | **[API Reference (Chinese)](docs/API.md)**

## Overview

OpenNova runs in your terminal and combines a small core with practical coding-agent workflows:
- **Multi-provider runtime** for OpenAI, Anthropic, and DeepSeek
- **Interactive REPL** with slash commands, history, and streamed output
- **Plan + act workflows** for decomposing larger tasks before execution
- **Tool + skill extensibility** for local tools and user-defined plugins
- **MCP integration** for external tool servers
- **Built-in safety guardrails** for risky commands and protected paths

## What’s in v0.2.0

The 0.2.0 release reflects the now-complete core surface:
- ReAct runtime with streaming responses and tool execution
- Plan mode with approval flow inside the REPL
- Diff/patch editing pipeline
- Context, working memory, and project memory components
- MCP stdio and SSE transport support
- Skill auto-discovery and bundled example skills
- Interactive user-question prompts in REPL runs
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
default_provider: openai
default_model: gpt-4o

providers:
  openai:
    api_key: ${OPENAI_API_KEY}
    default_model: gpt-4o

  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    default_model: claude-sonnet-4

  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
    default_model: deepseek-chat

agent:
  max_iterations: 20
  auto_confirm: false
  show_thinking: true

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

### Interactive mode

```bash
uv run opennova
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
| `/reload-skills` | Reload skills from disk |
| `/model` | Show current model info |
| `/config` | Show current configuration |
| `/history [n]` | Show recent conversation history |
| `/clear` | Clear current conversation state |
| `/help` | Show help message |
| `/exit` | Exit the REPL |

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

Create a skill file in `~/.opennova/skills/my_skill.py`:

```python
from opennova.skills.base import BaseSkill, SkillMetadata
from opennova.tools.base import ToolResult


class MySkill(BaseSkill):
    """My custom skill."""

    name = "my_skill"
    description = "Does something useful"

    metadata = SkillMetadata(
        name="my_skill",
        version="1.0.0",
        description="A custom skill",
        author="Your Name",
    )

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, output="Done!")
```

Skills are auto-discovered from:
- `~/.opennova/skills/`
- `.opennova/skills/`
- configured directories

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

## Architecture

```text
opennova/
├── providers/         # LLM provider implementations
├── tools/             # Built-in tools and tool registry
├── runtime/           # Agent runtime, loop, and state
├── cli/               # REPL and terminal rendering
├── diff/              # Diff/patch system
├── memory/            # Context and memory management
├── planning/          # Plan data structures and planner
├── security/          # Guardrails and sandboxing
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
