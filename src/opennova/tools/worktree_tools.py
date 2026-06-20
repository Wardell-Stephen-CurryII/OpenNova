"""Git worktree workflow tools."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from opennova.tools.base import BaseTool, ToolResult


def _run_git(command: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def _sanitize_branch_for_path(branch: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", branch).strip("-") or "worktree"


class EnterWorktreeTool(BaseTool):
    """Create an isolated git worktree for larger changes."""

    name = "enter_worktree"
    search_hint = "Create an isolated git worktree and branch for feature development"
    description = (
        "Create a git worktree for isolated development. "
        "By default this creates a new branch at the requested path."
    )

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.working_dir = str(self.config.get("working_dir", Path.cwd()))

    def execute(
        self,
        branch: str,
        path: str | None = None,
        base: str = "HEAD",
        create_branch: bool = True,
    ) -> ToolResult:
        if not branch.strip():
            return ToolResult(success=False, output="", error="branch must not be empty")

        root_result = _run_git(["git", "rev-parse", "--show-toplevel"], self.working_dir)
        if root_result.returncode != 0:
            return ToolResult(success=False, output="", error=root_result.stderr or "Not a git repository")

        repo_root = Path(root_result.stdout.strip()).resolve()
        target = Path(path).expanduser().resolve() if path else repo_root.parent / _sanitize_branch_for_path(branch)

        if target.exists():
            return ToolResult(success=False, output="", error=f"Worktree path already exists: {target}")

        command = ["git", "worktree", "add"]
        if create_branch:
            command.extend(["-b", branch])
        command.extend([str(target), base])

        result = _run_git(command, str(repo_root))
        if result.returncode != 0:
            return ToolResult(success=False, output=result.stdout, error=result.stderr or "git worktree add failed")

        return ToolResult(
            success=True,
            output=f"Created worktree at {target} on branch {branch}",
            metadata={
                "path": str(target),
                "branch": branch,
                "base": base,
                "created_branch": create_branch,
                "command": command,
            },
        )

    def is_destructive(self, **kwargs: Any) -> bool:
        return True

    def requires_permission(self, **kwargs: Any) -> bool:
        return True


class ExitWorktreeTool(BaseTool):
    """Remove an isolated git worktree when work is complete."""

    name = "exit_worktree"
    search_hint = "Remove an isolated git worktree after development"
    description = "Remove a git worktree path. This does not merge changes or delete the branch."

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.working_dir = str(self.config.get("working_dir", Path.cwd()))

    def execute(self, path: str, force: bool = False) -> ToolResult:
        target = Path(path).expanduser().resolve()
        if not str(target):
            return ToolResult(success=False, output="", error="path must not be empty")

        root_result = _run_git(["git", "rev-parse", "--show-toplevel"], self.working_dir)
        if root_result.returncode != 0:
            return ToolResult(success=False, output="", error=root_result.stderr or "Not a git repository")

        repo_root = Path(root_result.stdout.strip()).resolve()
        command = ["git", "worktree", "remove"]
        if force:
            command.append("--force")
        command.append(str(target))

        result = _run_git(command, str(repo_root))
        if result.returncode != 0:
            return ToolResult(success=False, output=result.stdout, error=result.stderr or "git worktree remove failed")

        return ToolResult(
            success=True,
            output=f"Removed worktree: {target}",
            metadata={"path": str(target), "force": force, "command": command},
        )

    def is_destructive(self, **kwargs: Any) -> bool:
        return True

    def requires_permission(self, **kwargs: Any) -> bool:
        return True
