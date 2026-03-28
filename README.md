# OpenNova

A lightweight CLI AI Coding Agent built from scratch in Python.

**[快速开始](docs/QUICKSTART.md)** | **[完整教程](docs/TUTORIAL.md)** | **[API 文档](docs/API.md)**

## Overview

OpenNova is a minimalist AI coding assistant that runs in your terminal. It's designed to be:
- **Lightweight**: No heavy framework dependencies (LangChain, CrewAI, etc.)
- **Flexible**: Support for multiple LLM providers (OpenAI, Anthropic, DeepSeek)
- **Extensible**: Plugin-based tool system with Skill support
- **Safe**: Built-in guardrails and confirmation for dangerous operations

## Features

### Phase 1 ✅
- ✅ ReAct (Reason-Act-Observe) reasoning loop
- ✅ Multi-provider support (OpenAI, Anthropic, DeepSeek)
- ✅ Streaming output for real-time responses
- ✅ Built-in tools: file operations, shell commands
- ✅ Interactive REPL with command history
- ✅ Configuration management (YAML + environment variables)

### Phase 2 ✅
- ✅ Diff/Patch code modification system
- ✅ Plan mode with task decomposition
- ✅ Memory and context management (token counting, working/project memory)
- ✅ Security guardrails (dangerous command detection, path sandboxing)
- ✅ Rich terminal rendering (syntax highlighting, diff preview, progress bars)

### Phase 3 ✅
- ✅ MCP (Model Context Protocol) integration
- ✅ Skill plugin system with auto-discovery
- ✅ Built-in example skills (code review, docs generator, git helper)
- ✅ Extensible architecture for custom tools

## Installation

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
# Clone the repository
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova

# Install dependencies
uv sync

# Initialize configuration
uv run opennova init
```

### Configuration

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

# MCP server configuration
mcp:
  enabled: true
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./src"]
    - name: github
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_PERSONAL_ACCESS_TOKEN: ${GITHUB_TOKEN}

# Skills configuration
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

### Interactive Mode (REPL)

```bash
uv run opennova
```

### Single Task Mode

```bash
# Execute a task directly
uv run opennova run "Read the README.md file"

# Run in plan mode
uv run opennova run --plan "Refactor the authentication module"

# Use a specific model
uv run opennova run -m gpt-4o "Create a new Python module"
```

### REPL Commands

Inside the interactive REPL:

| Command | Description |
|---------|-------------|
| `/plan <task>` | Generate a plan before executing |
| `/act <task>` | Execute directly (default mode) |
| `/tools` | List available tools |
| `/skills` | List loaded skills |
| `/model` | Show current model info |
| `/config` | Show current configuration |
| `/history` | Show conversation history |
| `/clear` | Clear conversation |
| `/help` | Show help message |
| `/exit` | Exit the REPL |

## Built-in Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents with optional line range |
| `write_file` | Write content to a file |
| `create_file` | Create a new file |
| `delete_file` | Delete a file (requires confirmation) |
| `list_directory` | List directory contents |
| `execute_command` | Execute shell commands |

## Built-in Skills

OpenNova includes several example skills:

| Skill | Description |
|-------|-------------|
| `code_review` | Review code for quality and best practices |
| `generate_docs` | Generate documentation/docstrings |
| `git_helper` | Git command assistance |
| `analyze_project` | Analyze project structure |

## Creating Custom Skills

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
        # Your skill logic here
        return ToolResult(success=True, output="Done!")
```

Skills are auto-discovered from:
- `~/.opennova/skills/`
- `.opennova/skills/`
- Configured directories

## MCP Integration

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
- **stdio**: Launch subprocess, communicate via stdin/stdout
- **sse**: Connect via Server-Sent Events

## Architecture

```
opennova/
├── providers/         # LLM provider implementations
│   ├── base.py        # Abstract provider interface
│   ├── openai.py      # OpenAI GPT-4, o1 support
│   ├── anthropic.py   # Claude 4, 3.5 support
│   ├── deepseek.py    # DeepSeek support
│   └── factory.py     # Provider factory
├── tools/             # Tool system
│   ├── base.py        # BaseTool and ToolRegistry
│   ├── file_tools.py  # File operations
│   └── shell_tools.py # Shell commands
├── runtime/           # Agent runtime
│   ├── state.py       # Agent state management
│   ├── loop.py        # ReAct loop
│   └── agent.py       # Main orchestrator
├── cli/               # CLI interface
│   ├── repl.py        # Interactive REPL
│   └── renderer.py    # Rich terminal rendering
├── diff/              # Diff/Patch system
│   ├── engine.py      # Diff generation and application
│   ├── parser.py      # LLM output parsing
│   └── changeset.py   # File change tracking
├── memory/            # Memory management
│   ├── context.py     # Context window management
│   ├── working.py     # Short-term working memory
│   └── project.py     # Long-term project memory
├── planning/          # Planning system
│   ├── planner.py     # Task decomposition
│   └── models.py      # Plan data structures
├── security/          # Security
│   ├── guardrails.py  # Safety checks
│   └── sandbox.py     # Path sandboxing
├── mcp/               # MCP integration
│   ├── types.py       # MCP data types
│   └── connector.py   # MCP server connections
├── skills/            # Skills system
│   ├── base.py        # BaseSkill and loader
│   ├── registry.py    # Skill management
│   └── examples.py    # Example skills
└── main.py            # Entry point
```

## Security Features

OpenNova includes several safety mechanisms:

- **Dangerous Command Detection**: Blocks potentially destructive shell commands
- **Path Sandboxing**: Restricts file operations to allowed directories
- **Protected Paths**: Prevents access to system directories (`/etc`, `/usr`, etc.)
- **Confirmation Prompts**: Requires user confirmation for risky operations
- **Sensitive File Detection**: Warns when accessing `.env`, `.pem`, and other sensitive files

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

MIT

## Author

Xingwang Lin ([@Wardell-Stephen-CurryII](https://github.com/Wardell-Stephen-CurryII))
