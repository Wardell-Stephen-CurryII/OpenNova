"""Local project plugin manifest support."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from opennova.hooks import HookManager


@dataclass
class PluginManifest:
    """Parsed local plugin manifest."""

    name: str
    root: Path
    description: str = ""
    enabled: bool = True
    commands: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    skills: list[Path] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[Path] = field(default_factory=list)

    @classmethod
    def from_file(cls, manifest_path: str | Path, project_path: str | Path = ".") -> PluginManifest:
        manifest_file = Path(manifest_path).resolve()
        project_root = Path(project_path).resolve()
        plugin_root = manifest_file.parent.resolve()
        plugins_root = project_root / ".opennova" / "plugins"

        plugin_root.relative_to(plugins_root.resolve())

        data = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
        name = str(data.get("name") or plugin_root.name)

        skills = [
            cls._resolve_inside_plugin(plugin_root, item, "skill path")
            for item in data.get("skills", [])
        ]
        hooks = [
            cls._resolve_inside_plugin(plugin_root, item, "hook path")
            for item in data.get("hooks", [])
        ]

        return cls(
            name=name,
            root=plugin_root,
            description=str(data.get("description", "")),
            enabled=bool(data.get("enabled", True)),
            commands=list(data.get("commands", []) or []),
            tools=list(data.get("tools", []) or []),
            skills=skills,
            mcp_servers=list(data.get("mcp_servers", []) or []),
            hooks=hooks,
        )

    @staticmethod
    def _resolve_inside_plugin(plugin_root: Path, value: str, label: str) -> Path:
        path = (plugin_root / value).resolve()
        try:
            path.relative_to(plugin_root)
        except ValueError as exc:
            raise ValueError(f"{label} is outside plugin directory: {value}") from exc
        return path


class PluginManager:
    """Discover and apply project-local plugins."""

    def __init__(self, project_path: str | Path = "."):
        self.project_path = Path(project_path).resolve()
        self.plugins_dir = self.project_path / ".opennova" / "plugins"
        self.trust_path = self.plugins_dir / "trusted.json"
        self.plugins: list[PluginManifest] = []
        self.errors: dict[str, str] = {}
        self.commands: list[dict[str, Any]] = []
        self.trusted_plugins: set[str] = self._load_trusted_plugins()

    def trust_plugin(self, name: str) -> None:
        """Persist trust for a local plugin so active contributions can load."""
        self.trusted_plugins.add(name)
        self._save_trusted_plugins()

    def untrust_plugin(self, name: str) -> None:
        """Remove persisted trust for a local plugin."""
        self.trusted_plugins.discard(name)
        self._save_trusted_plugins()

    def is_trusted(self, name: str) -> bool:
        """Return whether a plugin may apply active contributions."""
        return name in self.trusted_plugins

    def discover_manifests(self) -> list[Path]:
        """Find plugin.yaml files under .opennova/plugins/*/."""
        if not self.plugins_dir.exists():
            return []
        return sorted(self.plugins_dir.glob("*/plugin.yaml"))

    def load_enabled_plugins(
        self,
        config: dict[str, Any],
        hook_manager: HookManager | None = None,
    ) -> list[PluginManifest]:
        """Load enabled plugins and merge their declarative contributions."""
        self.plugins = []
        self.errors = {}
        self.commands = []
        self.trusted_plugins = self._load_trusted_plugins()

        for manifest_path in self.discover_manifests():
            plugin_name = manifest_path.parent.name
            try:
                manifest = PluginManifest.from_file(manifest_path, project_path=self.project_path)
                if not manifest.enabled:
                    continue
                if self.is_trusted(manifest.name):
                    self._apply_manifest(manifest, config=config, hook_manager=hook_manager)
                self.plugins.append(manifest)
            except Exception as exc:
                self.errors[plugin_name] = str(exc)

        return self.plugins

    def build_tools(self, config: dict[str, Any] | None = None) -> list[Any]:
        """Build trusted plugin-declared tools."""
        from opennova.tools.plugin_tools import PluginCommandTool

        tools: list[Any] = []
        for manifest in self.plugins:
            if not self.is_trusted(manifest.name):
                continue
            for tool_data in manifest.tools:
                name = str(tool_data.get("name", "")).strip()
                command = str(tool_data.get("command", "")).strip()
                if not name or not command:
                    continue
                tools.append(
                    PluginCommandTool(
                        name=name,
                        description=str(tool_data.get("description", f"Plugin tool: {name}")),
                        command=command,
                        args=[str(item) for item in tool_data.get("args", [])],
                        config=config,
                        read_only=bool(tool_data.get("read_only", False)),
                    )
                )
        return tools

    def _apply_manifest(
        self,
        manifest: PluginManifest,
        config: dict[str, Any],
        hook_manager: HookManager | None,
    ) -> None:
        skills_config = config.setdefault("skills", {})
        skill_dirs = skills_config.setdefault("dirs", [])
        for skill_dir in manifest.skills:
            skill_path = str(skill_dir)
            if skill_path not in skill_dirs:
                skill_dirs.append(skill_path)

        mcp_config = config.setdefault("mcp", {})
        mcp_servers = mcp_config.setdefault("servers", [])
        existing_names = {server.get("name") for server in mcp_servers if isinstance(server, dict)}
        for server in manifest.mcp_servers:
            if server.get("name") not in existing_names:
                mcp_servers.append(server)

        if hook_manager:
            for hook_path in manifest.hooks:
                hook_manager.load_hook_file(
                    hook_path,
                    module_prefix=f"opennova_plugin_hook_{manifest.name}",
                )

        for command in manifest.commands:
            command_entry = dict(command)
            command_entry.setdefault("plugin", manifest.name)
            self.commands.append(command_entry)

    def _load_trusted_plugins(self) -> set[str]:
        if not self.trust_path.exists():
            return set()
        payload = json.loads(self.trust_path.read_text(encoding="utf-8"))
        return {str(name) for name in payload.get("trusted", [])}

    def _save_trusted_plugins(self) -> None:
        self.trust_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"trusted": sorted(self.trusted_plugins)}
        self.trust_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
