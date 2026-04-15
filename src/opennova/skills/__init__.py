"""
Skills Plugin System for OpenNova.

Skills follow the Claude Code-style markdown format:
- ~/.opennova/skills/<skill-name>/SKILL.md
- .opennova/skills/<skill-name>/SKILL.md
- configured skill directories with the same layout

Each skill is a markdown prompt with YAML frontmatter, not a Python class.
"""

from opennova.skills.base import LoadedSkill, SkillLoader, SkillMetadata
from opennova.skills.registry import SkillRegistry

__all__ = [
    "LoadedSkill",
    "SkillMetadata",
    "SkillLoader",
    "SkillRegistry",
]
