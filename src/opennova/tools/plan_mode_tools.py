"""
Plan Mode Tools - Enter and Exit Plan Mode tools.

Provides:
- EnterPlanMode: Describe and track entry into planning mode for implementation tasks
- ExitPlanMode: Signal planning complete and request user approval
- Metadata aligned with runtime plan state and saved plan files
"""

from typing import Any

from opennova.runtime.state import AgentState
from opennova.tools.base import BaseTool, ToolResult


class EnterPlanModeTool(BaseTool):
    """Enter plan mode for planning implementation tasks."""

    name = "enter_plan_mode"
    description = "Enter plan mode to explore the codebase and design an implementation approach before writing code. Use this proactively for non-trivial implementation tasks where getting user approval on your approach before coding prevents wasted effort and ensures alignment."

    def execute(self, **kwargs: Any) -> ToolResult:
        """
        Enter plan mode.

        Returns:
            ToolResult with plan mode instructions
        """
        try:
            state = self.config.get("state")
            if isinstance(state, AgentState):
                state.set_mode("plan")

            instructions = """## What Happens in Plan Mode

In plan mode, you'll:
1. Thoroughly explore the codebase using Glob, Grep, and Read tools
2. Understand existing patterns and architecture
3. Design an implementation approach
4. Present your plan to the user for approval
5. Use AskUserQuestion if you need to clarify approaches
6. Exit plan mode with ExitPlanMode when ready to implement

## When to Use This Tool

**Prefer using EnterPlanMode** for implementation tasks unless they're simple. Use it when ANY of these conditions apply:

1. **New Feature Implementation**: Adding meaningful new functionality
2. **Multiple Valid Approaches**: The task can be solved in several different ways
3. **Code Modifications**: Changes that affect existing behavior or structure
4. **Architectural Decisions**: The task requires choosing between patterns or technologies
5. **Multi-File Changes**: The task will likely touch more than 2-3 files
6. **Unclear Requirements**: You need to explore before understanding full scope
7. **User Preferences Matter**: The implementation could reasonably go multiple ways

## Important Notes

- This tool REQUIRES user approval before implementation
- Once in plan mode, explore thoroughly and create a detailed plan before calling ExitPlanMode
"""
            return ToolResult(
                success=True,
                output="Entered plan mode. Please explore the codebase and design your approach.",
                metadata={
                    "mode": "plan",
                    "current_mode": state.mode if isinstance(state, AgentState) else "plan",
                    "has_plan": bool(state.current_plan) if isinstance(state, AgentState) else False,
                    "plan_file_path": str(state.plan_file_path) if isinstance(state, AgentState) and state.plan_file_path else None,
                    "instructions": instructions,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class ExitPlanModeTool(BaseTool):
    """Exit plan mode and request user approval for implementation."""

    name = "exit_plan_mode"
    description = "Use this tool when you are in plan mode and have finished writing your plan and are ready for user approval. This tool signals that you're done planning and ready for the user to review and approve your plan."

    def execute(self, **kwargs: Any) -> ToolResult:
        """
        Exit plan mode and request approval.

        Returns:
            ToolResult with approval status
        """
        try:
            state = self.config.get("state")
            if isinstance(state, AgentState):
                state.requires_confirmation = True

            instructions = """## How This Tool Works

- You should have already written your plan (it will be available in the system)
- This tool signals that you're done planning and ready for user review
- The user will see your plan and approve or request changes

## Before Using This Tool

Ensure your plan is complete and unambiguous:
- If you have unresolved questions about requirements or approach, use AskUserQuestion first
- Once your plan is finalized, use THIS tool to request approval
"""
            return ToolResult(
                success=True,
                output="Plan mode exited. Awaiting user approval of the plan.",
                metadata={
                    "status": "awaiting_approval",
                    "mode": state.mode if isinstance(state, AgentState) else "plan",
                    "has_plan": bool(state.current_plan) if isinstance(state, AgentState) else False,
                    "plan_file_path": str(state.plan_file_path) if isinstance(state, AgentState) and state.plan_file_path else None,
                    "requires_confirmation": state.requires_confirmation if isinstance(state, AgentState) else True,
                    "instructions": instructions,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
