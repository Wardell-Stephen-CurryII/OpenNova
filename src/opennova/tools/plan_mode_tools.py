"""
Plan Mode Tools - Enter and Exit Plan Mode tools.

Provides:
- EnterPlanMode: Transition to planning mode for implementation tasks
- ExitPlanMode: Signal planning complete and request user approval
- Plan file management for persistence
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from opennova.tools.base import BaseTool, ToolResult


class PlanFileFormat(str, Enum):
    """Format for plan file storage."""

    MARKDOWN = "markdown"
    JSON = "json"


@dataclass
class PlanStep:
    """A single step in a plan."""

    id: str
    description: str
    status: str = "pending"
    tool_hint: str | None = None
    result_summary: str | None = None
    error: str | None = None


@dataclass
class Plan:
    """A task plan with multiple steps."""

    task: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "planning"
    notes: str | None = None
    format: PlanFileFormat = PlanFileFormat.MARKDOWN

    def to_markdown(self) -> str:
        """Convert plan to markdown format."""
        lines = [
            f"# Plan: {self.task}",
            "",
            f"Created: {self.created_at.isoformat()}",
            f"Status: {self.status}",
            "",
        ]

        if self.notes:
            lines.extend(["## Notes", self.notes, ""])

        lines.append("## Steps")

        for step in self.steps:
            status_icon = {"pending": "○", "running": "⟳", "done": "✓", "failed": "✗"}.get(
                step.status, "?"
            )
            lines.append(f"- [{status_icon}] **{step.id}**: {step.description}")

            if step.tool_hint:
                lines.append(f"  Tool: {step.tool_hint}")

            if step.result_summary:
                lines.append(f"  Result: {step.result_summary}")

            if step.error:
                lines.append(f"  Error: {step.error}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Convert plan to dictionary."""
        return {
            "task": self.task,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "notes": self.notes,
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "status": s.status,
                    "tool_hint": s.tool_hint,
                    "result_summary": s.result_summary,
                    "error": s.error,
                }
                for s in self.steps
            ],
        }


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
   - Example: "Add a logout button" - where should it go? What should happen on click?
   - Example: "Add form validation" - what rules? What error messages?

2. **Multiple Valid Approaches**: The task can be solved in several different ways
   - Example: "Add caching to the API" - could use Redis, in-memory, file-based, etc.
   - Example: "Improve performance" - many optimization strategies possible

3. **Code Modifications**: Changes that affect existing behavior or structure
   - Example: "Update login flow" - what exactly should change?
   - Example: "Refactor this component" - what's the target architecture?

4. **Architectural Decisions**: The task requires choosing between patterns or technologies
   - Example: "Add real-time updates" - WebSockets vs SSE vs polling
   - Example: "Implement state management" - Redux vs Context vs custom solution

5. **Multi-File Changes**: The task will likely touch more than 2-3 files
   - Example: "Refactor authentication system"
   - Example: "Add a new API endpoint with tests"

6. **Unclear Requirements**: You need to explore before understanding full scope
   - Example: "Make the app faster" - need to profile and identify bottlenecks
   - Example: "Fix bug in checkout" - need to investigate root cause

7. **User Preferences Matter**: The implementation could reasonably go multiple ways
   - If you would use AskUserQuestion to clarify approach, use EnterPlanMode instead
   - Plan mode lets you explore first, then present options with context

## When NOT to Use This Tool

Only skip EnterPlanMode for simple tasks:
- Single-line or few-line fixes (typos, obvious bugs, small tweaks)
- Adding a single function with clear requirements
- Tasks where the user has given very specific, detailed instructions
- Pure research/exploration tasks (use Agent tool with explore agent instead)

## Examples

### GOOD - Use EnterPlanMode:
- "Add user authentication to the app" - Requires architectural decisions (session vs JWT, where to store tokens, middleware structure)
- "Optimize database queries" - Multiple approaches possible, need to profile first, significant impact
- "Implement dark mode" - Architectural decision on theme system, affects many components
- "Add a delete button to user profile" - Seems simple but involves: where to place it, confirmation dialog, API call, error handling, state updates
- "Update error handling in the API" - Affects multiple files, user should approve the approach

### BAD - Don't use EnterPlanMode:
- "Fix typo in the README" - Straightforward, no planning needed
- "Add a console.log to debug this function" - Simple, obvious implementation
- "What files handle routing?" - Research task, not implementation planning
- "Can we work on search feature?" - User wants to get started, not plan

## Important Notes

- This tool REQUIRES user approval - they must consent to entering plan mode
- If unsure whether to use it, err on the side of planning - it's better to get alignment upfront than to redo work
- Users appreciate being consulted before significant changes are made to their codebase
- Once in plan mode, explore thoroughly and create a detailed plan before calling ExitPlanMode
"""
            return ToolResult(
                success=True,
                output="Entered plan mode. Please explore the codebase and design your approach.",
                metadata={
                    "mode": "plan",
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
            instructions = """## How This Tool Works

- You should have already written your plan (it will be available in the system)
- This tool signals that you're done planning and ready for user review
- The user will see your plan and approve or request changes

## When to Use This Tool

IMPORTANT: Only use this tool when the task requires planning the implementation steps of a task that requires writing code. For research tasks where you're gathering information, searching files, reading files or in general trying to understand the codebase - do NOT use this tool.

## Before Using This Tool

Ensure your plan is complete and unambiguous:
- If you have unresolved questions about requirements or approach, use AskUserQuestion first
- Once your plan is finalized, use THIS tool to request approval

**Important:** Do NOT use AskUserQuestion to ask "Is this plan okay?" or "Should I proceed?" - that's exactly what THIS tool does. ExitPlanMode inherently requests user approval of your plan.

## Examples

1. **Research task**: "Search for and understand the implementation of vim mode in the codebase" - Do NOT use exit plan mode tool because you are not planning the implementation steps of a task.
2. **Implementation task**: "Help me implement yank mode for vim" - Use the exit plan mode tool after you have finished planning the implementation steps of the task.
3. **Implementation with uncertainty**: "Add a new feature to handle user authentication" - If unsure about auth method (OAuth, JWT, etc.), use AskUserQuestion first, then use exit plan mode tool after clarifying the approach.
"""
            return ToolResult(
                success=True,
                output="Plan mode exited. Awaiting user approval of the plan.",
                metadata={
                    "status": "awaiting_approval",
                    "instructions": instructions,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
