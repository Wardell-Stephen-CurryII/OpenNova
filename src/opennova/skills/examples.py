"""
Bundled skill locations for OpenNova.

Built-in skills use the same Claude Code-style markdown layout as user skills:
<skill-name>/SKILL.md.
"""

from pathlib import Path


def get_builtin_skill_dirs() -> list[Path]:
    """Return bundled markdown skill directories shipped with OpenNova."""
    bundled_dir = Path(__file__).parent / "bundled"
    return [bundled_dir] if bundled_dir.exists() else []
