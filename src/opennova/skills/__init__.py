"""
Skills Plugin System for OpenNova.

Skills follow the Claude Code-style markdown format:
- ~/.opennova/skills/<skill-name>/SKILL.md
- .opennova/skills/<skill-name>/SKILL.md
- configured skill directories with the same layout

Each skill is a markdown prompt with YAML frontmatter, not a Python class.
"""

from opennova.skills.arguments import (
    generate_progressive_argument_hint,
    parse_argument_names,
    parse_arguments,
    substitute_arguments,
)
from opennova.skills.base import (
    LoadedSkill,
    MaterializedSkill,
    SkillLoader,
    SkillMetadata,
    SkillSource,
)
from opennova.skills.registry import SkillRegistry

__all__ = [
    "LoadedSkill",
    "MaterializedSkill",
    "SkillMetadata",
    "SkillLoader",
    "SkillSource",
    "SkillRegistry",
    "parse_arguments",
    "parse_argument_names",
    "generate_progressive_argument_hint",
    "substitute_arguments",
]
