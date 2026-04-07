# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenNova is a lightweight CLI AI Coding Agent built from scratch in Python. It's a minimalist AI coding assistant that runs in your terminal with support for multiple LLM providers (OpenAI, Anthropic, DeepSeek), a plugin-based tool system with Skill support, and built-in safety guardrails.

## Development Commands

### Setup and Installation
```bash
# Install dependencies using uv (recommended package manager)
uv sync

# Install development dependencies
uv sync --dev

# Initialize configuration (creates ~/.opennova/config.yaml)
uv run opennova init
```

### Running the Application
```bash
# Interactive REPL mode (default)
uv run opennova

# Single task execution
uv run opennova run "Read the README.md file"

# Plan mode (generate plan before execution)
uv run opennova run --plan "Refactor the authentication module"

# Use specific model
uv run opennova run -m gpt-4o "Create a new Python module"

# List available tools
uv run opennova list-tools

# Show current configuration
uv run opennova config
```

### Testing
```bash
# Run all tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=opennova

# Run specific test file
uv run pytest tests/test_basic.py

# Run specific test function
uv run pytest tests/test_basic.py::test_tool_registry
```

### Code Quality
```bash
# Format code with ruff
uv run ruff format src/

# Lint code
uv run ruff check src/

# Type checking with mypy
uv run mypy src/opennova
```

## Architecture Overview

### Core Components

1. **Providers** (`src/opennova/providers/`): LLM provider implementations
   - `base.py`: Abstract provider interface
   - `openai.py`: OpenAI GPT-4, o1 support
   - `anthropic.py`: Claude 4, 3.5 support
   - `deepseek.py`: DeepSeek support
   - `factory.py`: Provider factory pattern

2. **Tools** (`src/opennova/tools/`): Tool system foundation
   - `base.py`: BaseTool, ToolRegistry, ToolResult
   - `file_tools.py`: File operations (read, write, create, delete, list)
   - `shell_tools.py`: Shell command execution with safety checks

3. **Runtime** (`src/opennova/runtime/`): Agent execution engine
   - `agent.py`: Main orchestrator (AgentRuntime)
   - `state.py`: Agent state management
   - `loop.py`: ReAct (Reason-Act-Observe) loop implementation

4. **Diff/Patch System** (`src/opennova/diff/`): Code modification
   - `engine.py`: Diff generation and application
   - `parser.py`: LLM output parsing for structured changes
   - `changeset.py`: File change tracking

5. **Memory Management** (`src/opennova/memory/`): Context handling
   - `context.py`: Context window management
   - `working.py`: Short-term working memory
   - `project.py`: Long-term project memory

6. **Planning System** (`src/opennova/planning/`): Task decomposition
   - `planner.py`: Task decomposition logic
   - `models.py`: Plan data structures

7. **Security** (`src/opennova/security/`): Safety mechanisms
   - `guardrails.py`: Dangerous command detection
   - `sandbox.py`: Path sandboxing and protected paths

8. **MCP Integration** (`src/opennova/mcp/`): Model Context Protocol
   - `types.py`: MCP data types
   - `connector.py`: MCP server connections (stdio/SSE)

9. **Skills System** (`src/opennova/skills/`): Plugin architecture
   - `base.py`: BaseSkill and loader
   - `registry.py`: Skill management
   - `examples.py`: Built-in example skills

10. **CLI Interface** (`src/opennova/cli/`)
    - `repl.py`: Interactive REPL with command history
    - `renderer.py`: Rich terminal rendering (syntax highlighting, diff preview)

### Key Design Patterns

- **ReAct Loop**: The agent follows a Reason-Act-Observe cycle for task execution
- **Provider Factory**: Abstracts LLM provider differences behind a common interface
- **Tool Registry**: Central registry for all available tools with JSON Schema definitions
- **Skill Auto-discovery**: Skills are automatically discovered from configured directories
- **Configuration Layering**: Config loads from defaults → global → project → env vars
- **Diff-based Editing**: Code changes are applied as diffs rather than overwrites

### Configuration System

Configuration is loaded in this order (later overrides earlier):
1. Default configuration (hardcoded in `config.py`)
2. Global config file (`~/.opennova/config.yaml`)
3. Project config file (`.opennova/config.yaml`)
4. Environment variables

Key configuration sections:
- `default_provider`: Which LLM provider to use (openai, anthropic, deepseek)
- `providers`: API keys and settings for each provider
- `agent`: Runtime settings (max_iterations, auto_confirm, show_thinking)
- `security`: Safety settings (sandbox_mode, command_timeout)
- `mcp`: MCP server configurations
- `skills`: Skill directories and exclusions

### Security Features

- **Dangerous Command Detection**: Blocks potentially destructive shell commands
- **Path Sandboxing**: Restricts file operations to allowed directories
- **Protected Paths**: Prevents access to system directories (`/etc`, `/usr`, etc.)
- **Confirmation Prompts**: Requires user confirmation for risky operations
- **Sensitive File Detection**: Warns when accessing `.env`, `.pem`, and other sensitive files

## Development Notes

### Adding New Tools
1. Create a new class inheriting from `BaseTool` in the appropriate module
2. Define `name` and `description` class attributes
3. Implement `execute()` method returning `ToolResult`
4. Optionally override `get_schema()` for custom parameter definitions
5. Register the tool in `AgentRuntime._register_builtin_tools()` if it should be always available

### Adding New Skills
1. Create a skill file in `~/.opennova/skills/` or configured directory
2. Inherit from `BaseSkill` and implement `execute()` method
3. Define `name`, `description`, and `metadata` attributes
4. Skills are auto-discovered at runtime

### Adding New LLM Providers
1. Create a new provider class inheriting from `BaseLLMProvider`
2. Implement required methods: `chat_completion()`, `stream_chat_completion()`
3. Add provider to `ProviderFactory.create_provider()`
4. Update default configuration in `config.py`

### Testing Philosophy
- Tests are organized by development phases (`test_basic.py`, `test_phase2.py`, `test_phase3.py`)
- Use `pytest-asyncio` for async tests
- Mock external dependencies (API calls, file system) when appropriate
- Test both success and error cases for tools

### Code Style
- Line length: 100 characters (configured in ruff/black)
- Target Python version: 3.11+
- Use type hints throughout
- Follow ruff linting rules (configured in pyproject.toml)

## Common Development Tasks

### Running a Single Test
```bash
uv run pytest tests/test_basic.py::test_tool_registry -v
```

### Debugging Configuration Issues
```bash
# Show merged configuration
uv run opennova config

# Check environment variables
echo $OPENAI_API_KEY

# Validate configuration
python -c "from opennova.config import load_config, validate_config; config = load_config(); print(validate_config(config))"
```

### Creating a New Skill
```python
# Save to ~/.opennova/skills/my_skill.py
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

### Adding a New Built-in Tool
```python
# Add to src/opennova/tools/file_tools.py or create new module
from opennova.tools.base import BaseTool, ToolResult

class MyTool(BaseTool):
    name = "my_tool"
    description = "My new tool"
    
    def execute(self, param1: str, param2: int = 10) -> ToolResult:
        try:
            # Tool logic
            return ToolResult(success=True, output="Success")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

# Register in AgentRuntime._register_builtin_tools()
self.tool_registry.register(MyTool())
```

## File Structure Reference

```
opennova/
├── src/opennova/
│   ├── providers/         # LLM provider implementations
│   ├── tools/            # Tool system
│   ├── runtime/          # Agent runtime and ReAct loop
│   ├── diff/             # Diff/Patch system
│   ├── memory/           # Memory management
│   ├── planning/         # Planning system
│   ├── security/         # Security features
│   ├── mcp/              # MCP integration
│   ├── skills/           # Skills system
│   ├── cli/              # CLI interface
│   ├── config.py         # Configuration management
│   └── main.py           # CLI entry point
├── tests/                # Test files
├── docs/                 # Documentation
├── pyproject.toml        # Project configuration
└── README.md             # Project overview
```