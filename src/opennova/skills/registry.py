"""
Skill Registry - Manage loaded markdown skills.

Provides centralized management for Claude Code-style skills:
- discovery/loading from <skill-name>/SKILL.md directories
- enable/disable/exclude handling
- metadata lookup and prompt materialization
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opennova.skills.base import LoadedSkill, SkillLoader, SkillMetadata


@dataclass
class SkillStats:
    """Statistics about loaded skills."""

    total_skills: int = 0
    enabled_skills: int = 0
    disabled_skills: int = 0
    error_skills: int = 0

    def update(self, skills: dict[str, LoadedSkill]) -> None:
        self.total_skills = len(skills)
        self.enabled_skills = 0
        self.disabled_skills = 0
        self.error_skills = 0

        for skill in skills.values():
            if skill.load_error:
                self.error_skills += 1
            elif not skill.metadata.enabled:
                self.disabled_skills += 1
            else:
                self.enabled_skills += 1


class SkillRegistry:
    """Registry for managing loaded markdown skills."""

    def __init__(self):
        self.skills: dict[str, LoadedSkill] = {}
        self._stats = SkillStats()

    def load_all(
        self,
        directories: list[str | Path] | None = None,
        excluded: list[str] | None = None,
        replace_existing: bool = True,
    ) -> dict[str, LoadedSkill]:
        excluded_names = set(excluded or [])

        if replace_existing:
            self.clear()

        discovered = SkillLoader.load_all_skills(directories)
        for name, skill in discovered.items():
            skill.metadata.enabled = name not in excluded_names
            self.skills[name] = skill

        self._update_stats()
        return self.skills.copy()

    def unregister(self, name: str) -> bool:
        if name not in self.skills:
            return False
        del self.skills[name]
        self._update_stats()
        return True

    def get_skill(self, name: str) -> LoadedSkill | None:
        return self.skills.get(name)

    def enable_skill(self, name: str) -> bool:
        skill = self.skills.get(name)
        if not skill:
            return False
        skill.metadata.enabled = True
        self._update_stats()
        return True

    def disable_skill(self, name: str) -> bool:
        skill = self.skills.get(name)
        if not skill:
            return False
        skill.metadata.enabled = False
        self._update_stats()
        return True

    def is_enabled(self, name: str) -> bool:
        skill = self.skills.get(name)
        return bool(skill and skill.metadata.enabled and not skill.load_error)

    def list_skills(self) -> list[str]:
        return list(self.skills.keys())

    def list_enabled_skills(self) -> list[str]:
        return [name for name, skill in self.skills.items() if skill.metadata.enabled and not skill.load_error]

    def get_skill_info(self, name: str) -> dict[str, Any] | None:
        skill = self.skills.get(name)
        if not skill:
            return None

        info: dict[str, Any] = {
            "name": name,
            "source": skill.source_path,
            "source_type": skill.source_type,
            "error": skill.load_error,
            "skill_dir": skill.skill_dir,
        }
        info.update(skill.metadata.to_dict())
        return info

    def materialize_skill_prompt(self, name: str, args: str = "") -> str | None:
        skill = self.skills.get(name)
        if not skill or not skill.metadata.enabled or skill.load_error:
            return None
        return skill.materialize_prompt(args)

    def get_all_metadata(self) -> dict[str, SkillMetadata]:
        return {name: skill.metadata for name, skill in self.skills.items()}

    def get_stats(self) -> SkillStats:
        return self._stats

    def _update_stats(self) -> None:
        self._stats.update(self.skills)

    def clear(self) -> None:
        self.skills.clear()
        self._update_stats()

    def __contains__(self, name: str) -> bool:
        return name in self.skills

    def __len__(self) -> int:
        return len(self.skills)

    def __repr__(self) -> str:
        return f"SkillRegistry(skills={len(self.skills)}, enabled={self._stats.enabled_skills})"
