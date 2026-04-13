"""
Base Skill and Skill Loader.

Provides:
- BaseSkill: Base class for user-defined skills
- SkillMetadata: Metadata about a skill
- SkillLoader: Dynamic skill loading from files
"""

import importlib.util
import sys
from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opennova.tools.base import BaseTool, ToolResult


@dataclass
class SkillMetadata:
    """
    Metadata for a skill.

    Attributes:
        name: Skill name
        version: Version string
        description: What the skill does
        author: Author name
        tags: Tags for categorization
        requires: Required dependencies
        enabled: Whether the skill is enabled
    """

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "tags": self.tags,
            "requires": self.requires,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillMetadata":
        """Create from dictionary."""
        return cls(
            name=data.get("name", "unknown"),
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            tags=data.get("tags", []),
            requires=data.get("requires", []),
            enabled=data.get("enabled", True),
        )


class BaseSkill(BaseTool):
    """
    Base class for user-defined skills.

    Skills are specialized tools that can be loaded dynamically.
    Override the execute() method to implement your skill logic.

    Example:
        class MySkill(BaseSkill):
            name = "my_skill"
            description = "Does something cool"

            metadata = SkillMetadata(
                name="my_skill",
                version="1.0.0",
                description="A cool skill",
                author="Your Name",
            )

            def execute(self, **kwargs):
                # Your skill logic here
                return ToolResult(success=True, output="Done!")
    """

    metadata: SkillMetadata | None = None

    def get_metadata(self) -> SkillMetadata:
        """Get skill metadata."""
        if self.metadata is None:
            return SkillMetadata(name=self.name, description=self.description)
        return self.metadata


@dataclass
class LoadedSkill:
    """A loaded skill with its metadata and instance."""

    skill_class: type[BaseSkill]
    instance: BaseSkill | None = None
    metadata: SkillMetadata | None = None
    source_type: str = "discovered"
    source_path: str | None = None
    load_error: str | None = None

    def get_instance(self) -> BaseSkill | None:
        """Get or create skill instance."""
        if self.instance is None and self.load_error is None:
            try:
                self.instance = self.skill_class()
            except Exception as e:
                self.load_error = str(e)
        return self.instance


class SkillLoader:
    """
    Dynamic loader for skill files.

    Loads Python files from skill directories and extracts
    BaseSkill subclasses.
    """

    DEFAULT_SKILL_DIRS = [
        Path.home() / ".opennova" / "skills",
        Path(".opennova") / "skills",
    ]

    @classmethod
    def discover_skills(
        cls,
        additional_dirs: list[str | Path] | None = None,
    ) -> list[Path]:
        """
        Discover skill files in standard directories.

        Args:
            additional_dirs: Extra directories to search

        Returns:
            List of Python files that may contain skills
        """
        skill_dirs = [Path(d) for d in cls.DEFAULT_SKILL_DIRS]

        if additional_dirs:
            skill_dirs.extend(Path(d) for d in additional_dirs)

        skill_files = []

        for skill_dir in skill_dirs:
            if skill_dir.exists() and skill_dir.is_dir():
                for py_file in skill_dir.glob("*.py"):
                    if not py_file.name.startswith("_"):
                        skill_files.append(py_file)

        return skill_files

    @classmethod
    def load_skill_file(
        cls,
        file_path: str | Path,
    ) -> list[LoadedSkill]:
        """
        Load skills from a Python file.

        Args:
            file_path: Path to the Python file

        Returns:
            List of LoadedSkill objects
        """
        path = Path(file_path)

        if not path.exists():
            return []

        module_name = f"opennova_skill_{path.stem}"

        if module_name in sys.modules:
            del sys.modules[module_name]

        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return []

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module

            spec.loader.exec_module(module)

        except Exception as e:
            return [
                LoadedSkill(
                    skill_class=BaseSkill,
                    source_path=str(path),
                    load_error=f"Failed to load module: {e}",
                )
            ]

        skills = []

        for attr_name in dir(module):
            attr = getattr(module, attr_name)

            if (
                isinstance(attr, type)
                and issubclass(attr, BaseSkill)
                and attr is not BaseSkill
            ):
                metadata = None

                try:
                    temp_instance = attr()
                    metadata = temp_instance.get_metadata()
                except Exception:
                    pass

                skills.append(
                    LoadedSkill(
                        skill_class=attr,
                        metadata=metadata,
                        source_path=str(path),
                    )
                )

        return skills

    @classmethod
    def load_all_skills(
        cls,
        additional_dirs: list[str | Path] | None = None,
    ) -> dict[str, LoadedSkill]:
        """
        Load all discovered skills.

        Args:
            additional_dirs: Extra directories to search

        Returns:
            Dict mapping skill names to LoadedSkill objects
        """
        skill_files = cls.discover_skills(additional_dirs)

        loaded_skills: dict[str, LoadedSkill] = {}

        for skill_file in skill_files:
            skills = cls.load_skill_file(skill_file)

            for skill in skills:
                if skill.load_error:
                    continue

                name = skill.metadata.name if skill.metadata else skill.skill_class.__name__

                if name not in loaded_skills:
                    loaded_skills[name] = skill

        return loaded_skills

    @classmethod
    def create_skill_template(cls, name: str, output_dir: str | Path) -> Path:
        """
        Create a template skill file.

        Args:
            name: Name for the skill
            output_dir: Directory to create the skill in

        Returns:
            Path to created file
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
        class_name = "".join(word.capitalize() for word in safe_name.split("_"))

        template = f'''"""
{class_name} Skill for OpenNova.

Description: A custom skill for [describe what it does].
"""

from opennova.skills.base import BaseSkill, SkillMetadata
from opennova.tools.base import ToolResult


class {class_name}Skill(BaseSkill):
    """Custom skill: {name}."""

    name = "{name}"
    description = "Description of what this skill does"

    metadata = SkillMetadata(
        name="{name}",
        version="0.1.0",
        description="Skill description",
        author="Your Name",
        tags=["custom"],
    )

    def execute(self, **kwargs) -> ToolResult:
        """
        Execute the skill.

        Args:
            **kwargs: Skill-specific parameters

        Returns:
            ToolResult with output
        """
        # Add your skill logic here
        
        return ToolResult(
            success=True,
            output="Skill executed successfully!",
        )
'''

        skill_file = output_path / f"{safe_name}.py"
        skill_file.write_text(template)

        return skill_file
