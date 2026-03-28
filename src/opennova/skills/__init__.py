"""
Skills Plugin System for OpenNova.

Skills are custom tools that users can create and load from:
- ~/.opennova/skills/ directory
- Project-level .opennova/skills/ directory
- Configure in config.yaml

Each skill is a Python file with a Skill class that inherits from BaseSkill.
"""

from opennova.skills.base import BaseSkill, SkillMetadata, SkillLoader
from opennova.skills.registry import SkillRegistry

__all__ = [
    "BaseSkill",
    "SkillMetadata",
    "SkillLoader",
    "SkillRegistry",
]
