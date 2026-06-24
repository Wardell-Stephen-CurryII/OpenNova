"""
Skill Registry - Manage loaded markdown skills.

Provides centralized management for Claude Code-style skills:
- discovery/loading from markdown skill roots
- canonical-name and bare-name resolution
- conditional activation, dynamic discovery, and session ranking
- metadata lookup and prompt materialization
"""

from __future__ import annotations

import fnmatch
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
MAX_LISTING_SKILLS = 20


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
        self._conditional_skill_names: set[str] = set()
        self._dynamic_skill_names: set[str] = set()
        self._activated_skill_names: set[str] = set()
        self._discovered_skill_dirs: set[str] = set()
        self._usage_counter = 0
        self._skill_usage: dict[str, tuple[int, int]] = {}

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
            self._apply_initial_activation_state(skill)
            self.skills[name] = skill

        self._update_stats()
        return self.skills.copy()

    def unregister(self, name: str) -> bool:
        resolution = self.resolve_skill_name(name)
        if not resolution.resolved_name:
            return False
        resolved = resolution.resolved_name
        del self.skills[resolved]
        self._conditional_skill_names.discard(resolved)
        self._dynamic_skill_names.discard(resolved)
        self._activated_skill_names.discard(resolved)
        self._skill_usage.pop(resolved, None)
        self._update_stats()
        return True

    def resolve_skill_name(self, name: str) -> SkillResolution:
        normalized = str(name).strip().lstrip("/")
        if not normalized:
            return SkillResolution(requested_name=name)

        visible_names = set(self._visible_skill_names())
        if normalized in self.skills and normalized in visible_names:
            return SkillResolution(requested_name=name, resolved_name=normalized, matches=[normalized])

        visible_matches = sorted(
            skill_name
            for skill_name in visible_names
            if skill_name.split(":")[-1] == normalized
        )
        if len(visible_matches) == 1:
            return SkillResolution(requested_name=name, resolved_name=visible_matches[0], matches=visible_matches)
        if len(visible_matches) > 1:
            return SkillResolution(requested_name=name, matches=visible_matches)

        if normalized in self.skills:
            return SkillResolution(requested_name=name, resolved_name=normalized, matches=[normalized])

        matches = sorted(skill_name for skill_name in self.skills if skill_name.split(":")[-1] == normalized)
        if len(matches) == 1:
            return SkillResolution(requested_name=name, resolved_name=matches[0], matches=matches)
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
        return self._ranked_skill_names(include_pending=True)

    def list_enabled_skills(self) -> list[str]:
        return [name for name in self._ranked_skill_names(include_pending=True) if self.is_enabled(name)]

    def list_model_invocable_skills(self) -> list[str]:
        return [
            name for name in self._ranked_skill_names(include_pending=False)
            if self._is_visible_model_invocable(name)
        ]

    def list_user_invocable_skills(self) -> list[str]:
        return [
            name for name in self._ranked_skill_names(include_pending=False)
            if self._is_visible_user_invocable(name)
        ]

    def list_pending_conditional_skills(self) -> list[str]:
        return sorted(self._conditional_skill_names)

    def list_model_invocable_skill_summaries(self) -> list[dict[str, str]]:
        summaries: list[dict[str, str]] = []
        for name in self.list_model_invocable_skills()[:MAX_LISTING_SKILLS]:
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

        skills = [self.skills[name] for name in self.list_model_invocable_skills()[:MAX_LISTING_SKILLS]]
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
            skill and self._is_visible_model_invocable(skill.name)
        )

    def can_user_invoke(self, name: str) -> bool:
        skill = self.get_skill(name)
        return bool(
            skill and self._is_visible_user_invocable(skill.name)
        )

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
        if not skill or not skill.metadata.enabled or skill.load_error or not self._is_visible(skill.name):
            return None
        return skill.materialize_prompt(args)

    def get_skill_argument_hint(self, name: str, typed_args: str = "") -> str | None:
        skill = self.get_skill(name)
        if not skill:
            return None
        typed = parse_arguments(typed_args)
        return generate_progressive_argument_hint(skill.metadata.arguments, typed)

    def activate_for_paths(self, paths: list[str], cwd: str) -> list[str]:
        activated: list[str] = []
        for name in sorted(self._conditional_skill_names):
            skill = self.skills.get(name)
            if not skill or not skill.metadata.paths:
                continue
            if self._paths_match(skill.metadata.paths, paths, cwd):
                self._conditional_skill_names.discard(name)
                self._dynamic_skill_names.add(name)
                self._activated_skill_names.add(name)
                skill.metadata.activation_state = "activated"
                activated.append(name)
        return activated

    def discover_for_paths(self, paths: list[str], cwd: str) -> list[str]:
        cwd_path = Path(cwd).resolve()
        discovered_names: list[str] = []
        discovered_sources: list[SkillSource] = []
        seen_this_call: set[str] = set()

        for raw_path in paths:
            path = Path(raw_path)
            current = (path if path.is_dir() else path.parent).resolve()
            while True:
                if current == cwd_path.parent:
                    break
                for hidden_dir in (".opennova", ".claude"):
                    skill_root = current / hidden_dir / "skills"
                    skill_root_id = str(skill_root.resolve()) if skill_root.exists() else str(skill_root)
                    if skill_root_id in self._discovered_skill_dirs or skill_root_id in seen_this_call:
                        continue
                    if skill_root.exists() and skill_root.is_dir():
                        seen_this_call.add(skill_root_id)
                        discovered_sources.append(
                            SkillSource(root=skill_root, source_type="dynamic", loaded_from="skills")
                        )
                if current == cwd_path:
                    break
                parent = current.parent
                if parent == current:
                    break
                current = parent

        for source in discovered_sources:
            self._discovered_skill_dirs.add(str(source.root.resolve()))
            loaded = SkillLoader.load_all_skills(sources=[source])
            for name, skill in loaded.items():
                skill.metadata.enabled = True
                skill.metadata.activation_state = "dynamic"
                existing = self.skills.get(name)
                if existing is None or self._source_depth(skill) >= self._source_depth(existing):
                    self.skills[name] = skill
                    self._conditional_skill_names.discard(name)
                    self._dynamic_skill_names.add(name)
                    discovered_names.append(name)

        self._update_stats()
        return discovered_names

    def record_skill_usage(self, name: str) -> None:
        resolution = self.resolve_skill_name(name)
        if not resolution.resolved_name:
            return
        resolved = resolution.resolved_name
        count, _ = self._skill_usage.get(resolved, (0, 0))
        self._usage_counter += 1
        self._skill_usage[resolved] = (count + 1, self._usage_counter)

    def get_all_metadata(self) -> dict[str, SkillMetadata]:
        return {name: skill.metadata for name, skill in self.skills.items()}

    def get_stats(self) -> SkillStats:
        return self._stats

    def _apply_initial_activation_state(self, skill: LoadedSkill) -> None:
        paths = [pattern for pattern in skill.metadata.paths if pattern and pattern != "**"]
        if paths:
            skill.metadata.paths = paths
            skill.metadata.activation_state = "conditional-pending"
            self._conditional_skill_names.add(skill.name)
            self._dynamic_skill_names.discard(skill.name)
            self._activated_skill_names.discard(skill.name)
        else:
            skill.metadata.activation_state = "static"
            self._conditional_skill_names.discard(skill.name)

    def _paths_match(self, patterns: list[str], paths: list[str], cwd: str) -> bool:
        cwd_path = Path(cwd).resolve()
        normalized_patterns = [pattern.rstrip("/") for pattern in patterns if pattern and pattern != "**"]
        for raw_path in paths:
            try:
                path = Path(raw_path).resolve()
                relative = path.relative_to(cwd_path).as_posix()
            except Exception:
                continue
            for pattern in normalized_patterns:
                variants = {
                    pattern,
                    pattern.replace("/**/", "/"),
                    pattern.replace("/**", "/*"),
                }
                if any(fnmatch.fnmatch(relative, variant) for variant in variants):
                    return True
        return False

    def _source_depth(self, skill: LoadedSkill) -> int:
        root = Path(skill.metadata.source_root) if skill.metadata.source_root else Path(".")
        return len(root.parts)

    def _is_visible(self, name: str) -> bool:
        return name in self.skills and name not in self._conditional_skill_names

    def _visible_skill_names(self) -> list[str]:
        return [name for name in self.skills if self._is_visible(name)]

    def _is_visible_model_invocable(self, name: str) -> bool:
        skill = self.skills.get(name)
        return bool(
            skill
            and self._is_visible(name)
            and skill.metadata.enabled
            and not skill.load_error
            and not skill.metadata.disable_model_invocation
        )

    def _is_visible_user_invocable(self, name: str) -> bool:
        skill = self.skills.get(name)
        return bool(
            skill
            and self._is_visible(name)
            and skill.metadata.enabled
            and not skill.load_error
            and skill.metadata.user_invocable
        )

    def _ranked_skill_names(self, *, include_pending: bool) -> list[str]:
        candidates = list(self.skills.keys()) if include_pending else self._visible_skill_names()

        def sort_key(name: str) -> tuple[int, int, int, str]:
            skill = self.skills[name]
            activation_priority = {
                "activated": 0,
                "dynamic": 1,
                "static": 2,
                "conditional-pending": 3,
            }.get(skill.metadata.activation_state, 4)
            usage_count, usage_order = self._skill_usage.get(name, (0, 0))
            return (activation_priority, -usage_count, -usage_order, name)

        return sorted(candidates, key=sort_key)

    def _update_stats(self) -> None:
        self._stats.update(self.skills)

    def clear(self) -> None:
        self.skills.clear()
        self._conditional_skill_names.clear()
        self._dynamic_skill_names.clear()
        self._activated_skill_names.clear()
        self._discovered_skill_dirs.clear()
        self._skill_usage.clear()
        self._usage_counter = 0
        self._update_stats()

    def __contains__(self, name: str) -> bool:
        return self.resolve_skill_name(name).found

    def __len__(self) -> int:
        return len(self.skills)

    def __repr__(self) -> str:
        return f"SkillRegistry(skills={len(self.skills)}, enabled={self._stats.enabled_skills})"
