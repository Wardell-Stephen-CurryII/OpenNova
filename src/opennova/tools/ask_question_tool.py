"""
Ask User Question Tool - Interactive prompts during agent execution.

Provides:
- AskUserQuestion: Ask multiple-choice questions or free-text questions to gather information
- Support for preview content when options are selected
- Multi-select capability
- Free-text input when no options are provided
"""

from typing import Any

from opennova.tools.base import BaseTool, ToolResult


class AskUserQuestionTool(BaseTool):
    """Ask the user questions to gather information, clarify ambiguity, or make decisions.

    Supports two modes:
    - Choice mode: 2-4 options presented to the user
    - Free-text mode: no options, user types freely; skipping lets the model decide
    """

    name = "ask_user_question"
    description = (
        "Ask the user a question to gather information, clarify ambiguity, "
        "understand preferences, make decisions, or offer them choices. "
        "Use this when you need user input during execution.\n"
        "If you provide 2-4 options, the user picks from them. "
        "If you provide no options, the user can answer freely or skip to let you decide."
    )

    def execute(
        self,
        question: str,
        header: str | None = None,
        options: list[dict[str, Any]] | None = None,
        multi_select: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        """Ask the user a question.

        Args:
            question: The question to ask
            header: Optional header label for the dialog
            options: Optional list of option dicts with 'label' and optional 'description'
            multi_select: Whether multiple options can be selected (choice mode only)
        """
        try:
            options = options or []
            is_free_text = len(options) < 2

            output_lines = [f"Question: {question}"]
            if header:
                output_lines.append(f"[{header}]")

            normalized_options = []
            if is_free_text:
                output_lines.append("(Free-text answer, press Enter to skip)")
                output_lines.append("")
            else:
                if multi_select:
                    output_lines.append("(Select multiple options, comma-separated)")
                else:
                    output_lines.append("(Select one option)")
                output_lines.append("")

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
                            "options": options if not is_free_text else [],
                            "multiSelect": multi_select if not is_free_text else False,
                        }
                    ],
                    "prompt_payload": {
                        "question": question,
                        "header": header,
                        "options": normalized_options,
                        "multi_select": multi_select if not is_free_text else False,
                        "free_text": is_free_text,
                    },
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
