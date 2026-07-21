"""UI-independent slash-command dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from opennova.cli.commands import SlashCommandRegistry


class SlashCommandDispatcher:
    """Resolve command metadata and invoke handlers on a product surface."""

    def __init__(self, registry: SlashCommandRegistry) -> None:
        self.registry = registry

    async def dispatch(
        self,
        target: Any,
        text: str,
        *,
        on_unknown: Callable[[str], None] | None = None,
    ) -> bool:
        command_text, _, args = text.partition(" ")
        command_name = command_text.lower().replace("_", "-")
        command = self.registry.get(command_name)
        if command is None or not command.handler:
            if on_unknown:
                on_unknown(command_name)
            return False
        handler = getattr(target, command.handler, None)
        if not callable(handler):
            if on_unknown:
                on_unknown(command_name)
            return False
        await handler(args)
        return True
