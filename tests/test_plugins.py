"""Tests for local plugin manifest loading."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_plugin_manager_loads_manifest_and_applies_hooks_skills_and_mcp(tmp_path: Path):
    from opennova.hooks import HookManager
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "hooks.py").write_text(
        "def pre_tool_use(event):\n"
        "    event['metadata']['plugin'] = 'demo'\n"
        "    return event\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.yaml").write_text(
        """
name: demo
description: Demo plugin
enabled: true
commands:
  - name: demo-command
skills:
  - skills
mcp_servers:
  - name: demo_mcp
    transport: stdio
    command: python
hooks:
  - hooks.py
""".strip(),
        encoding="utf-8",
    )

    config = {"skills": {"dirs": []}, "mcp": {"servers": []}}
    hooks = HookManager(project_path=tmp_path)
    manager = PluginManager(project_path=tmp_path)
    manager.trust_plugin("demo")
    loaded = manager.load_enabled_plugins(config=config, hook_manager=hooks)

    event = hooks.run_pre_tool_use({"tool_name": "read_file", "metadata": {}})

    assert [plugin.name for plugin in loaded] == ["demo"]
    assert event["metadata"]["plugin"] == "demo"
    assert str(plugin_dir / "skills") in config["skills"]["dirs"]
    assert config["mcp"]["servers"][0]["name"] == "demo_mcp"
    assert loaded[0].commands[0]["name"] == "demo-command"


def test_plugin_manager_skips_disabled_plugins(tmp_path: Path):
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "disabled"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text("name: disabled\nenabled: false\n", encoding="utf-8")

    loaded = PluginManager(project_path=tmp_path).load_enabled_plugins(config={})

    assert loaded == []


def test_plugin_manager_rejects_hook_paths_outside_plugin_dir(tmp_path: Path):
    from opennova.plugins import PluginManifest

    plugin_dir = tmp_path / ".opennova" / "plugins" / "bad"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.yaml"
    manifest.write_text("name: bad\nhooks:\n  - ../../outside.py\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside plugin directory"):
        PluginManifest.from_file(manifest, project_path=tmp_path)


def test_plugin_manager_reports_bad_manifest(tmp_path: Path):
    from opennova.plugins import PluginManager

    plugin_dir = tmp_path / ".opennova" / "plugins" / "broken"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text("name: [broken\n", encoding="utf-8")

    manager = PluginManager(project_path=tmp_path)
    loaded = manager.load_enabled_plugins(config={})

    assert loaded == []
    assert "broken" in manager.errors
