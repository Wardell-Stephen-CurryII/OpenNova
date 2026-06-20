"""Local hook loading and execution for OpenNova."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import Any

from opennova.tools.base import ToolResult

HookCallback = Callable[[dict[str, Any]], dict[str, Any] | ToolResult | None]


class HookManager:
    """Manage local lifecycle hooks."""

    SUPPORTED_EVENTS = {
        "session_start",
        "pre_tool_use",
        "post_tool_use",
        "pre_compact",
        "post_compact",
    }

    def __init__(self, project_path: str | Path = "."):
        self.project_path = Path(project_path).resolve()
        self.hooks_dir = self.project_path / ".opennova" / "hooks"
        self._callbacks: dict[str, list[HookCallback]] = {
            event: [] for event in self.SUPPORTED_EVENTS
        }

    def register(self, event: str, callback: HookCallback) -> None:
        """Register a callback for a supported hook event."""
        if event not in self.SUPPORTED_EVENTS:
            raise ValueError(f"Unsupported hook event: {event}")
        self._callbacks[event].append(callback)

    def load_project_hooks(self) -> int:
        """Load hook functions from .opennova/hooks/*.py."""
        if not self.hooks_dir.exists():
            return 0

        loaded = 0
        for path in sorted(self.hooks_dir.glob("*.py")):
            loaded += self.load_hook_file(path, module_prefix="opennova_project_hook")
        return loaded

    def load_hook_file(self, path: str | Path, module_prefix: str = "opennova_hook") -> int:
        """Load supported hook functions from one Python file."""
        hook_path = Path(path).resolve()
        module_name = f"{module_prefix}_{hook_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, hook_path)
        if not spec or not spec.loader:
            return 0
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded = 0
        for event in self.SUPPORTED_EVENTS:
            callback = getattr(module, event, None)
            if callable(callback):
                self.register(event, callback)
                loaded += 1
        return loaded

    def run_pre_tool_use(self, event: dict[str, Any]) -> dict[str, Any] | ToolResult:
        """Run pre-tool hooks. A hook may return ToolResult to block execution."""
        return self._run_event("pre_tool_use", event)

    def run_post_tool_use(self, event: dict[str, Any]) -> dict[str, Any] | ToolResult:
        """Run post-tool hooks."""
        return self._run_event("post_tool_use", event)

    def _run_event(self, event_name: str, event: dict[str, Any]) -> dict[str, Any] | ToolResult:
        current = event
        for callback in self._callbacks.get(event_name, []):
            result = callback(current)
            if isinstance(result, ToolResult):
                return result
            if isinstance(result, dict):
                current = result
        return current
