"""Side-effect-free catalog for globally shipped OpenNova tools."""

BUILTIN_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "write_file",
    "create_file",
    "edit_file",
    "multi_edit_file",
    "delete_file",
    "list_directory",
    "execute_command",
    "glob_files",
    "grep_code",
    "python_diagnostics",
    "python_symbols",
    "python_definition",
    "python_references",
    "task_create",
    "task_list",
    "task_get",
    "task_update",
    "task_stop",
    "task_output",
    "todo_write",
    "agent",
    "send_message",
    "ask_user_question",
    "skill",
    "enter_plan_mode",
    "exit_plan_mode",
    "web_search",
    "web_fetch",
    "init_project_guide",
    "list_mcp_resources",
    "read_mcp_resource",
    "git_commit",
    "git_status",
    "git_diff",
    "git_log",
    "git_branch",
    "enter_worktree",
    "exit_worktree",
)


def builtin_tool_names() -> list[str]:
    """Return a defensive copy suitable for inspection commands."""
    return list(BUILTIN_TOOL_NAMES)
