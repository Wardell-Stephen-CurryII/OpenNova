"""
Skill Registry - Manage loaded skills.

Provides centralized management for all skills:
- Registration and lookup
- Enable/disable skills
- Integration with ToolRegistry
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opennova.skills.base import BaseSkill, LoadedSkill, SkillLoader, SkillMetadata
from opennova.tools.base import ToolRegistry


@dataclass
class SkillStats:
    """Statistics about loaded skills."""

    total_skills: int = 0
    enabled_skills: int = 0
    disabled_skills: int = 0
    error_skills: int = 0

    def update(self, skills: dict[str, LoadedSkill]) -> None:
        """Update statistics from skills dict."""
        self.total_skills = len(skills)
        self.enabled_skills = 0
        self.disabled_skills = 0
        self.error_skills = 0

        for skill in skills.values():
            if skill.load_error:
                self.error_skills += 1
            elif skill.metadata and not skill.metadata.enabled:
                self.disabled_skills += 1
            else:
                self.enabled_skills += 1


class SkillRegistry:
    """
    Registry for managing skills.

    Features:
    - Load skills from directories
    - Register with ToolRegistry
    - Enable/disable individual skills
    - Track skill statistics
    """

    def __init__(self, tool_registry: ToolRegistry | None = None):
        """
        Initialize skill registry.

        Args:
            tool_registry: Optional tool registry to register skills with
        """
        self.tool_registry = tool_registry or ToolRegistry()
        self.skills: dict[str, LoadedSkill] = {}
        self._stats = SkillStats()

    def _store_loaded_skill(self, name: str, skill: LoadedSkill) -> None:
        """Store a loaded skill and register it if enabled."""
        self.skills[name] = skill

        if skill.metadata and not skill.metadata.enabled:
            return

        instance = skill.get_instance()
        if instance:
            self.tool_registry.register(instance)

    def _unregister_skill_tool(self, name: str) -> None:
        """Remove a skill tool from the tool registry if present."""
        if self.tool_registry.has_tool(name):
            self.tool_registry.unregister(name)

    def load_all(
        self,
        directories: list[str | Path] | None = None,
        builtins: list[type[BaseSkill]] | None = None,
        excluded: list[str] | None = None,
        replace_existing: bool = True,
    ) -> dict[str, LoadedSkill]:
        """Load built-in and discovered skills through one canonical path."""
        excluded_names = set(excluded or [])

        if replace_existing:
            self.clear()

        loaded: dict[str, LoadedSkill] = {}

        for skill_class in builtins or []:
            instance = skill_class()
            metadata = instance.get_metadata()
            metadata.enabled = metadata.enabled and metadata.name not in excluded_names
            loaded_skill = LoadedSkill(
                skill_class=skill_class,
                instance=instance,
                metadata=metadata,
                source_type="builtin",
            )
            loaded[metadata.name] = loaded_skill
            self._store_loaded_skill(metadata.name, loaded_skill)

        discovered = SkillLoader.load_all_skills(directories)
        for name, skill in discovered.items():
            if skill.load_error:
                continue
            if skill.metadata:
                skill.metadata.enabled = skill.metadata.enabled and name not in excluded_names
                skill.source_type = "discovered"
            loaded[name] = skill
            self._store_loaded_skill(name, skill)

        self._update_stats()
        return loaded

    def register(self, skill: BaseSkill) -> None:
        """
        Register a skill instance directly.

        Args:
            skill: Skill instance to register
        """
        metadata = skill.get_metadata()
        self.skills[metadata.name] = LoadedSkill(
            skill_class=type(skill),
            instance=skill,
            metadata=metadata,
            source_type="builtin",
        )

        self.tool_registry.register(skill)

        self._update_stats()

    def unregister(self, name: str) -> bool:
        """
        Unregister a skill by name.

        Args:
            name: Skill name

        Returns:
            True if skill was removed
        """
        if name not in self.skills:
            return False

        del self.skills[name]
        self._unregister_skill_tool(name)
        self._update_stats()

        return True

    def get_skill(self, name: str) -> LoadedSkill | None:
        """
        Get a skill by name.

        Args:
            name: Skill name

        Returns:
            LoadedSkill or None
        """
        return self.skills.get(name)

    def get_skill_instance(self, name: str) -> BaseSkill | None:
        """
        Get skill instance by name.

        Args:
            name: Skill name

        Returns:
            Skill instance or None
        """
        loaded = self.skills.get(name)
        if loaded:
            return loaded.get_instance()
        return None

    def enable_skill(self, name: str) -> bool:
        """
        Enable a skill.

        Args:
            name: Skill name

        Returns:
            True if skill was enabled
        """
        skill = self.skills.get(name)
        if not skill or not skill.metadata:
            return False

        skill.metadata.enabled = True

        instance = skill.get_instance()
        if instance and not self.tool_registry.has_tool(name):
            self.tool_registry.register(instance)

        self._update_stats()

        return True

    def disable_skill(self, name: str) -> bool:
        """
        Disable a skill.

        Args:
            name: Skill name

        Returns:
            True if skill was disabled
        """
        skill = self.skills.get(name)
        if not skill or not skill.metadata:
            return False

        skill.metadata.enabled = False
        self._unregister_skill_tool(name)
        self._update_stats()

        return True

    def is_enabled(self, name: str) -> bool:
        """Check if a skill is enabled."""
        skill = self.skills.get(name)
        if not skill or not skill.metadata:
            return False
        return skill.metadata.enabled

    def list_skills(self) -> list[str]:
        """Get list of all skill names."""
        return list(self.skills.keys())

    def list_enabled_skills(self) -> list[str]:
        """Get list of enabled skill names."""
        return [
            name
            for name, skill in self.skills.items()
            if skill.metadata and skill.metadata.enabled and not skill.load_error
        ]

    def get_skill_info(self, name: str) -> dict[str, Any] | None:
        """
        Get information about a skill.

        Args:
            name: Skill name

        Returns:
            Dict with skill info or None
        """
        skill = self.skills.get(name)
        if not skill:
            return None

        info: dict[str, Any] = {
            "name": name,
            "source": skill.source_path,
            "source_type": skill.source_type,
            "error": skill.load_error,
        }

        if skill.metadata:
            info.update(skill.metadata.to_dict())

        instance = skill.get_instance()
        if instance:
            info["tool_name"] = instance.name
            info["tool_description"] = instance.description

        return info

    def get_all_metadata(self) -> dict[str, SkillMetadata]:
        """Get metadata for all skills."""
        return {
            name: skill.metadata
            for name, skill in self.skills.items()
            if skill.metadata
        }

    def get_stats(self) -> SkillStats:
        """Get skill statistics."""
        return self._stats

    def _update_stats(self) -> None:
        """Update internal statistics."""
        self._stats.update(self.skills)

    def clear(self) -> None:
        """Remove all skills."""
        for name in list(self.skills.keys()):
            self.unregister(name)

    def __contains__(self, name: str) -> bool:
        return name in self.skills

    def __len__(self) -> int:
        return len(self.skills)

    def __repr__(self) -> str:
        return f"SkillRegistry(skills={len(self.skills)}, enabled={self._stats.enabled_skills})"
