"""
Claude Code-style markdown skill loading for OpenNova.

Provides:
- SkillFrontmatter: Parsed skill frontmatter fields
- SkillMetadata: Normalized metadata for loaded skills
- LoadedSkill: Loaded markdown skill representation
- SkillLoader: Discovery and parsing for <skill-name>/SKILL.md directories
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from opennova.skills.arguments import parse_argument_names, substitute_arguments

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
    canonical_name: str = ""
    source_root: str = ""
    namespace: str = ""
    loaded_from: str = "discovered"
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
            "canonical_name": self.canonical_name,
            "source_root": self.source_root,
            "namespace": self.namespace,
            "loaded_from": self.loaded_from,
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

    def materialize_prompt(self, args: str = "") -> MaterializedSkill:
        """Render the skill prompt similarly to Claude Code file-based skills."""
        prompt = self.content
        if self.skill_dir:
            prompt = f"Base directory for this skill: {self.skill_dir}\n\n{prompt}"

        prompt = substitute_arguments(prompt, args, argument_names=self.metadata.arguments)
        return MaterializedSkill(
            prompt=prompt,
            resolved_name=self.name,
            source_path=self.source_path,
            skill_dir=self.skill_dir,
            allowed_tools=list(self.metadata.allowed_tools),
            model=self.metadata.model,
            argument_names=list(self.metadata.arguments),
        )


@dataclass(frozen=True)
class MaterializedSkill:
    """A concrete, invocation-ready skill prompt with runtime hints."""

    prompt: str
    resolved_name: str
    source_path: str | None
    skill_dir: str | None
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    argument_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillSource:
    """A root directory from which skills should be loaded."""

    root: Path
    plugin_name: str | None = None
    source_type: str = "discovered"
    loaded_from: str = "skills"


class SkillLoader:
    """Discover and parse directory-based Claude Code-style SKILL.md files."""

    DEFAULT_SKILL_DIRS = [
        Path(".opennova") / "skills",
        Path.home() / ".opennova" / "skills",
        Path(".claude") / "skills",
        Path.home() / ".claude" / "skills",
    ]

    @classmethod
    def discover_skills(
        cls,
        additional_dirs: list[str | Path] | None = None,
    ) -> list[Path]:
        sources = cls.normalize_sources(additional_dirs=additional_dirs)
        skill_dirs: list[Path] = []
        skill_files: list[Path] = []
        seen: set[str] = set()
        seen_dirs: set[str] = set()

        for source in sources:
            base_dir = source.root
            skill_dirs.append(base_dir)
            if not base_dir.exists() or not base_dir.is_dir():
                continue
            cls._discover_from_dir(base_dir, base_dir, skill_files, seen, seen_dirs)

        return skill_files

    @classmethod
    def normalize_sources(
        cls,
        additional_dirs: list[str | Path] | None = None,
        sources: list[SkillSource] | None = None,
    ) -> list[SkillSource]:
        normalized: list[SkillSource] = []

        if additional_dirs is None and sources is None:
            for path in cls.DEFAULT_SKILL_DIRS:
                normalized.append(SkillSource(root=cls._resolve_root(path)))

        for path in additional_dirs or []:
            normalized.append(SkillSource(root=cls._resolve_root(path)))

        for source in sources or []:
            normalized.append(
                SkillSource(
                    root=cls._resolve_root(source.root),
                    plugin_name=source.plugin_name,
                    source_type=source.source_type,
                    loaded_from=source.loaded_from,
                )
            )

        deduped: list[SkillSource] = []
        seen: set[tuple[str, str | None, str, str]] = set()
        for source in normalized:
            key = (str(source.root), source.plugin_name, source.source_type, source.loaded_from)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(source)
        return deduped

    @staticmethod
    def _resolve_root(path: str | Path) -> Path:
        root = Path(path).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        return root.resolve()

    @classmethod
    def _discover_from_dir(
        cls,
        base_dir: Path,
        current_dir: Path,
        skill_files: list[Path],
        seen: set[str],
        seen_dirs: set[str],
    ) -> None:
        direct_skill = current_dir / "SKILL.md"
        if direct_skill.exists() and direct_skill.is_file():
            identity = cls._file_identity(direct_skill)
            if identity not in seen:
                skill_files.append(direct_skill)
                seen.add(identity)
            return

        for entry in sorted(current_dir.iterdir(), key=lambda item: (item.is_symlink(), item.name)):
            if entry.is_dir():
                dir_identity = cls._dir_identity(entry)
                if dir_identity in seen_dirs:
                    continue
                seen_dirs.add(dir_identity)
                cls._discover_from_dir(base_dir, entry, skill_files, seen, seen_dirs)
                continue
            if entry.suffix.lower() != ".md" or entry.name == "SKILL.md":
                continue
            identity = cls._file_identity(entry)
            if identity in seen:
                continue
            skill_files.append(entry)
            seen.add(identity)

    @staticmethod
    def _file_identity(path: Path) -> str:
        try:
            return os.path.realpath(path)
        except OSError:
            return str(path.resolve())

    @staticmethod
    def _dir_identity(path: Path) -> str:
        try:
            return os.path.realpath(path)
        except OSError:
            return str(path.resolve())

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
    def load_skill_file(
        cls,
        file_path: str | Path,
        source: SkillSource | None = None,
    ) -> LoadedSkill | None:
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
        source = source or SkillSource(root=path.parent.parent if path.name == "SKILL.md" else path.parent)
        canonical_name, namespace = cls._build_canonical_name(path, source)
        skill_dir = str(path.parent if path.name == "SKILL.md" else path.parent)
        description = str(frontmatter.get("description") or "").strip()
        if not description:
            description = cls._extract_description_from_markdown(markdown_content)

        metadata = SkillMetadata(
            name=canonical_name,
            description=description,
            canonical_name=canonical_name,
            source_root=str(source.root),
            namespace=namespace,
            loaded_from=source.loaded_from,
            when_to_use=str(frontmatter.get("when_to_use") or ""),
            version=str(frontmatter.get("version") or ""),
            allowed_tools=cls._coerce_list(frontmatter.get("allowed-tools")),
            argument_hint=str(frontmatter.get("argument-hint") or ""),
            arguments=parse_argument_names(frontmatter.get("arguments")),
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
            name=canonical_name,
            metadata=metadata,
            content=markdown_content.strip(),
            source_type=source.source_type,
            source_path=str(path),
            skill_dir=skill_dir,
        )

    @classmethod
    def _build_canonical_name(cls, path: Path, source: SkillSource) -> tuple[str, str]:
        if path.name == "SKILL.md":
            if path.parent == source.root:
                relative_parts = [path.parent.name]
            else:
                relative_parts = list(path.parent.relative_to(source.root).parts)
        else:
            relative_parts = list(path.relative_to(source.root).with_suffix("").parts)

        namespace_parts = relative_parts[:-1]
        namespace = ":".join(namespace_parts)
        name_parts = [*([source.plugin_name] if source.plugin_name else []), *relative_parts]
        canonical_name = ":".join(name_parts)
        return canonical_name, namespace

    @classmethod
    def load_all_skills(
        cls,
        additional_dirs: list[str | Path] | None = None,
        sources: list[SkillSource] | None = None,
    ) -> dict[str, LoadedSkill]:
        loaded_skills: dict[str, LoadedSkill] = {}
        normalized_sources = cls.normalize_sources(additional_dirs=additional_dirs, sources=sources)
        seen_identities: set[str] = set()
        for source in normalized_sources:
            if not source.root.exists() or not source.root.is_dir():
                continue
            discovered: list[Path] = []
            cls._discover_from_dir(source.root, source.root, discovered, seen_identities, set())
            for skill_file in discovered:
                loaded = cls.load_skill_file(skill_file, source=source)
                if not loaded or loaded.load_error:
                    continue
                if loaded.name not in loaded_skills:
                    loaded_skills[loaded.name] = loaded
        return loaded_skills
