"""
Configuration loader and management.

Handles loading configuration from:
1. Default configuration
2. Global config file (~/.opennova/config.yaml)
3. Project config file (.opennova/config.yaml)
4. Environment variables
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "default_provider": "openai",
    "providers": {
        "openai": {
            "api_key": "${OPENAI_API_KEY}",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4o",
        },
        "anthropic": {
            "api_key": "${ANTHROPIC_API_KEY}",
            "default_model": "claude-sonnet-4",
        },
        "deepseek": {
            "api_key": "${DEEPSEEK_API_KEY}",
            "base_url": "https://api.deepseek.com/v1",
            "default_model": "deepseek-chat",
        },
    },
    "agent": {
        "max_iterations": 20,
        "auto_confirm": False,
        "show_thinking": True,
    },
    "security": {
        "sandbox_mode": True,
        "command_timeout": 30,
    },
}


@dataclass
class Config:
    """Configuration container."""

    data: dict[str, Any] = field(default_factory=lambda: DEFAULT_CONFIG.copy())
    config_path: str | None = None

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (supports dot notation)."""
        keys = key.split(".")
        value = self.data

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default

            if value is None:
                return default

        return value

    def set(self, key: str, value: Any) -> None:
        """Set configuration value (supports dot notation)."""
        keys = key.split(".")
        data = self.data

        for k in keys[:-1]:
            if k not in data:
                data[k] = {}
            data = data[k]

        data[keys[-1]] = value

    def save(self, path: str | None = None) -> None:
        """Save configuration to file."""
        save_path = path or self.config_path
        if not save_path:
            raise ValueError("No config path specified")

        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        with open(save_path, "w") as f:
            yaml.dump(self.data, f, default_flow_style=False, sort_keys=False)


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand environment variables in configuration values."""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.environ.get(env_var, "")
        return value
    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries."""
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def find_config_file() -> Path | None:
    """Find the appropriate configuration file."""
    project_config = Path(".opennova/config.yaml")
    if project_config.exists():
        return project_config

    global_config = Path.home() / ".opennova" / "config.yaml"
    if global_config.exists():
        return global_config

    return None


def load_config(
    config_path: str | None = None,
    load_env: bool = True,
) -> Config:
    """
    Load configuration from file.

    Priority (later overrides earlier):
    1. Default configuration
    2. Global config (~/.opennova/config.yaml)
    3. Project config (.opennova/config.yaml)
    4. Environment variables

    Args:
        config_path: Optional explicit config file path
        load_env: Whether to load from .env file

    Returns:
        Config object with merged configuration
    """
    if load_env:
        from dotenv import load_dotenv

        load_dotenv()
        env_file = Path(".env")
        if env_file.exists():
            load_dotenv(env_file)

    config_data = DEFAULT_CONFIG.copy()
    loaded_path = None

    global_config = Path.home() / ".opennova" / "config.yaml"
    if global_config.exists():
        with open(global_config) as f:
            global_data = yaml.safe_load(f) or {}
            config_data = _deep_merge(config_data, global_data)
            loaded_path = str(global_config)

    if config_path:
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file) as f:
                file_data = yaml.safe_load(f) or {}
                config_data = _deep_merge(config_data, file_data)
                loaded_path = str(config_file)
    else:
        project_config = Path(".opennova/config.yaml")
        if project_config.exists():
            with open(project_config) as f:
                project_data = yaml.safe_load(f) or {}
                config_data = _deep_merge(config_data, project_data)
                loaded_path = str(project_config)

    config_data = _expand_env_vars(config_data)

    return Config(data=config_data, config_path=loaded_path)


def get_default_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".opennova" / "config.yaml"


def create_default_config() -> Path:
    """Create default configuration file if it doesn't exist."""
    config_path = get_default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if not config_path.exists():
        with open(config_path, "w") as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)

    return config_path


def validate_config(config: Config) -> list[str]:
    """
    Validate configuration and return list of issues.

    Args:
        config: Configuration to validate

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    default_provider = config.get("default_provider")
    if not default_provider:
        errors.append("No default_provider specified")
        return errors

    providers = config.get("providers", {})
    if default_provider not in providers:
        errors.append(f"Default provider '{default_provider}' not in providers")
        return errors

    provider_config = providers.get(default_provider, {})
    api_key = provider_config.get("api_key", "")

    if not api_key:
        env_var_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }
        env_var = env_var_map.get(default_provider)
        if env_var and not os.environ.get(env_var):
            errors.append(f"API key not configured for provider '{default_provider}'")

    return errors
