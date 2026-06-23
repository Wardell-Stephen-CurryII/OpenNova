"""
Skill Registry - Manage loaded markdown skills.

Provides centralized management for Claude Code-style skills:
- discovery/loading from markdown skill roots
- canonical-name and bare-name resolution
- metadata lookup and prompt materialization
- budget-constrained progressive disclosure listing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opennova.skills.arguments import generate_progressive_argument_hint, parse_arguments
from opennova.skills.base import (
    LoadedSkill,
    MaterializedSkill,
    SkillLoader,
    SkillMetadata,
    SkillSource,
)

SKILL_LISTING_CHAR_BUDGET = 8_000
MAX_SKILL_DESC_CHARS = 250


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


@dataclass
class SkillResolution:
    """Resolution result for canonical and bare-name lookups."""

    requested_name: str
    resolved_name: str | None = None
    matches: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.resolved_name is not None

    @property
    def is_ambiguous(self) -> bool:
        return self.resolved_name is None and len(self.matches) > 1


class SkillRegistry:
    """Registry for managing loaded markdown skills."""

    def __init__(self):
        self.skills: dict[str, LoadedSkill] = {}
        self._stats = SkillStats()

    def load_all(
        self,
        directories: list[str | Path] | None = None,
        sources: list[SkillSource] | None = None,
        excluded: list[str] | None = None,
        replace_existing: bool = True,
    ) -> dict[str, LoadedSkill]:
        excluded_names = set(excluded or [])

        if replace_existing:
            self.clear()

        discovered = SkillLoader.load_all_skills(directories, sources=sources)
        for name, skill in discovered.items():
            skill.metadata.enabled = name not in excluded_names
            self.skills[name] = skill

        self._update_stats()
        return self.skills.copy()

    def unregister(self, name: str) -> bool:
        resolution = self.resolve_skill_name(name)
        if not resolution.resolved_name:
            return False
        del self.skills[resolution.resolved_name]
        self._update_stats()
        return True

    def resolve_skill_name(self, name: str) -> SkillResolution:
        normalized = str(name).strip().lstrip("/")
        if not normalized:
            return SkillResolution(requested_name=name)

        if normalized in self.skills:
            return SkillResolution(
                requested_name=name,
                resolved_name=normalized,
                matches=[normalized],
            )

        matches = sorted(
            skill_name for skill_name in self.skills if skill_name.split(":")[-1] == normalized
        )
        if len(matches) == 1:
            return SkillResolution(
                requested_name=name,
                resolved_name=matches[0],
                matches=matches,
            )
        return SkillResolution(requested_name=name, matches=matches)

    def get_skill(self, name: str) -> LoadedSkill | None:
        resolution = self.resolve_skill_name(name)
        if not resolution.resolved_name:
            return None
        return self.skills.get(resolution.resolved_name)

    def enable_skill(self, name: str) -> bool:
        skill = self.get_skill(name)
        if not skill:
            return False
        skill.metadata.enabled = True
        self._update_stats()
        return True

    def disable_skill(self, name: str) -> bool:
        skill = self.get_skill(name)
        if not skill:
            return False
        skill.metadata.enabled = False
        self._update_stats()
        return True

    def is_enabled(self, name: str) -> bool:
        skill = self.get_skill(name)
        return bool(skill and skill.metadata.enabled and not skill.load_error)

    def list_skills(self) -> list[str]:
        return sorted(self.skills.keys())

    def list_enabled_skills(self) -> list[str]:
        return sorted(
            name
            for name, skill in self.skills.items()
            if skill.metadata.enabled and not skill.load_error
        )

    def list_model_invocable_skills(self) -> list[str]:
        return sorted(
            name
            for name, skill in self.skills.items()
            if skill.metadata.enabled and not skill.load_error and not skill.metadata.disable_model_invocation
        )

    def list_user_invocable_skills(self) -> list[str]:
        return sorted(
            name
            for name, skill in self.skills.items()
            if skill.metadata.enabled and not skill.load_error and skill.metadata.user_invocable
        )

    def list_model_invocable_skill_summaries(self) -> list[dict[str, str]]:
        summaries: list[dict[str, str]] = []
        for name in self.list_model_invocable_skills():
            skill = self.skills[name]
            summaries.append(
                {
                    "name": name,
                    "description": skill.metadata.description or "",
                    "when_to_use": skill.metadata.when_to_use or "",
                    "argument_hint": skill.metadata.argument_hint or "",
                }
            )
        return summaries

    def format_skill_listing(self, max_chars: int | None = None) -> str:
        if max_chars is None:
            max_chars = SKILL_LISTING_CHAR_BUDGET

        skills = [self.skills[name] for name in self.list_model_invocable_skills()]
        if not skills:
            return ""

        def _format_entry(skill: LoadedSkill, max_desc: int | None = None) -> str:
            desc = skill.metadata.description or ""
            if max_desc is not None and len(desc) > max_desc:
                desc = desc[: max_desc - 1] + "…"
            return f"- {skill.name}: {desc}"

        full_entries = [_format_entry(skill) for skill in skills]
        full_total = sum(len(entry) for entry in full_entries) + len(full_entries) - 1
        if full_total <= max_chars:
            return "\n".join(full_entries)

        name_overhead = sum(len(skill.name) + 4 for skill in skills) + len(skills) - 1
        available_for_descs = max_chars - name_overhead
        max_desc_len = max(available_for_descs // len(skills), 20)
        if max_desc_len < 20:
            return "\n".join(f"- {skill.name}" for skill in skills)
        return "\n".join(_format_entry(skill, max_desc_len) for skill in skills)

    def can_model_invoke(self, name: str) -> bool:
        skill = self.get_skill(name)
        return bool(
            skill
            and skill.metadata.enabled
            and not skill.load_error
            and not skill.metadata.disable_model_invocation
        )

    def can_user_invoke(self, name: str) -> bool:
        skill = self.get_skill(name)
        return bool(skill and skill.metadata.enabled and not skill.load_error and skill.metadata.user_invocable)

    def get_skill_info(self, name: str) -> dict[str, Any] | None:
        resolution = self.resolve_skill_name(name)
        if not resolution.resolved_name:
            if resolution.is_ambiguous:
                return {"name": name, "ambiguous": True, "matches": resolution.matches}
            return None

        skill = self.skills[resolution.resolved_name]
        info: dict[str, Any] = {
            "name": resolution.resolved_name,
            "source": skill.source_path,
            "source_type": skill.source_type,
            "error": skill.load_error,
            "skill_dir": skill.skill_dir,
            "model_invocable": self.can_model_invoke(resolution.resolved_name),
            "user_invocable": self.can_user_invoke(resolution.resolved_name),
        }
        info.update(skill.metadata.to_dict())
        return info

    def materialize_skill_prompt(self, name: str, args: str = "") -> MaterializedSkill | None:
        skill = self.get_skill(name)
        if not skill or not skill.metadata.enabled or skill.load_error:
            return None
        return skill.materialize_prompt(args)

    def get_skill_argument_hint(self, name: str, typed_args: str = "") -> str | None:
        skill = self.get_skill(name)
        if not skill:
            return None
        typed = parse_arguments(typed_args)
        return generate_progressive_argument_hint(skill.metadata.arguments, typed)

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
        return self.resolve_skill_name(name).found

    def __len__(self) -> int:
        return len(self.skills)

    def __repr__(self) -> str:
        return f"SkillRegistry(skills={len(self.skills)}, enabled={self._stats.enabled_skills})"
