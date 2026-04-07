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
        """
        Ask the user a multiple choice question.

        Args:
            question: The complete question to ask the user. Should be clear, specific,
                      and end with a question mark. Example:
                      "Which library should we use for date formatting?"
            options: The available choices for this question (2-4 options). Each option is a
                     dict with 'label', 'description', and optional 'preview' keys.
                     Example: [{"label": "moment.js", "description": "Lightweight library with good i18n"}]
            header: Very short label displayed as a chip/tag (max 12 chars).
                     Examples: "Auth method", "Library", "Approach".
            multi_select: Set to true to allow multiple selections instead of just one.

        Returns:
            ToolResult with user's answer
        """
        try:
            # In a real implementation with UI, this would show an interactive prompt.
            # For now, we simulate the interaction with a default behavior.

            if len(options) < 2 or len(options) > 4:
                return ToolResult(
                    success=False,
                    output="",
                    error="Questions must have 2-4 options.",
                )

            # Format the question for display
            output_lines = [f"Question: {question}"]

            if header:
                output_lines.append(f"[{header}]")

            if multi_select:
                output_lines.append("(Select multiple options, comma-separated)")
            else:
                output_lines.append("(Select one option)")

            output_lines.append("")

            # Display options
            for i, option in enumerate(options, 1):
                label = option.get("label", f"Option {i}")
                description = option.get("description", "")

                output_lines.append(f"  [{i}] {label}")

                if description:
                    output_lines.append(f"      {description}")

                preview = option.get("preview")
                if preview:
                    output_lines.append(f"      Preview: {preview[:100]}{'...' if len(preview) > 100 else ''}")
                    output_lines.append("")

            # For CLI/non-interactive mode, return a default response
            # In production, this would wait for actual user input
            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={
                    "questions": [
                        {
                            "question": question,
                            "header": header,
                            "options": options,
                            "multiSelect": multi_select,
                        }
                    ],
                    "answers": {},  # Would be populated by user input
                    "note": "This tool requires interactive UI for actual user responses. In CLI mode, please respond with your choice.",
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
