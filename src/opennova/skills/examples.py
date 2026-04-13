"""
Example Skills for OpenNova.

These are sample skills that demonstrate how to create custom tools.
"""

from opennova.skills.base import BaseSkill, SkillMetadata
from opennova.tools.base import ToolResult


class CodeReviewSkill(BaseSkill):
    """Skill for reviewing code changes."""

    name = "code_review"
    description = "Review code and provide suggestions for improvement"

    metadata = SkillMetadata(
        name="code_review",
        version="1.0.0",
        description="Review code for quality, security, and best practices",
        author="OpenNova",
        tags=["code", "review", "quality"],
    )

    def execute(self, code: str, language: str = "python") -> ToolResult:
        """
        Review code for improvements.

        Args:
            code: Code to review
            language: Programming language

        Returns:
            ToolResult with review suggestions
        """
        suggestions = []

        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            if len(line) > 100 and not stripped.startswith("#"):
                suggestions.append(f"Line {i}: Consider breaking long line")

            if stripped.startswith("print(") and language == "python":
                suggestions.append(f"Line {i}: Consider using logging instead of print")

            if "TODO" in stripped or "FIXME" in stripped:
                suggestions.append(f"Line {i}: Found TODO/FIXME comment")

            if stripped.startswith("eval("):
                suggestions.append(f"Line {i}: eval() is dangerous, consider alternatives")

        if not suggestions:
            suggestions.append("No issues found - code looks good!")

        output = f"Code Review ({len(lines)} lines, {language}):\n\n"
        output += "\n".join(f"- {s}" for s in suggestions)

        return ToolResult(success=True, output=output)


class DocumentationSkill(BaseSkill):
    """Skill for generating documentation."""

    name = "generate_docs"
    description = "Generate documentation for Python code"

    metadata = SkillMetadata(
        name="generate_docs",
        version="1.0.0",
        description="Generate docstrings and documentation",
        author="OpenNova",
        tags=["docs", "documentation"],
    )

    def execute(self, function_name: str, args: str = "", description: str = "") -> ToolResult:
        """
        Generate documentation for a function.

        Args:
            function_name: Name of the function
            args: Comma-separated argument names
            description: Brief description of what it does

        Returns:
            ToolResult with generated docstring
        """
        docstring = f'"""{description or f"Execute the {function_name} function."}\n\n'

        args_list = [a.strip() for a in args.split(",") if a.strip()]

        if args_list:
            docstring += "Args:\n"
            for arg in args_list:
                docstring += f"    {arg}: Description for {arg}\n"
            docstring += "\n"

        docstring += 'Returns:\n    Description of return value\n"""\n'

        return ToolResult(success=True, output=docstring)


class GitHelperSkill(BaseSkill):
    """Skill for git operations assistance."""

    name = "git_helper"
    description = "Help with common git operations"

    metadata = SkillMetadata(
        name="git_helper",
        version="1.0.0",
        description="Assist with git commands and workflows",
        author="OpenNova",
        tags=["git", "version-control"],
    )

    def execute(self, operation: str, branch: str = "") -> ToolResult:
        """
        Get git command suggestions.

        Args:
            operation: Operation type (commit, push, branch, merge, etc.)
            branch: Branch name if applicable

        Returns:
            ToolResult with git commands
        """
        commands = {
            "commit": "git add . && git commit -m 'Your message'",
            "push": f"git push origin {branch or 'main'}",
            "pull": "git pull origin $(git branch --show-current)",
            "branch": f"git checkout -b {branch or 'new-branch'}",
            "status": "git status",
            "log": "git log --oneline -10",
            "diff": "git diff",
            "undo": "git reset --soft HEAD~1",
            "stash": "git stash",
            "unstash": "git stash pop",
        }

        operation_lower = operation.lower()

        if operation_lower in commands:
            cmd = commands[operation_lower]
            output = f"Git command for '{operation}':\n\n  {cmd}\n\n"
            output += "Make sure to review the command before executing."
            return ToolResult(success=True, output=output)

        suggestions = []
        for key, cmd in commands.items():
            if operation_lower in key or key in operation_lower:
                suggestions.append(f"- {key}: {cmd}")

        if suggestions:
            output = f"No exact match for '{operation}'. Similar operations:\n\n"
            output += "\n".join(suggestions)
        else:
            output = f"Unknown operation: {operation}\n\n"
            output += "Available operations:\n"
            output += ", ".join(commands.keys())

        return ToolResult(success=True, output=output)


class ProjectAnalyzerSkill(BaseSkill):
    """Skill for analyzing project structure."""

    name = "analyze_project"
    description = "Analyze project structure and provide insights"

    metadata = SkillMetadata(
        name="analyze_project",
        version="1.0.0",
        description="Analyze codebase structure and patterns",
        author="OpenNova",
        tags=["analysis", "project"],
    )

    def execute(self, project_path: str = ".") -> ToolResult:
        """
        Analyze project structure.

        Args:
            project_path: Path to project root

        Returns:
            ToolResult with analysis
        """
        from pathlib import Path

        path = Path(project_path)

        if not path.exists():
            return ToolResult(success=False, output="", error=f"Path does not exist: {project_path}")

        file_types: dict[str, int] = {}
        total_files = 0
        total_dirs = 0

        ignore_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}

        for item in path.rglob("*"):
            if any(part in ignore_dirs for part in item.parts):
                continue

            if item.is_file():
                total_files += 1
                ext = item.suffix.lower() or "no_extension"
                file_types[ext] = file_types.get(ext, 0) + 1
            elif item.is_dir():
                total_dirs += 1

        output = f"Project Analysis: {path.name}\n\n"
        output += f"Directories: {total_dirs}\n"
        output += f"Files: {total_files}\n\n"

        if file_types:
            output += "File Types:\n"
            sorted_types = sorted(file_types.items(), key=lambda x: x[1], reverse=True)
            for ext, count in sorted_types[:10]:
                display_ext = ext if ext != "no_extension" else "(no extension)"
                output += f"  {display_ext}: {count}\n"

        return ToolResult(success=True, output=output)


def get_builtin_skill_classes() -> list[type[BaseSkill]]:
    """Return the built-in skill classes shipped with OpenNova."""
    return [
        CodeReviewSkill,
        DocumentationSkill,
        GitHelperSkill,
        ProjectAnalyzerSkill,
    ]
