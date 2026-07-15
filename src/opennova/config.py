"""
Configuration loader and management.

Handles loading configuration from:
1. Default configuration
2. Global config file (~/.opennova/config.yaml)
3. Project config file (.opennova/config.yaml)
4. Environment variables
"""

import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from opennova.security.secrets import redact_sensitive_data

DEFAULT_CONFIG: dict[str, Any] = {
    "default_provider": "deepseek",
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
            "default_model": "deepseek-v4-pro",
        },
    },
    "agent": {
        "max_iterations": 20,
        "auto_confirm": False,
        "show_thinking": True,
        "compression": {
            "enabled": True,
            "threshold": 0.55,
            "keep_last_pairs": 6,
            "max_tool_result_tokens": 8000,
        },
    },
    "session": {
        "persistence": {
            "debounce_ms": 250,
            "snapshot_event_threshold": 100,
            "snapshot_size_threshold": 1048576,
            "fsync_critical": True,
        }
    },
    "security": {
        "sandbox_mode": True,
        "command_timeout": 30,
        "allow_network": True,
        "auto_confirm_safe": True,
        "allowed_paths": [],
        "blocked_commands": [],
        "strict_shell_parsing": False,
        "permission_mode": "auto",
        "always_allow_tools": [],
        "always_deny_tools": [],
        "always_ask_tools": [],
        "permission_rules": [],
        "network": {
            "allowed_domains": [],
            "blocked_domains": [],
            "allow_localhost": False,
            "mutating_methods_require_confirmation": True,
        },
        "secrets": {
            "enabled": True,
            "redact_tool_outputs": True,
            "warn_on_write": True,
            "block_on_write": False,
            "max_scan_chars": 200000,
        },
        "process_sandbox": {
            "enabled": True,
            "backend": "auto",
            "enforce": False,
            "tmp_dir": None,
            "extra_read_roots": [],
            "extra_writable_roots": [],
        },
        "audit": {
            "enabled": True,
            "path": ".opennova/audit/security.jsonl",
            "max_arg_chars": 500,
        },
        "read_only": False,
        "max_file_size": 104857600,
    },
    "mcp": {
        "enabled": True,
        "servers": [],
    },
    "skills": {
        "enabled": True,
        "dirs": [],
        "exclude": [],
    },
}


@dataclass
class Config:
    """Configuration container."""

    data: dict[str, Any] = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG))
    config_path: str | None = None

    def __post_init__(self) -> None:
        self.data = deepcopy(self.data)

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (supports dot notation)."""
        keys = key.split(".")
        value: Any = self.data

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
            child = data.get(k)
            if not isinstance(child, dict):
                child = {}
                data[k] = child
            data = child

        data[keys[-1]] = value

    def setdefault(self, key: str, default: Any = None) -> Any:
        """Provide the mutable-mapping operation used by extension loaders."""
        return self.data.setdefault(key, deepcopy(default))

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self.data

    def to_dict(self) -> dict[str, Any]:
        """Return an isolated mutable snapshot for runtime ownership."""
        return deepcopy(self.data)

    def redacted_data(self) -> dict[str, Any]:
        """Return a safe representation for terminal and diagnostic output."""
        redacted = redact_sensitive_data(self.data)
        return redacted if isinstance(redacted, dict) else {}

    def save(self, path: str | None = None) -> None:
        """Save configuration to file."""
        save_path = path or self.config_path
        if not save_path:
            raise ValueError("No config path specified")

        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        with open(save_path, "w") as f:
            yaml.dump(self.data, f, default_flow_style=False, sort_keys=False)

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """Get MCP server configurations."""
        mcp_config = self.get("mcp", {})
        if not isinstance(mcp_config, dict):
            return []
        if not mcp_config.get("enabled", True):
            return []
        servers = mcp_config.get("servers", [])
        if not isinstance(servers, list):
            return []
        return [deepcopy(server) for server in servers if isinstance(server, dict)]

    def get_skill_dirs(self) -> list[str]:
        """Get skill directories to load from."""
        skills_config = self.get("skills", {})
        if not isinstance(skills_config, dict):
            return []
        if not skills_config.get("enabled", True):
            return []
        directories = skills_config.get("dirs", [])
        if not isinstance(directories, list):
            return []
        return [str(directory) for directory in directories]

    def get_excluded_skills(self) -> list[str]:
        """Get list of excluded skill names."""
        skills_config = self.get("skills", {})
        if not isinstance(skills_config, dict):
            return []
        excluded = skills_config.get("exclude", [])
        if not isinstance(excluded, list):
            return []
        return [str(name) for name in excluded]


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
    result = deepcopy(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)

    return result


def _config_mapping(value: Any, source: str) -> dict[str, Any]:
    """Validate that one YAML document can participate in config merging."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Configuration in {source} must be a YAML mapping")
    return value


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

    config_data = deepcopy(DEFAULT_CONFIG)
    loaded_path = None

    global_config = Path.home() / ".opennova" / "config.yaml"
    if global_config.exists():
        with open(global_config) as f:
            global_data = _config_mapping(yaml.safe_load(f), str(global_config))
            config_data = _deep_merge(config_data, global_data)
            loaded_path = str(global_config)

    if config_path:
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file) as f:
                file_data = _config_mapping(yaml.safe_load(f), str(config_file))
                config_data = _deep_merge(config_data, file_data)
                loaded_path = str(config_file)
    else:
        project_config = Path(".opennova/config.yaml")
        if project_config.exists():
            with open(project_config) as f:
                project_data = _config_mapping(yaml.safe_load(f), str(project_config))
                config_data = _deep_merge(config_data, project_data)
                loaded_path = str(project_config)

    config_data = _config_mapping(_expand_env_vars(config_data), "expanded configuration")

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

    permission_mode = config.get("security.permission_mode", "auto")
    valid_permission_modes = {
        "request",
        "auto",
        "full",
        "default",
        "ask",
        "allowEdits",
        "readOnly",
        "bypass",
    }
    if permission_mode not in valid_permission_modes:
        errors.append(
            "security.permission_mode must be one of: request, auto, full, "
            "default, ask, allowEdits, readOnly, bypass"
        )

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
