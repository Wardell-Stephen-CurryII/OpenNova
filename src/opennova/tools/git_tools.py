"""
Git Integration Tools - Version control operations.

Provides:
- GitCommit: Create commits with proper message formatting
- GitStatus: Show current repository status
- GitDiff: Show unstaged and staged changes
- GitBranch: List and manage branches
- GitLog: Show commit history
"""

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from opennova.tools.base import BaseTool, ToolResult


@dataclass
class GitStatusInfo:
    """Information about git repository status."""

    branch: str
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    has_changes: bool = False


class GitCommitTool(BaseTool):
    """Create a git commit following repository conventions."""

    name = "git_commit"
    description = "Create a git commit. Stage relevant files first using Bash(git add path/to/file), then commit with a descriptive message following this repository's commit message style."

    def execute(
        self,
        message: str | None = None,
        amend: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        """
        Create a git commit.

        Args:
            message: Commit message (if None, will analyze changes)
            amend: True to amend the last commit

        Returns:
            ToolResult with commit information
        """
        try:
            if message is None:
                # Analyze changes and draft a commit message
                message = self._generate_commit_message()

            # Build git commit command
            command = ["git", "commit", "-m", message]

            if amend:
                command.append("--amend")

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                return ToolResult(
                    success=False,
                    output="",
                    error=result.stderr or "Commit failed",
                )

            # Get commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )

            commit_hash = hash_result.stdout.strip()[:8]

            return ToolResult(
                success=True,
                output=f"Created commit {commit_hash}: {message[:50]}{'...' if len(message) > 50 else ''}",
                metadata={
                    "commit_hash": commit_hash,
                    "message": message,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def _generate_commit_message(self) -> str:
        """Generate a commit message based on current changes."""
        try:
            # Get git diff to understand changes
            diff_result = subprocess.run(
                ["git", "diff", "--cached", "--stat"],
                capture_output=True,
                text=True,
                check=True,
            )

            # Simple analysis of the diff
            changed_files = []
            for line in diff_result.stdout.split("\n"):
                if "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        file_path = parts[0].strip()
                        stats = parts[2].strip()
                        changed_files.append(f"{file_path} ({stats})")

            if not changed_files:
                return "Update changes"

            # Categorize the change type
            return f"Update code with changes to: {', '.join(changed_files[:3])}"

        except Exception:
            return "Update changes"


class GitStatusTool(BaseTool):
    """Show current git repository status."""

    name = "git_status"
    description = "Show the working tree status. Use this to see staged, unstaged, and untracked files in the repository."

    def execute(self, **kwargs: Any) -> ToolResult:
        """
        Show git status.

        Returns:
            ToolResult with status information
        """
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True,
                text=True,
                check=True,
            )

            status_info = self._parse_git_status(result.stdout)

            output_lines = [
                f"Branch: {status_info.branch}",
                "",
            ]

            if status_info.staged:
                output_lines.append("Staged changes:")
                for path in status_info.staged:
                    output_lines.append(f"  {path}")

            if status_info.unstaged:
                output_lines.append("Unstaged changes:")
                for path in status_info.unstaged:
                    output_lines.append(f"  {path}")

            if status_info.untracked:
                output_lines.append("Untracked files:")
                for path in status_info.untracked:
                    output_lines.append(f"  {path}")

            if not status_info.has_changes:
                output_lines.append("(No changes)")

            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={
                    "branch": status_info.branch,
                    "staged": status_info.staged,
                    "unstaged": status_info.unstaged,
                    "untracked": status_info.untracked,
                    "has_changes": status_info.has_changes,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    def _parse_git_status(self, output: str) -> GitStatusInfo:
        """Parse git status output."""
        # Get current branch
        try:
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=True,
            )
            branch = branch_result.stdout.strip()
        except Exception:
            branch = "unknown"

        staged = []
        unstaged = []
        untracked = []
        has_changes = False

        for line in output.split("\n"):
            if not line:
                continue

            status_code = line[:2]
            path = line[3:]

            has_changes = True

            if status_code[0] in "M":  # Modified
                if status_code[1] == "M":  # Modified staged
                    staged.append(path)
                else:
                    unstaged.append(path)
            elif status_code[0] == "A":  # Added
                staged.append(path)
            elif status_code[0] == "D":  # Deleted
                unstaged.append(path)
            elif status_code[0] == "?":  # Untracked
                untracked.append(path)

        return GitStatusInfo(
            branch=branch,
            staged=staged,
            unstaged=unstaged,
            untracked=untracked,
            has_changes=has_changes,
        )


class GitDiffTool(BaseTool):
    """Show git diff for staged and unstaged changes."""

    name = "git_diff"
    description = "Show unstaged and staged changes. Use this to review what will be committed or examine the differences between revisions."

    def execute(
        self,
        cached: bool = False,
        **kwargs: Any,
    ) -> ToolResult:
        """
        Show git diff.

        Args:
            cached: True for staged changes only, False for unstaged

        Returns:
            ToolResult with diff output
        """
        try:
            command = ["git", "diff"]
            if cached:
                command.append("--cached")

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
            )

            # Limit output size
            output = result.stdout
            if len(output) > 10000:
                output = output[:10000] + "\n... (diff truncated)"

            return ToolResult(
                success=True,
                output=output or "No changes to show.",
                metadata={
                    "cached": cached,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class GitLogTool(BaseTool):
    """Show git commit history."""

    name = "git_log"
    description = "Show commit history. Use this to review recent changes, understand project evolution, or investigate when something was changed."

    def execute(
        self,
        max_count: int = 10,
        **kwargs: Any,
    ) -> ToolResult:
        """
        Show git log.

        Args:
            max_count: Maximum number of commits to show

        Returns:
            ToolResult with log output
        """
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", f"-{max_count}"],
                capture_output=True,
                text=True,
                check=True,
            )

            if not result.stdout.strip():
                return ToolResult(
                    success=True,
                    output="No commits in history.",
                    metadata={"commits": []},
                )

            commits = result.stdout.strip().split("\n")

            output_lines = ["Recent commits:", ""]
            for commit in commits:
                output_lines.append(f"  {commit}")

            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={
                    "commits": commits,
                    "count": len(commits),
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))


class GitBranchTool(BaseTool):
    """List and manage git branches."""

    name = "git_branch"
    description = "List all branches. Use this to see available branches or switch between different branches."

    def execute(
        self,
        **kwargs: Any,
    ) -> ToolResult:
        """
        List git branches.

        Returns:
            ToolResult with branch list
        """
        try:
            result = subprocess.run(
                ["git", "branch", "-a"],
                capture_output=True,
                text=True,
                check=True,
            )

            if not result.stdout.strip():
                return ToolResult(
                    success=True,
                    output="No branches found.",
                    metadata={"branches": []},
                )

            branches = result.stdout.strip().split("\n")

            output_lines = ["Branches:", ""]
            current_branch = ""
            for branch in branches:
                branch_clean = branch.replace("*", "").strip()
                if "*" in branch:
                    current_branch = branch_clean
                    output_lines.append(f"  * {branch_clean} (current)")
                else:
                    output_lines.append(f"    {branch_clean}")

            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={
                    "branches": [b.replace("*", "").strip() for b in branches],
                    "current": current_branch,
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
