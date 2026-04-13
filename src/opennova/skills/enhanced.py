"""Skills System - Enhanced skill management.

Provides:
- BaseSkill: Abstract base for all skills
- SkillMetadata: Metadata for skills
- SkillRegistry: Enhanced registry with search and templates
- Built-in skills: Common utility skills
"""

import json
import re
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
        description: What skill does
        author: Author name
        tags: Tags for categorization
        requires: Required dependencies
        enabled: Whether skill is enabled
    """

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class SkillTemplate:
    """
    Template for creating new skills.

    Provides:
    - File template with base structure
    - Example implementations
    - Metadata placeholders
    """

    name: str
    description: str
    author: str = "Your Name"

    def generate_file(self, output_dir: Path) -> Path:
        """Generate skill file from template.

        Args:
            output_dir: Directory to write file

        Returns:
            Path to generated file
        """
        safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in self.name)

        file_path = output_dir / f"{safe_name}.py"

        template = f'''"""
{self.name} Skill for OpenNova.

Description: {self.description}

Author: {self.author}
"""

from opennova.skills.base import BaseSkill
from opennova.tools.base import ToolResult


class {self.name}Skill(BaseSkill):
    \"\"\"
    \"\"\"

    name = "{self.name}"
    description = "{self.description}"

    metadata = SkillMetadata(
        name="{self.name}",
        version="0.1.0",
        description="{self.description}",
        author="{self.author}",
        tags=["custom"],
    )

    def execute(self, **kwargs: Any) -> ToolResult:
        \"\"\"
        Your skill logic here
        \"\"\"

        return ToolResult(
            success=True,
            output="{self.name} skill executed successfully!",
        )
    \"\"\"
'''

        file_path.write_text(template)

        return file_path


class SkillSearchQuery:
    """
    Query for skill search.

    Attributes:
        query: Search string
        tags: Filter by tags
        category: Filter by skill category
        enabled_only: Only show enabled skills
    """

    query: str = ""
    tags: list[str] | None = None
    category: str | None = None
    enabled_only: bool = False


class SkillRegistry:
    """
    Enhanced skill registry with search and templates.

    Features:
    - Load skills from directories
    - Register/unregister skills
    - Search skills by name, tags, category
    - Enable/disable skills
    - Get skill statistics
    - Create skill templates
    """

    DEFAULT_SKILL_DIRS = [
        Path.home() / ".opennova" / "skills",
        Path(".opennova") / "skills",
    ]

    def __init__(self, skill_dirs: list[str] | None = None):
        """
        Initialize skill registry.

        Args:
            skill_dirs: Custom directories to search
        """
        self.skill_dirs = skill_dirs or self.DEFAULT_SKILL_DIRS
        self.skills: dict[str, Any] = {}
        self._stats = {"total": 0, "enabled": 0, "disabled": 0}

    def discover_skills(self) -> dict[str, Any]:
        """
        Discover all Python skill files.

        Returns:
            Dict of skill metadata by name
        """
        discovered = {}

        for skill_dir in self.skill_dirs:
            if not skill_dir.exists():
                continue

            for py_file in skill_dir.glob("*.py"):
                # Skip private files (starting with _)
                if py_file.name.startswith("_"):
                    continue

                try:
                    with open(py_file, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Extract metadata from comments
                    metadata = self._extract_metadata(content, py_file.stem)

                    if metadata.get("name"):
                        skill_name = metadata["name"]
                        discovered[skill_name] = metadata

                except Exception:
                    pass

        # Update stats
        self._stats["total"] = len(discovered)
        self._stats["enabled"] = sum(1 for m in discovered.values() if m.get("enabled", True))
        self._stats["disabled"] = sum(1 for m in discovered.values() if not m.get("enabled", True))

        return discovered

    def _extract_metadata(self, content: str, stem: str) -> dict[str, Any]:
        """Extract metadata from skill file comments."""
        metadata = {
            "name": stem,
            "description": "",
            "author": "Your Name",
            "tags": [],
            "requires": [],
            "enabled": True,
        }

        for line in content.split("\n"):
            line = line.strip()
            if not line.startswith("#"):
                continue

            # Extract @dataclass decorators
            if line.startswith("@dataclass"):
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "class":
                    metadata["name"] = parts[1].strip().split("(")[0]

            # Extract description
            elif line.lower().startswith("description"):
                value = line.split("=", 1)[1].strip().strip('"')
                metadata["description"] = value

            # Extract author
            elif line.lower().startswith("author"):
                value = line.split("=", 1)[1].strip().strip('"')
                metadata["author"] = value

            # Extract tags
            elif line.lower().startswith("tags"):
                tags_str = line.split("=", 1)[1].strip().strip('"')
                tags_str = tags_str.strip("[]()")
                metadata["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]

            # Extract requires
            elif line.lower().startswith("requires"):
                requires_str = line.split("=", 1)[1].strip().strip('"')
                metadata["requires"] = [r.strip() for r in requires_str.split(",") if r.strip()]

            # Extract enabled
            elif line.lower().startswith("enabled"):
                value = line.split("=", 1)[1].strip().strip('"')
                metadata["enabled"] = value.lower() == "true"

        return metadata

    def load_skill(self, name: str) -> dict[str, Any] | None:
        """
        Load a skill by name.

        Args:
            name: Skill name

        Returns:
            Skill metadata or None if not found
        """
        skill_file = None

        for skill_dir in self.skill_dirs:
            potential_file = skill_dir / f"{name}.py"
            if potential_file.exists():
                skill_file = potential_file
                break

        if not skill_file:
            return None

        try:
            with open(skill_file, "r", encoding="utf-8") as f:
                content = f.read()

            metadata = self._extract_metadata(content, name)

            # Load skill as module
            return {
                "metadata": metadata,
                "file_path": str(skill_file),
                "load_error": None,
            }

        except Exception as e:
            return {
                "metadata": None,
                "file_path": None,
                "load_error": str(e),
            }

    def search(self, query: SkillSearchQuery) -> list[dict[str, Any]]:
        """
        Search skills by query, tags, and category.

        Args:
            query: Search query object

        Returns:
            List of matching skills
        """
        results = []
        query_lower = query.query.lower()

        for name, metadata in self.skills.items():
            # Check if enabled (if query requires enabled_only)
            if query.enabled_only and not metadata.get("enabled", True):
                continue

            # Check name match
            if query_lower and query_lower in name.lower():
                results.append(metadata)

            # Check tag match
            if query.tags:
                skill_tags_lower = [tag.lower() for tag in metadata.get("tags", [])]
                if any(tag in skill_tags_lower for tag in query.tags):
                    results.append(metadata)

            # Check category match
            if query.category and metadata.get("category") == query.category:
                results.append(metadata)

        return results

    def create_skill_template(self, name: str, output_dir: Path) -> Path:
        """
        Create a skill template file.

        Args:
            name: Name of the skill
            output_dir: Directory to write to

        Returns:
            Path to created template file
        """
        template = SkillTemplate(
            name=name,
            description="Description of your skill",
            author="Your Name",
        )

        return template.generate_file(output_dir)

    def get_stats(self) -> dict[str, Any]:
        """Get skill statistics."""
        return self._stats

    def list_all(self) -> dict[str, Any]:
        """List all skills."""
        return self.skills

    def __contains__(self, name: str) -> bool:
        """Check if skill exists."""
        return name in self.skills
