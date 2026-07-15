# OpenNova 0.4.1 Quickstart

OpenNova's interactive interface is the Textual TUI. The legacy interactive CLI and standalone `opennova tui` command have been removed; run `opennova` directly to open the workbench.

## 1. Install

```bash
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova
uv sync
uv run opennova init
```

`init` creates `~/.opennova/config.yaml`. You can also run `uv tool install .` and omit `uv run` from the remaining examples.

## 2. Configure a model

Environment variables are the recommended way to provide keys:

```bash
export DEEPSEEK_API_KEY="sk-your-key"
# Or OPENAI_API_KEY / ANTHROPIC_API_KEY
```

Change the default provider and model in the global or project configuration:

```yaml
default_provider: deepseek
default_model: deepseek-v4-pro

providers:
  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com/v1
    default_model: deepseek-v4-pro
```

Project-level `.opennova/config.yaml` values override global configuration.

## 3. Launch

```bash
# Open the Textual TUI
uv run opennova

# Choose a saved session
uv run opennova --resume

# Continue the newest session
uv run opennova --continue

# Run one non-interactive task
uv run opennova run "Read README.md"
```

`--resume` shows sessions in newest-first order. Restoring a session rebuilds the visible message log and backend context, then continues the original session.

## 4. First conversation

Enter this in the TUI:

```text
Analyze this project and tell me which files I should read first.
```

Useful workflows:

```text
/init
/plan Add tests for the user module
/tools
/todos
/status
```

`/init` generates `OPENNOVA.md` in the project root. Future tasks automatically load it as project guidance.

## 5. Copy message text

Drag over text directly in the message log, then press `Ctrl+Shift+C`. `Cmd+C` also works in macOS terminals that forward the binding. `Ctrl+C` remains reserved for cancelling the active Agent run.

## Common entry points

| Command | Description |
|---|---|
| `uv run opennova` | Open the TUI |
| `uv run opennova --resume` | Open the session picker |
| `uv run opennova --continue` | Continue the newest session |
| `uv run opennova --permission-mode request\|auto\|full` | Select the approval mode |
| `uv run opennova run "task"` | Run one non-interactive task |
| `uv run opennova init` | Create global configuration |
| `uv run opennova list-tools` | List registered tools |
| `uv run opennova config` | Show merged configuration |
| `uv run opennova --version` | Show the version |

## Common TUI commands

| Command | Description |
|---|---|
| `/act <task>` | Execute directly |
| `/plan <task>` | Generate a plan and request approval |
| `/resume [id]` | Pick or restore a session |
| `/permissions ...` | Inspect or update approval rules |
| `/plugins ...` | Manage project plugins |
| `/hooks [trust|untrust]` | Inspect or manage project hook trust |
| `/automations ...` | Manage local automations |
| `/diagnostics [path]` | Run Python diagnostics |
| `/checkpoint ...` | Inspect or restore file checkpoints |
| `/export [dir]` | Export a Markdown transcript |
| `/help` | Show the complete command list |
| `/exit` | Exit |

For project paths containing non-ASCII characters, run tests with:

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest
```

Continue with the [full tutorial](TUTORIAL.en.md), [API reference](API.en.md), or [issue tracker](https://github.com/Wardell-Stephen-CurryII/OpenNova/issues).
