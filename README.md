# OpenNova

A lightweight CLI AI Coding Agent built from scratch in Python.

## Overview

OpenNova is a minimalist AI coding assistant that runs in your terminal. It's designed to be:
- **Lightweight**: No heavy framework dependencies (LangChain, CrewAI, etc.)
- **Flexible**: Support for multiple LLM providers (OpenAI, Anthropic, DeepSeek)
- **Extensible**: Plugin-based tool system with Skill support
- **Safe**: Built-in guardrails and confirmation for dangerous operations

## Features

### Phase 1 (Current)
- ✅ ReAct (Reason-Act-Observe) reasoning loop
- ✅ Multi-provider support (OpenAI, Anthropic, DeepSeek)
- ✅ Streaming output for real-time responses
- ✅ Built-in tools: file operations, shell commands
- ✅ Interactive REPL with command history
- ✅ Configuration management (YAML + environment variables)

### Phase 2 (Planned)
- 🔄 Diff/Patch code modification system
- 🔄 Plan mode with task decomposition
- 🔄 Memory and context management
- 🔄 Security guardrails
- 🔄 Rich terminal rendering

### Phase 3 (Planned)
- 🔄 MCP (Model Context Protocol) integration
- 🔄 Skill plugin system
- 🔄 Cross-session memory
- 🔄 Project-aware context

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

- `/plan <task>` - Generate a plan before executing
- `/act <task>` - Execute directly (default)
- `/tools` - List available tools
- `/model` - Show current model info
- `/help` - Show help
- `/exit` - Exit the REPL

## Built-in Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents with optional line range |
| `write_file` | Write content to a file |
| `create_file` | Create a new file |
| `delete_file` | Delete a file (requires confirmation) |
| `list_directory` | List directory contents |
| `execute_command` | Execute shell commands |

## Architecture

```
opennova/
├── providers/     # LLM provider implementations
│   ├── base.py    # Abstract provider interface
│   ├── openai.py
│   ├── anthropic.py
│   └── deepseek.py
├── tools/         # Tool system
│   ├── base.py    # BaseTool and ToolRegistry
│   ├── file_tools.py
│   └── shell_tools.py
├── runtime/       # Agent runtime
│   ├── state.py   # Agent state management
│   ├── loop.py    # ReAct loop
│   └── agent.py   # Main orchestrator
├── cli/           # CLI interface
│   └── repl.py    # Interactive REPL
└── main.py        # Entry point
```

## Development

```bash
# Run tests
uv run pytest

# Type check
uv run mypy src/opennova

# Format code
uv run ruff format src/
```

## License

MIT

## Author

Xingwang Lin ([@Wardell-Stephen-CurryII](https://github.com/Wardell-Stephen-CurryII))
