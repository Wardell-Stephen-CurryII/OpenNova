"""Skill tool for invoking markdown skills through the tool system."""

from typing import Any

from opennova.tools.base import BaseTool, ToolResult


class SkillTool(BaseTool):
    """Invoke a loaded markdown skill through a first-class tool path."""

    name = "skill"
    description = (
        "Invoke a reusable markdown skill when one matches the user's request. "
        "Use this instead of emitting literal /skill text."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.runtime = self.config.get("runtime")

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Name of the loaded skill to invoke.",
                },
                "args": {
                    "type": "string",
                    "description": "Optional string arguments passed to the skill prompt.",
                },
            },
            "required": ["skill"],
        }

    def execute(self, skill: str, args: str = "") -> ToolResult:
        if self.runtime is None:
            return ToolResult(success=False, output="", error="Skill tool is missing runtime context")
        return self.runtime.invoke_skill(skill_name=skill, skill_args=args, caller="model")
