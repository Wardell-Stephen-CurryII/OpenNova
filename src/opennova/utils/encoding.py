"""Encoding helpers for robust execution in non-ASCII project paths."""

from __future__ import annotations

import os
from collections.abc import Mapping


def utf8_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment with UTF-8 locale defaults for Python subprocesses."""
    env = dict(os.environ if base is None else base)
    env.setdefault("LC_ALL", "en_US.UTF-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env["PYTHONUTF8"] = "1"
    return env
