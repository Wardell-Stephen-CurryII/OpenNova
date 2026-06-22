"""Local project plugin manifest support."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from opennova.hooks import HookManager


@dataclass
class PluginTestReport:
    """Validation result for one local plugin."""

    name: str
    success: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PluginPolicy:
    """Local plugin policy used for non-blocking audits."""

    require_signature: bool = False
    allow_hooks: bool = True
    allow_mcp: bool = True

    @classmethod
    def strict(cls) -> PluginPolicy:
        return cls(require_signature=True, allow_hooks=False, allow_mcp=False)


@dataclass
class PluginManifest:
    """Parsed local plugin manifest."""

    name: str
    root: Path
    description: str = ""
    enabled: bool = True
    signature: str = ""
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
            signature=str(data.get("signature", "")),
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
                error_key = f"{manifest.name}.{name or 'tool'}"
                error = self._validate_tool_manifest(tool_data)
                if error:
                    self.errors[error_key] = error
                    continue
                command = str(tool_data.get("command", "")).strip()
                permission = self._tool_permission(tool_data)
                tools.append(
                    PluginCommandTool(
                        name=name,
                        description=str(tool_data.get("description", f"Plugin tool: {name}")),
                        command=command,
                        args=[str(item) for item in tool_data.get("args", [])],
                        config=config,
                        read_only=bool(tool_data.get("read_only", False)),
                        permission=permission,
                    )
                )
        return tools

    def build_lockfile(self) -> dict[str, Any]:
        """Build a local trust snapshot for discovered plugins."""
        plugins: list[dict[str, Any]] = []
        for manifest in self.plugins:
            plugins.append(
                {
                    "name": manifest.name,
                    "description": manifest.description,
                    "path": str(manifest.root.relative_to(self.project_path)),
                    "enabled": manifest.enabled,
                    "signature": manifest.signature,
                    "trusted": self.is_trusted(manifest.name),
                    "commands": [dict(command) for command in manifest.commands],
                    "tools": [
                        {
                            "name": str(tool.get("name", "")),
                            "permission": self._tool_permission(tool),
                            "read_only": bool(tool.get("read_only", False)),
                        }
                        for tool in manifest.tools
                    ],
                    "skills": [
                        str(skill.relative_to(manifest.root))
                        for skill in manifest.skills
                    ],
                    "hooks": [
                        str(hook.relative_to(manifest.root))
                        for hook in manifest.hooks
                    ],
                    "mcp_servers": [
                        str(server.get("name", ""))
                        for server in manifest.mcp_servers
                        if isinstance(server, dict)
                    ],
                }
            )
        return {"version": 1, "plugins": plugins}

    def test_plugin(self, name: str) -> PluginTestReport:
        """Validate one discovered plugin without executing its hooks or tools."""
        manifest = next((plugin for plugin in self.plugins if plugin.name == name), None)
        if manifest is None:
            return PluginTestReport(name=name, success=False, errors=[f"Plugin not found: {name}"])

        errors: list[str] = []
        for tool in manifest.tools:
            error = self._validate_tool_manifest(tool)
            if error:
                tool_name = str(tool.get("name") or "tool")
                errors.append(f"{tool_name}: {error}")
        for hook in manifest.hooks:
            if not hook.exists():
                errors.append(f"Hook path does not exist: {hook}")
        for skill in manifest.skills:
            if not skill.exists():
                errors.append(f"Skill path does not exist: {skill}")

        return PluginTestReport(name=name, success=not errors, errors=errors)

    def audit_permissions(self) -> list[dict[str, Any]]:
        """Return a local permission audit for discovered plugins."""
        audits: list[dict[str, Any]] = []
        for manifest in self.plugins:
            risks: list[str] = []
            for tool in manifest.tools:
                permission = self._tool_permission(tool)
                if permission != "read":
                    risks.append(f"tool:{tool.get('name', 'tool')}:{permission}")
            if manifest.hooks:
                risks.append(f"hooks:{len(manifest.hooks)}")
            for server in manifest.mcp_servers:
                if isinstance(server, dict):
                    risks.append(f"mcp:{server.get('name', 'server')}")
            audits.append(
                {
                    "name": manifest.name,
                    "trusted": self.is_trusted(manifest.name),
                    "signature": manifest.signature,
                    "risks": risks,
                }
            )
        return audits

    def audit_policy(self, policy: PluginPolicy) -> list[dict[str, Any]]:
        """Return policy violations for discovered plugins without blocking load."""
        reports: list[dict[str, Any]] = []
        for manifest in self.plugins:
            violations: list[str] = []
            if policy.require_signature and not manifest.signature:
                violations.append("missing-signature")
            if not policy.allow_hooks and manifest.hooks:
                violations.append("hooks-disallowed")
            if not policy.allow_mcp and manifest.mcp_servers:
                violations.append("mcp-disallowed")
            reports.append(
                {
                    "name": manifest.name,
                    "trusted": self.is_trusted(manifest.name),
                    "violations": violations,
                }
            )
        return reports

    def startup_warnings(
        self,
        lockfile: dict[str, Any] | None = None,
        policy: PluginPolicy | None = None,
    ) -> list[dict[str, str]]:
        """Return startup warning messages for drift and policy issues."""
        warnings: list[dict[str, str]] = []
        if lockfile:
            drift = self.compare_lockfile(lockfile)
            for item in drift["changed"]:
                warnings.append(
                    {
                        "type": "drift",
                        "plugin": str(item["name"]),
                        "message": "; ".join(str(change) for change in item["changes"]),
                    }
                )
            for key in ("added", "removed"):
                for item in drift[key]:
                    warnings.append(
                        {
                            "type": "drift",
                            "plugin": str(item["name"]),
                            "message": f"plugin {key[:-1]}",
                        }
                    )
        if policy:
            for report in self.audit_policy(policy):
                if report["violations"]:
                    warnings.append(
                        {
                            "type": "policy",
                            "plugin": str(report["name"]),
                            "message": ",".join(str(item) for item in report["violations"]),
                        }
                    )
        return warnings

    def compare_lockfile(self, lockfile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """Compare a lockfile snapshot with currently loaded plugin manifests."""
        current = {plugin["name"]: plugin for plugin in self.build_lockfile().get("plugins", [])}
        locked = {plugin["name"]: plugin for plugin in lockfile.get("plugins", [])}

        added = [{"name": name} for name in sorted(set(current) - set(locked))]
        removed = [{"name": name} for name in sorted(set(locked) - set(current))]
        changed: list[dict[str, Any]] = []
        for name in sorted(set(current) & set(locked)):
            changes = self._plugin_lock_changes(locked[name], current[name])
            if changes:
                changed.append({"name": name, "changes": changes})
        return {"added": added, "removed": removed, "changed": changed}

    def _plugin_lock_changes(
        self,
        locked: dict[str, Any],
        current: dict[str, Any],
    ) -> list[str]:
        changes: list[str] = []
        scalar_fields = ("description", "enabled", "signature", "trusted")
        for field_name in scalar_fields:
            if locked.get(field_name) != current.get(field_name):
                changes.append(f"{field_name} changed")

        list_fields = ("commands", "skills", "hooks", "mcp_servers")
        for field_name in list_fields:
            if locked.get(field_name, []) != current.get(field_name, []):
                changes.append(f"{field_name} changed")

        locked_tools = {tool.get("name"): tool for tool in locked.get("tools", [])}
        current_tools = {tool.get("name"): tool for tool in current.get("tools", [])}
        for tool_name in sorted(set(locked_tools) | set(current_tools)):
            if tool_name not in locked_tools:
                changes.append(f"tool added: {tool_name}")
                continue
            if tool_name not in current_tools:
                changes.append(f"tool removed: {tool_name}")
                continue
            if locked_tools[tool_name].get("permission") != current_tools[tool_name].get("permission"):
                changes.append(f"tool {tool_name} permission changed")
        return changes

    def _validate_tool_manifest(self, tool_data: dict[str, Any]) -> str | None:
        """Return an error message for invalid plugin tool declarations."""
        for field_name in ("name", "description", "command"):
            if not str(tool_data.get(field_name, "")).strip():
                return f"Plugin tool missing required field: {field_name}"
        args = tool_data.get("args", [])
        if args and not (isinstance(args, list) and all(isinstance(item, str) for item in args)):
            return "Plugin tool args must be a list of strings"
        permission = self._tool_permission(tool_data)
        if permission not in {"read", "edit", "command"}:
            return "Plugin tool permission must be one of: read, edit, command"
        return None

    def _tool_permission(self, tool_data: dict[str, Any]) -> str:
        """Normalize plugin tool permission declarations."""
        if tool_data.get("permission"):
            return str(tool_data["permission"])
        if tool_data.get("read_only"):
            return "read"
        return "command"

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
