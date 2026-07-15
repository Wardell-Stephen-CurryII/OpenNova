"""Local hook loading and execution for OpenNova."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opennova.security.workspace_trust import digest_paths
from opennova.tools.base import ToolResult

HookCallback = Callable[[dict[str, Any]], dict[str, Any] | ToolResult | None]


@dataclass
class HookRegistration:
    """One registered hook callback."""

    callback: HookCallback
    source: str = "project"
    once: bool = False
    session_scoped: bool = False


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
        self._callbacks: dict[str, list[HookRegistration]] = {
            event: [] for event in self.SUPPORTED_EVENTS
        }

    def register(
        self,
        event: str,
        callback: HookCallback,
        *,
        source: str = "project",
        once: bool = False,
        session_scoped: bool = False,
    ) -> None:
        """Register a callback for a supported hook event."""
        if event not in self.SUPPORTED_EVENTS:
            raise ValueError(f"Unsupported hook event: {event}")
        self._callbacks[event].append(
            HookRegistration(
                callback=callback,
                source=source,
                once=once,
                session_scoped=session_scoped,
            )
        )

    def register_session_hook(
        self,
        event: str,
        callback: HookCallback,
        *,
        source: str,
        once: bool = False,
    ) -> None:
        """Register a session-scoped hook callback."""
        self.register(
            event,
            callback,
            source=source,
            once=once,
            session_scoped=True,
        )

    def clear_session_hooks(self, source: str | None = None) -> int:
        """Remove session hooks, optionally limited to one source."""
        cleared = 0
        for event, registrations in self._callbacks.items():
            kept: list[HookRegistration] = []
            for registration in registrations:
                is_session = registration.session_scoped
                matches_source = source is None or registration.source == source
                if is_session and matches_source:
                    cleared += 1
                    continue
                kept.append(registration)
            self._callbacks[event] = kept
        return cleared

    def clear_source(self, source: str, *, prefix: bool = False) -> int:
        """Remove callbacks loaded from an extension source."""
        cleared = 0
        for event, registrations in self._callbacks.items():
            kept: list[HookRegistration] = []
            for registration in registrations:
                matches = (
                    registration.source.startswith(source)
                    if prefix
                    else registration.source == source
                )
                if matches:
                    cleared += 1
                    continue
                kept.append(registration)
            self._callbacks[event] = kept
        return cleared

    def project_hook_paths(self) -> list[Path]:
        """Return executable project hook files in deterministic order."""
        if not self.hooks_dir.exists():
            return []
        return sorted(path.resolve() for path in self.hooks_dir.glob("*.py") if path.is_file())

    def project_hooks_digest(self) -> str:
        """Return a digest bound to project hook paths and contents."""
        paths = self.project_hook_paths()
        return digest_paths(self.project_path, paths) if paths else ""

    def load_project_hooks(self) -> int:
        """Load hook functions from .opennova/hooks/*.py."""
        paths = self.project_hook_paths()
        if not paths:
            return 0

        self.clear_source("workspace-hooks")
        loaded = 0
        for path in paths:
            loaded += self.load_hook_file(
                path,
                module_prefix="opennova_project_hook",
                source="workspace-hooks",
            )
        return loaded

    def load_hook_file(
        self,
        path: str | Path,
        module_prefix: str = "opennova_hook",
        *,
        source: str = "project",
    ) -> int:
        """Load supported hook functions from one Python file."""
        hook_path = Path(path).resolve()
        try:
            hook_path.relative_to(self.project_path)
        except ValueError as exc:
            raise ValueError(f"Hook path is outside project directory: {path}") from exc
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
                self.register(event, callback, source=source)
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
        registrations = list(self._callbacks.get(event_name, []))
        to_remove: list[HookRegistration] = []
        for registration in registrations:
            result = registration.callback(current)
            if isinstance(result, ToolResult):
                if registration.once and result.success:
                    to_remove.append(registration)
                self._remove_registrations(event_name, to_remove)
                return result
            if isinstance(result, dict):
                current = result
                if registration.once:
                    to_remove.append(registration)
        self._remove_registrations(event_name, to_remove)
        return current

    def _remove_registrations(
        self,
        event_name: str,
        registrations: list[HookRegistration],
    ) -> None:
        if not registrations:
            return
        active = self._callbacks.get(event_name, [])
        self._callbacks[event_name] = [item for item in active if item not in registrations]
