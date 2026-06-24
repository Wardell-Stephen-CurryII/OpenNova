"""Declarative skill-hook adaptation for OpenNova runtime hooks."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from opennova.tools.base import ToolResult


def is_valid_hook_config(value: Any) -> bool:
    """Return whether the parsed frontmatter hook config is structurally valid."""
    if not isinstance(value, dict):
        return False
    for event_name, matchers in value.items():
        if not isinstance(event_name, str) or not isinstance(matchers, list):
            return False
        for matcher in matchers:
            if not isinstance(matcher, dict):
                return False
            if "matcher" in matcher and not isinstance(matcher.get("matcher"), str):
                return False
            hooks = matcher.get("hooks")
            if not isinstance(hooks, list):
                return False
            for hook in hooks:
                if not isinstance(hook, dict):
                    return False
    return True


def make_declarative_hook_callback(
    event_name: str,
    matcher: str,
    hook_definition: dict[str, Any],
) -> tuple[callable, bool]:
    """Build a HookManager callback from one declarative skill hook."""

    once = bool(hook_definition.get("once", False))
    add_metadata = hook_definition.get("add_metadata")
    set_arguments = hook_definition.get("set_arguments")
    block_message = hook_definition.get("block")

    def callback(event: dict[str, Any]) -> dict[str, Any] | ToolResult:
        if event_name in {"pre_tool_use", "post_tool_use"} and matcher and event.get("tool_name") != matcher:
            return event

        updated = deepcopy(event)
        if isinstance(add_metadata, dict):
            updated.setdefault("metadata", {}).update(deepcopy(add_metadata))
        if isinstance(set_arguments, dict):
            updated.setdefault("arguments", {}).update(deepcopy(set_arguments))
        if isinstance(block_message, str) and block_message.strip():
            return ToolResult(success=False, output="", error=block_message.strip())
        return updated

    return callback, once


def register_skill_hooks(
    hook_manager: Any,
    hooks: dict[str, Any],
    *,
    skill_name: str,
    skill_root: str | None = None,
) -> int:
    """Register declarative skill hooks as session hooks."""
    del skill_root
    if not hooks:
        return 0

    source = f"skill:{skill_name}"
    registered = 0
    for event_name, matchers in hooks.items():
        if event_name not in hook_manager.SUPPORTED_EVENTS or not isinstance(matchers, list):
            continue
        for matcher_config in matchers:
            if not isinstance(matcher_config, dict):
                continue
            matcher = str(matcher_config.get("matcher") or "")
            hook_entries = matcher_config.get("hooks")
            if not isinstance(hook_entries, list):
                continue
            for hook_definition in hook_entries:
                if not isinstance(hook_definition, dict):
                    continue
                callback, once = make_declarative_hook_callback(event_name, matcher, hook_definition)
                hook_manager.register_session_hook(event_name, callback, source=source, once=once)
                registered += 1
    return registered
