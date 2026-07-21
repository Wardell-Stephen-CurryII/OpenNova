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


@dataclass(frozen=True)
class RuntimeBootstrapPolicy:
    """Side effects permitted by one runtime profile."""

    create_provider: bool
    create_session: bool
    load_extensions: bool
    load_skills: bool
    connect_mcp: bool


BOOTSTRAP_POLICIES = {
    RuntimeBootstrapProfile.INSPECT: RuntimeBootstrapPolicy(False, False, False, False, False),
    RuntimeBootstrapProfile.BARE: RuntimeBootstrapPolicy(True, True, False, False, False),
    RuntimeBootstrapProfile.INTERACTIVE: RuntimeBootstrapPolicy(True, True, True, True, True),
    RuntimeBootstrapProfile.HEADLESS: RuntimeBootstrapPolicy(True, True, True, True, True),
}


def bootstrap_policy(profile: RuntimeBootstrapProfile | str) -> RuntimeBootstrapPolicy:
    """Return the explicit side-effect policy for a product surface."""
    return BOOTSTRAP_POLICIES[RuntimeBootstrapProfile(profile)]


def inspect_runtime() -> RuntimeInspectionSnapshot:
    """Build a pure inspection snapshot with no filesystem or network writes."""
    return RuntimeInspectionSnapshot(
        profile=RuntimeBootstrapProfile.INSPECT,
        tool_names=tuple(builtin_tool_names()),
    )
