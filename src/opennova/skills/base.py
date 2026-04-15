"""
Claude Code-style markdown skill loading for OpenNova.

Provides:
- SkillFrontmatter: Parsed skill frontmatter fields
- SkillMetadata: Normalized metadata for loaded skills
- LoadedSkill: Loaded markdown skill representation
- SkillLoader: Discovery and parsing for <skill-name>/SKILL.md directories
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?", re.MULTILINE)


@dataclass
class SkillFrontmatter:
    """Raw/normalized frontmatter fields for a markdown skill."""

    name: str | None = None
    description: str | None = None
    when_to_use: str | None = None
    version: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str | None = None
    arguments: list[str] = field(default_factory=list)
    model: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    context: str | None = None
    agent: str | None = None
    effort: str | int | None = None
    paths: list[str] = field(default_factory=list)
    shell: Any = None


@dataclass
class SkillMetadata:
    """Normalized metadata for a discovered markdown skill."""

    name: str
    description: str
    when_to_use: str = ""
    version: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str = ""
    arguments: list[str] = field(default_factory=list)
    model: str = ""
    user_invocable: bool = True
    disable_model_invocation: bool = False
    context: str = ""
    agent: str = ""
    effort: str = ""
    paths: list[str] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "version": self.version,
            "allowed_tools": self.allowed_tools,
            "argument_hint": self.argument_hint,
            "arguments": self.arguments,
            "model": self.model,
            "user_invocable": self.user_invocable,
            "disable_model_invocation": self.disable_model_invocation,
            "context": self.context,
            "agent": self.agent,
            "effort": self.effort,
            "paths": self.paths,
            "enabled": self.enabled,
        }


@dataclass
class LoadedSkill:
    """A loaded markdown skill and its associated metadata/content."""

    name: str
    metadata: SkillMetadata
    content: str
    source_type: str = "discovered"
    source_path: str | None = None
    skill_dir: str | None = None
    load_error: str | None = None

    def materialize_prompt(self, args: str = "") -> str:
        """Render the skill prompt similarly to Claude Code file-based skills."""
        prompt = self.content
        if self.skill_dir:
            prompt = f"Base directory for this skill: {self.skill_dir}\n\n{prompt}"

        if args:
            prompt = prompt.replace("$ARGUMENTS", args)
            prompt = prompt.replace("$ARGS", args)
        return prompt


class SkillLoader:
    """Discover and parse directory-based Claude Code-style SKILL.md files."""

    DEFAULT_SKILL_DIRS = [
        Path.home() / ".opennova" / "skills",
        Path(".opennova") / "skills",
    ]

    @classmethod
    def discover_skills(
        cls,
        additional_dirs: list[str | Path] | None = None,
    ) -> list[Path]:
        skill_dirs = [Path(d) for d in cls.DEFAULT_SKILL_DIRS]
        if additional_dirs:
            skill_dirs.extend(Path(d) for d in additional_dirs)

        skill_files: list[Path] = []
        seen: set[Path] = set()

        for base_dir in skill_dirs:
            if not base_dir.exists() or not base_dir.is_dir():
                continue

            for entry in base_dir.iterdir():
                if not entry.is_dir():
                    continue
                skill_file = entry / "SKILL.md"
                if skill_file.exists() and skill_file.is_file() and skill_file not in seen:
                    skill_files.append(skill_file)
                    seen.add(skill_file)

        return skill_files

    @classmethod
    def parse_frontmatter(cls, raw_text: str, file_path: str | Path) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter with permissive fallback semantics."""
        match = _FRONTMATTER_RE.match(raw_text)
        if not match:
            return {}, raw_text

        frontmatter_text = match.group(1)
        body = raw_text[match.end() :]

        try:
            data = yaml.safe_load(frontmatter_text) or {}
            if not isinstance(data, dict):
                data = {}
            return data, body
        except Exception:
            return {}, body

    @staticmethod
    def _extract_description_from_markdown(content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            return stripped
        return ""

    @staticmethod
    def _coerce_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _coerce_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1", "on"}:
                return True
            if lowered in {"false", "no", "0", "off"}:
                return False
        return default

    @classmethod
    def load_skill_file(cls, file_path: str | Path) -> LoadedSkill | None:
        path = Path(file_path)
        if not path.exists():
            return None

        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as exc:
            return LoadedSkill(
                name=path.parent.name,
                metadata=SkillMetadata(name=path.parent.name, description=""),
                content="",
                source_path=str(path),
                skill_dir=str(path.parent),
                load_error=str(exc),
            )

        frontmatter, markdown_content = cls.parse_frontmatter(raw, path)
        skill_name = path.parent.name
        description = str(frontmatter.get("description") or "").strip()
        if not description:
            description = cls._extract_description_from_markdown(markdown_content)

        metadata = SkillMetadata(
            name=str(frontmatter.get("name") or skill_name),
            description=description,
            when_to_use=str(frontmatter.get("when_to_use") or ""),
            version=str(frontmatter.get("version") or ""),
            allowed_tools=cls._coerce_list(frontmatter.get("allowed-tools")),
            argument_hint=str(frontmatter.get("argument-hint") or ""),
            arguments=cls._coerce_list(frontmatter.get("arguments")),
            model=str(frontmatter.get("model") or ""),
            user_invocable=cls._coerce_bool(frontmatter.get("user-invocable"), True),
            disable_model_invocation=cls._coerce_bool(
                frontmatter.get("disable-model-invocation"),
                False,
            ),
            context=str(frontmatter.get("context") or ""),
            agent=str(frontmatter.get("agent") or ""),
            effort="" if frontmatter.get("effort") is None else str(frontmatter.get("effort")),
            paths=cls._coerce_list(frontmatter.get("paths")),
        )

        return LoadedSkill(
            name=metadata.name,
            metadata=metadata,
            content=markdown_content.strip(),
            source_type="discovered",
            source_path=str(path),
            skill_dir=str(path.parent),
        )

    @classmethod
    def load_all_skills(
        cls,
        additional_dirs: list[str | Path] | None = None,
    ) -> dict[str, LoadedSkill]:
        loaded_skills: dict[str, LoadedSkill] = {}
        for skill_file in cls.discover_skills(additional_dirs):
            loaded = cls.load_skill_file(skill_file)
            if not loaded or loaded.load_error:
                continue
            if loaded.name not in loaded_skills:
                loaded_skills[loaded.name] = loaded
        return loaded_skills
