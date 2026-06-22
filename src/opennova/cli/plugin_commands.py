"""Shared plugin slash-command handling."""

from __future__ import annotations

import json

from opennova.plugins import PluginManager, PluginPolicy
from opennova.tools.base import ToolResult


def handle_plugin_command(manager: PluginManager, args: str) -> ToolResult:
    """Handle `/plugins` subcommands."""
    tokens = (args or "list").split()
    subcommand = tokens[0] if tokens else "list"

    try:
        if subcommand == "list":
            plugins = manager.plugins
            output = "\n".join(
                f"{plugin.name} trusted={manager.is_trusted(plugin.name)} enabled={plugin.enabled}"
                for plugin in plugins
            ) or "No local plugins discovered."
            return ToolResult(success=True, output=output, metadata={"plugins": plugins})

        if subcommand in {"trust", "untrust"} and len(tokens) == 2:
            name = tokens[1]
            if subcommand == "trust":
                manager.trust_plugin(name)
                output = f"Trusted plugin: {name}"
            else:
                manager.untrust_plugin(name)
                output = f"Untrusted plugin: {name}"
            return ToolResult(success=True, output=output, metadata={"plugin": name})

        if subcommand == "test" and len(tokens) == 2:
            name = tokens[1]
            report = manager.test_plugin(name)
            if report.success:
                return ToolResult(
                    success=True,
                    output=f"Plugin {name} passed validation",
                    metadata={"report": report},
                )
            return ToolResult(
                success=False,
                output="",
                error="\n".join(report.errors),
                metadata={"report": report},
            )

        if subcommand == "lock":
            lockfile = manager.build_lockfile()
            lock_path = manager.plugins_dir / "lock.json"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(json.dumps(lockfile, indent=2, ensure_ascii=False), encoding="utf-8")
            return ToolResult(
                success=True,
                output=f"Plugin lockfile written: {lock_path}",
                metadata={"lockfile": lockfile, "path": str(lock_path)},
            )

        if subcommand == "drift":
            lock_path = manager.plugins_dir / "lock.json"
            if not lock_path.exists():
                return ToolResult(success=False, output="", error=f"Plugin lockfile not found: {lock_path}")
            lockfile = json.loads(lock_path.read_text(encoding="utf-8"))
            drift = manager.compare_lockfile(lockfile)
            lines: list[str] = []
            for key in ("added", "removed"):
                lines.extend(f"{key}: {item['name']}" for item in drift[key])
            for item in drift["changed"]:
                lines.append(f"changed: {item['name']} ({'; '.join(item['changes'])})")
            return ToolResult(
                success=True,
                output="\n".join(lines) or "No plugin drift detected.",
                metadata={"drift": drift},
            )

        if subcommand == "warnings":
            lock_path = manager.plugins_dir / "lock.json"
            lockfile = None
            if lock_path.exists():
                lockfile = json.loads(lock_path.read_text(encoding="utf-8"))
            policy = PluginPolicy.strict() if tokens[1:] == ["--policy", "strict"] else None
            warnings = manager.startup_warnings(lockfile=lockfile, policy=policy)
            lines = [
                f"{item['type']}: {item['plugin']} {item['message']}".rstrip()
                for item in warnings
            ]
            return ToolResult(
                success=True,
                output="\n".join(lines) or "No plugin startup warnings.",
                metadata={
                    "warnings": warnings,
                    "policy": "strict" if policy else "default",
                    "lockfile": str(lock_path) if lockfile else "",
                },
            )

        if subcommand == "audit":
            if tokens[1:] == ["--policy", "strict"]:
                reports = manager.audit_policy(PluginPolicy.strict())
                lines = [
                    f"{item['name']} violations={','.join(item['violations']) or 'none'}"
                    for item in reports
                ]
                return ToolResult(
                    success=True,
                    output="\n".join(lines) or "No local plugins discovered.",
                    metadata={"policy": "strict", "audit": reports},
                )
            audit = manager.audit_permissions()
            lines = [
                f"{item['name']} trusted={item['trusted']} signature={item['signature'] or 'none'} "
                f"risks={','.join(item['risks']) or 'none'}"
                for item in audit
            ]
            return ToolResult(
                success=True,
                output="\n".join(lines) or "No local plugins discovered.",
                metadata={"audit": audit},
            )
    except Exception as exc:
        return ToolResult(success=False, output="", error=str(exc))

    return ToolResult(
        success=False,
        output="",
        error=(
            "Usage: /plugins [list|trust <name>|untrust <name>|test <name>|lock|"
            "drift|warnings [--policy strict]|audit]"
        ),
    )
