"""Explicit runtime bootstrap profiles and side-effect-free inspection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from opennova.tools.catalog import builtin_tool_names


class RuntimeBootstrapProfile(StrEnum):
    """Declare which runtime side effects a product surface permits."""

    INSPECT = "inspect"
    BARE = "bare"
    INTERACTIVE = "interactive"
    HEADLESS = "headless"


@dataclass(frozen=True)
class RuntimeInspectionSnapshot:
    """Information available without constructing providers or extensions."""

    profile: RuntimeBootstrapProfile
    tool_names: tuple[str, ...]


def inspect_runtime() -> RuntimeInspectionSnapshot:
    """Build a pure inspection snapshot with no filesystem or network writes."""
    return RuntimeInspectionSnapshot(
        profile=RuntimeBootstrapProfile.INSPECT,
        tool_names=tuple(builtin_tool_names()),
    )
