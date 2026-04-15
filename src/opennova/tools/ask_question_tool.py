"""
Ask User Question Tool - Interactive prompts during agent execution.

Provides:
- AskUserQuestion: Ask multiple-choice questions to gather information
- Support for preview content when options are selected
- Multi-select capability
- User annotations and notes
"""

from typing import Any

from opennova.tools.base import BaseTool, ToolResult


class AskUserQuestionTool(BaseTool):
    """Ask the user multiple choice questions to gather information."""

    name = "ask_user_question"
    description = "Ask the user multiple choice questions to gather information, clarify ambiguity, understand preferences, make decisions, or offer them choices. Use this when you need user input during execution."

    def execute(
        self,
        question: str,
        options: list[dict[str, Any]],
        header: str | None = None,
        multi_select: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        """Ask the user a multiple choice question."""
        try:
            if len(options) < 2 or len(options) > 4:
                return ToolResult(
                    success=False,
                    output="",
                    error="Questions must have 2-4 options.",
                )

            output_lines = [f"Question: {question}"]

            if header:
                output_lines.append(f"[{header}]")

            if multi_select:
                output_lines.append("(Select multiple options, comma-separated)")
            else:
                output_lines.append("(Select one option)")

            output_lines.append("")

            normalized_options = []
            for i, option in enumerate(options, 1):
                label = option.get("label", f"Option {i}")
                description = option.get("description", "")
                preview = option.get("preview")

                normalized_option = {
                    "index": i,
                    "label": label,
                    "description": description,
                }
                if preview:
                    normalized_option["preview"] = preview
                normalized_options.append(normalized_option)

                output_lines.append(f"  [{i}] {label}")
                if description:
                    output_lines.append(f"      {description}")
                if preview:
                    output_lines.append(
                        f"      Preview: {preview[:100]}{'...' if len(preview) > 100 else ''}"
                    )
                    output_lines.append("")

            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={
                    "interaction_required": True,
                    "interaction_type": "ask_user_question",
                    "question": question,
                    "questions": [
                        {
                            "question": question,
                            "header": header,
                            "options": options,
                            "multiSelect": multi_select,
                        }
                    ],
                    "prompt_payload": {
                        "question": question,
                        "header": header,
                        "options": normalized_options,
                        "multi_select": multi_select,
                    },
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
