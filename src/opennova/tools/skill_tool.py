"""Skill tool for invoking markdown skills through the tool system."""

from typing import Any

from opennova.tools.base import BaseTool, ToolResult


class SkillTool(BaseTool):
    """Invoke a markdown skill. Skills provide specialized capabilities and domain knowledge.

    When a skill matches the user's request, invoking this tool is a BLOCKING REQUIREMENT:
    call the skill BEFORE generating any other response about the task.

    Available skills are listed in system-reminder messages in the conversation.
    Do not invoke a skill that is already running.
    """

    name = "skill"
    description = (
        "Execute a skill within the main conversation. "
        "When users ask you to perform tasks, check if any of the available skills "
        "(listed in system-reminder messages) match. "
        "Skills provide specialized capabilities and domain knowledge.\n"
        "How to invoke:\n"
        "- Set 'skill' to the skill name (e.g. 'pdf', 'commit', 'review-pr')\n"
        "- Set 'args' to optional arguments for the skill\n"
        "When a skill matches the user's request, invoke the Skill tool "
        "BEFORE generating any other response about the task. "
        "Never mention a skill without actually calling this tool."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.runtime = self.config.get("runtime")

    def _get_available_skill_names(self) -> list[str]:
        if self.runtime is None:
            return []
        skill_registry = getattr(self.runtime, "skill_registry", None)
        if skill_registry is None:
            return []
        return skill_registry.list_model_invocable_skills()

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": (
                        "The skill name. E.g., 'commit', 'review-pr', or 'pdf'. "
                        "Available skills are listed in system-reminder messages."
                    ),
                },
                "args": {
                    "type": "string",
                    "description": "Optional arguments passed to the skill.",
                },
            },
            "required": ["skill"],
        }

    def execute(self, skill: str, args: str = "") -> ToolResult:
        if self.runtime is None:
            return ToolResult(success=False, output="", error="Skill tool is missing runtime context")
        return self.runtime.invoke_skill(skill_name=skill, skill_args=args, caller="model")
