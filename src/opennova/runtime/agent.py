"""
Agent Runtime - Main orchestrator for OpenNova agent.

Manages the agent lifecycle:
- Initialization with configuration
- Mode switching (plan/act)
- Tool registration
- ReAct loop coordination
- MCP server connections
- Skill loading
"""

import copy
import os
import re
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from opennova.hooks import HookManager
from opennova.memory.context import ContextManager
from opennova.memory.project import ProjectMemory
from opennova.memory.working import WorkingMemory
from opennova.planning.planner import Planner
from opennova.plugins import PluginManager
from opennova.providers.base import Message, StreamChunk
from opennova.providers.factory import ProviderFactory
from opennova.runtime.events import ToolEvent
from opennova.runtime.loop import ReActLoop
from opennova.runtime.state import (
    AgentState,
    Plan,
    PlanApprovalStatus,
    PlanStatus,
    PlanStep,
)
from opennova.tasks import TaskManager
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult


class AgentRuntime:
    """
    Main Agent Runtime class.

    Orchestrates all components:
    - LLM Provider
    - Tool Registry
    - State Management
    - ReAct Loop
    - MCP Connections
    - Skills
    """

    def __init__(
        self,
        config: dict[str, Any],
        register_default_tools: bool = True,
        enable_mcp: bool = True,
        enable_skills: bool = True,
    ):
        """
        Initialize the agent runtime.

        Args:
            config: Configuration dictionary with providers and agent settings
            register_default_tools: Whether to register built-in tools
            enable_mcp: Whether to enable MCP server connections
            enable_skills: Whether to load skills
        """
        self.config = config
        self.state = AgentState()
        self.tool_registry = ToolRegistry()
        self.task_manager = TaskManager()
        self.register_default_tools = register_default_tools
        self.enable_mcp = enable_mcp
        self.enable_skills = enable_skills

        agent_config = config.get("agent", {})
        self.max_iterations = agent_config.get("max_iterations", 20)
        self.show_thinking = agent_config.get("show_thinking", True)
        self.auto_confirm = agent_config.get("auto_confirm", False)
        self.security_config = config.get("security", {})

        self.llm = ProviderFactory.create_provider(config)
        self.project_memory = ProjectMemory(project_path=os.getcwd())
        self.working_memory = WorkingMemory()
        self.hook_manager = HookManager(project_path=os.getcwd())
        self.hook_manager.load_project_hooks()
        self.plugin_manager = PluginManager(project_path=os.getcwd())
        self.plugin_manager.load_enabled_plugins(config=self.config, hook_manager=self.hook_manager)

        # Read compression config
        compression_config = agent_config.get("compression", {})
        self.context_manager = ContextManager(
            model=self.llm.model,
            max_tool_result_tokens=compression_config.get("max_tool_result_tokens", 8000),
        )
        self.context_manager.compression_threshold = compression_config.get(
            "threshold", 0.55
        )
        self.context_manager.keep_last_pairs = compression_config.get(
            "keep_last_pairs", 6
        )

        # Wire up context compressor
        from opennova.memory.compressor import ContextCompressor
        self.context_manager.set_compressor(ContextCompressor(llm_provider=self.llm))

        from opennova.session import SessionManager
        self.session_manager = SessionManager(project_path=os.getcwd())
        self.session_manager.start_session()
        self.session_transcript: list[dict[str, Any]] = []

        self.loop: ReActLoop | None = None
        self._callbacks: dict[str, Callable] = {}
        self.tool_events: list[dict[str, Any]] = []
        self.planner = Planner(self.llm)

        self.mcp_manager = None
        self._mcp_server_configs = []
        self.skill_registry = None
        from opennova.security.audit import SecurityAuditLogger
        from opennova.security.guardrails import Guardrails
        from opennova.security.permissions import PermissionStore

        self.permission_store = PermissionStore(Path(os.getcwd()) / ".opennova" / "permissions.json")
        audit_config = self.security_config.get("audit", {})
        self.security_audit_logger = SecurityAuditLogger(
            path=audit_config.get("path", ".opennova/audit/security.jsonl"),
            enabled=audit_config.get("enabled", True),
            max_arg_chars=audit_config.get("max_arg_chars", 500),
            session_id=self.session_manager.session_id,
            secrets_policy=self.security_config.get("secrets", {}),
        )
        self.guardrails = Guardrails(
            sandbox_mode=self.security_config.get("sandbox_mode", True),
            allowed_paths=self.security_config.get("allowed_paths", []),
            blocked_commands=self.security_config.get("blocked_commands", []),
            auto_confirm_safe=self.security_config.get("auto_confirm_safe", True),
            allow_network=self.security_config.get("allow_network", True),
            strict_shell_parsing=self.security_config.get("strict_shell_parsing", False),
            permission_mode=self.security_config.get("permission_mode", "default"),
            always_allow_tools=self.security_config.get("always_allow_tools", []),
            always_deny_tools=self.security_config.get("always_deny_tools", []),
            always_ask_tools=self.security_config.get("always_ask_tools", []),
            permission_rules=self.security_config.get("permission_rules", []),
            network_policy=self.security_config.get("network", {}),
            secrets_policy=self.security_config.get("secrets", {}),
            permission_store=self.permission_store,
        )

        # Set global task manager for task tools
        from opennova.tools.task_tools import set_global_task_manager
        set_global_task_manager(self.task_manager)

        if register_default_tools:
            self._register_builtin_tools()
            self._register_plugin_tools()

        if enable_skills:
            self._init_skills()

        if enable_mcp:
            self._init_mcp()

    def _register_builtin_tools(self) -> None:
        """Register all built-in tools."""
        security_config = self.config.get("security", {})
        tool_config = {
            "command_timeout": security_config.get("command_timeout", 30),
            "working_dir": os.getcwd(),
            "sandbox_mode": security_config.get("sandbox_mode", True),
            "allowed_paths": security_config.get("allowed_paths", []),
            "blocked_commands": security_config.get("blocked_commands", []),
            "auto_confirm_safe": security_config.get("auto_confirm_safe", True),
            "allow_network": security_config.get("allow_network", True),
            "strict_shell_parsing": security_config.get("strict_shell_parsing", False),
            "permission_mode": security_config.get("permission_mode", "default"),
            "always_allow_tools": security_config.get("always_allow_tools", []),
            "always_deny_tools": security_config.get("always_deny_tools", []),
            "always_ask_tools": security_config.get("always_ask_tools", []),
            "permission_rules": security_config.get("permission_rules", []),
            "network_policy": security_config.get("network", {}),
            "secrets_policy": security_config.get("secrets", {}),
            "process_sandbox": security_config.get("process_sandbox", {}),
            "temp_dir": security_config.get("process_sandbox", {}).get("tmp_dir"),
            "read_only": security_config.get("read_only", False),
            "max_file_size": security_config.get("max_file_size", 100 * 1024 * 1024),
        }

        from opennova.tools.agent_tools import AgentTool, SendMessageTool
        from opennova.tools.ask_question_tool import AskUserQuestionTool
        from opennova.tools.diagnostics_tools import (
            PythonDefinitionTool,
            PythonDiagnosticsTool,
            PythonReferencesTool,
            PythonSymbolsTool,
        )
        from opennova.tools.file_tools import (
            CreateFileTool,
            DeleteFileTool,
            EditFileTool,
            ListDirectoryTool,
            MultiEditFileTool,
            ReadFileTool,
            WriteFileTool,
        )
        from opennova.tools.git_tools import (
            GitBranchTool,
            GitCommitTool,
            GitDiffTool,
            GitLogTool,
            GitStatusTool,
        )
        from opennova.tools.mcp_resource_tools import ListMCPResourcesTool, ReadMCPResourceTool
        from opennova.tools.plan_mode_tools import (
            EnterPlanModeTool,
            ExitPlanModeTool,
        )
        from opennova.tools.project_guide_tool import InitProjectGuideTool
        from opennova.tools.search_tools import GlobFilesTool, GrepCodeTool
        from opennova.tools.shell_tools import ExecuteCommandTool
        from opennova.tools.skill_tool import SkillTool
        from opennova.tools.task_tools import (
            TaskCreateTool,
            TaskGetTool,
            TaskListTool,
            TaskOutputTool,
            TaskStopTool,
            TaskUpdateTool,
        )
        from opennova.tools.todo_tools import TodoWriteTool
        from opennova.tools.web_tools import WebFetchTool, WebSearchTool
        from opennova.tools.worktree_tools import EnterWorktreeTool, ExitWorktreeTool

        # File and shell tools
        self.tool_registry.register(ReadFileTool(config=tool_config))
        self.tool_registry.register(WriteFileTool(config=tool_config))
        self.tool_registry.register(CreateFileTool(config=tool_config))
        self.tool_registry.register(EditFileTool(config=tool_config))
        self.tool_registry.register(MultiEditFileTool(config=tool_config))
        self.tool_registry.register(DeleteFileTool(config=tool_config))
        self.tool_registry.register(ListDirectoryTool(config=tool_config))
        self.tool_registry.register(ExecuteCommandTool(config=tool_config))
        self.tool_registry.register(GlobFilesTool(config=tool_config))
        self.tool_registry.register(GrepCodeTool(config=tool_config))
        self.tool_registry.register(PythonDiagnosticsTool(config=tool_config))
        self.tool_registry.register(PythonSymbolsTool(config=tool_config))
        self.tool_registry.register(PythonDefinitionTool(config=tool_config))
        self.tool_registry.register(PythonReferencesTool(config=tool_config))

        # Task management tools (Claude Code-style)
        self.tool_registry.register(TaskCreateTool())
        self.tool_registry.register(TaskListTool())
        self.tool_registry.register(TaskGetTool())
        self.tool_registry.register(TaskUpdateTool())
        self.tool_registry.register(TaskStopTool())
        self.tool_registry.register(TaskOutputTool())
        self.tool_registry.register(TodoWriteTool())

        # Agent tools (Claude Code-style)
        self.tool_registry.register(AgentTool(config={"runtime": self}))
        self.tool_registry.register(SendMessageTool())

        # User interaction tools
        self.tool_registry.register(AskUserQuestionTool())
        self.tool_registry.register(SkillTool(config={"runtime": self}))

        # Plan mode tools
        self.tool_registry.register(EnterPlanModeTool(config={"state": self.state, "runtime": self}))
        self.tool_registry.register(ExitPlanModeTool(config={"state": self.state, "runtime": self}))

        # Web tools
        self.tool_registry.register(WebSearchTool(config=tool_config))
        self.tool_registry.register(WebFetchTool(config=tool_config))
        self.tool_registry.register(
            InitProjectGuideTool(config={"working_dir": os.getcwd(), "runtime": self})
        )
        self.tool_registry.register(ListMCPResourcesTool(config={"runtime": self}))
        self.tool_registry.register(ReadMCPResourceTool(config={"runtime": self}))

        # Git tools
        self.tool_registry.register(GitCommitTool())
        self.tool_registry.register(GitStatusTool())
        self.tool_registry.register(GitDiffTool())
        self.tool_registry.register(GitLogTool())
        self.tool_registry.register(GitBranchTool())
        self.tool_registry.register(EnterWorktreeTool(config=tool_config))
        self.tool_registry.register(ExitWorktreeTool(config=tool_config))

    def _register_plugin_tools(self) -> None:
        """Register trusted project plugin tools."""
        security_config = self.config.get("security", {})
        tool_config = {
            "working_dir": os.getcwd(),
            "allowed_paths": security_config.get("allowed_paths", []),
        }
        for tool in self.plugin_manager.build_tools(config=tool_config):
            self.tool_registry.register(tool)

    def _init_skills(self) -> None:
        """Initialize markdown skill loading."""
        from opennova.skills.examples import get_builtin_skill_dirs
        from opennova.skills.registry import SkillRegistry

        skills_config = self.config.get("skills", {})
        if not skills_config.get("enabled", True):
            return

        self.skill_registry = SkillRegistry()

        configured_dirs = [Path(path) for path in skills_config.get("dirs", [])]
        skill_dirs = [*get_builtin_skill_dirs(), *configured_dirs]
        excluded = skills_config.get("exclude", [])

        self.skill_registry.load_all(
            directories=skill_dirs,
            sources=self.plugin_manager.get_skill_sources(),
            excluded=excluded,
        )

    def _init_mcp(self) -> None:
        """Initialize MCP server connections."""
        from opennova.mcp.connector import MCPManager
        from opennova.mcp.types import MCPServerConfig

        mcp_config = self.config.get("mcp", {})
        if not mcp_config.get("enabled", True):
            return

        self.mcp_manager = MCPManager(self.tool_registry)
        self._mcp_server_configs: list[MCPServerConfig] = []
        self._mcp_config_errors: dict[str, str] = {}
        self._mcp_connection_results: dict[str, bool] = {}

        servers = mcp_config.get("servers", [])
        for index, server_data in enumerate(servers):
            server_name = server_data.get("name", f"server[{index}]")
            try:
                server_config = MCPServerConfig.from_dict(server_data)
                self._mcp_server_configs.append(server_config)
            except Exception as exc:
                self._mcp_config_errors[server_name] = str(exc)

    async def connect_mcp_servers(self) -> dict[str, bool]:
        """
        Connect to all configured MCP servers.

        Returns:
            Dict of server names to connection status
        """
        if not self.mcp_manager or not self._mcp_server_configs:
            return {}

        self._mcp_connection_results = await self.mcp_manager.connect_all(self._mcp_server_configs)
        return self._mcp_connection_results

    async def _ensure_mcp_ready(self) -> dict[str, bool]:
        """Lazily connect configured MCP servers before act-mode execution."""
        mcp_manager = getattr(self, "mcp_manager", None)
        mcp_server_configs = getattr(self, "_mcp_server_configs", [])
        if not mcp_manager or not mcp_server_configs:
            return {}

        connected_servers = set(mcp_manager.get_server_names())
        enabled_configs = [config for config in mcp_server_configs if config.enabled]
        if enabled_configs and all(config.name in connected_servers for config in enabled_configs):
            return {config.name: True for config in enabled_configs}

        return await self.connect_mcp_servers()

    async def disconnect_mcp_servers(self) -> None:
        """Disconnect from all MCP servers."""
        if self.mcp_manager:
            await self.mcp_manager.disconnect_all()

    def create_child_runtime(self) -> "AgentRuntime":
        """Create a child runtime that inherits this runtime's configuration."""
        child = AgentRuntime(
            config=copy.deepcopy(self.config),
            register_default_tools=self.register_default_tools,
            enable_mcp=self.enable_mcp,
            enable_skills=self.enable_skills,
        )
        child.auto_confirm = self.auto_confirm
        return child

    def register_tool(self, tool: BaseTool) -> None:
        """
        Register a custom tool.

        Args:
            tool: Tool instance to register
        """
        self.tool_registry.register(tool)

    def register_callback(self, event: str, callback: Callable) -> None:
        """
        Register an event callback.

        Args:
            event: Event name ('thought', 'action', 'result', 'stream')
            callback: Callback function
        """
        self._callbacks[event] = callback

    def _emit(self, event: str, *args, **kwargs) -> None:
        """Emit an event to registered callback."""
        if event in self._callbacks:
            self._callbacks[event](*args, **kwargs)

    async def run(
        self,
        task: str,
        mode: str = "act",
        stream: bool = True,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """
        Run the agent on a task.

        Args:
            task: Task description
            mode: Operating mode ('plan' or 'act')
            stream: Whether to stream output

        Returns:
            Final result string
        """
        if mode != "plan" and self._is_plan_execution_approval(task):
            self.state.mark_plan_approved()
            return await self.execute_approved_plan(stream=stream)

        self.state.reset(task)
        self.state.set_mode(mode)

        if mode == "plan":
            return await self._run_plan_mode(task, stream=stream)
        else:
            return await self._run_act_mode(task, stream=stream, progress_callback=progress_callback)

    async def _run_plan_mode(self, task: str, stream: bool = True) -> str:
        """
        Run in plan mode: generate a reviewable plan artifact and stop for approval.

        Args:
            task: Task description
            stream: Whether to stream output

        Returns:
            Final result string
        """
        plan = await self._create_plan(task)
        result = self._prepare_plan_for_approval(plan)
        self._save_session_messages()
        return result

    def _prepare_plan_for_approval(self, plan: Plan) -> str:
        """Persist plan state and return an approval-gated response."""
        self.state.set_plan(plan)
        plan_file_path = self._save_plan_to_project(plan)
        self.state.set_plan_file_path(plan_file_path)
        self.state.mark_plan_awaiting_approval()
        self._sync_plan_progress(plan)

        self._emit("plan", plan, plan_file_path)
        return "Plan ready for approval"

    def _build_step_execution_task(self, plan: Plan, step: PlanStep) -> str:
        """Build an act-mode task prompt for an approved plan step."""
        lines = [
            "Execute the approved development plan step.",
            f"Overall plan: {plan.task}",
            f"Current step ({step.id}): {step.description}",
            f"Plan file: {self.state.plan_file_path or '(not saved)'}",
            "",
            "Complete plan snapshot:",
            self._render_plan_snapshot(plan),
            "",
            "Follow the current approved plan exactly.",
            "Do not re-plan from scratch.",
            "If execution reveals the plan is stale or incorrect, update the plan status/notes first and then continue.",
        ]

        return "\n".join(lines)

    def _build_memory_messages(self, task: str) -> list[Message]:
        """Build compact memory context messages for an act-mode run."""
        memory_parts = [self.project_memory.get_project_context()]
        relevant_decisions = self.project_memory.get_relevant_decisions(task, limit=3)

        if relevant_decisions:
            decision_lines = [
                f"- {decision.description}: {decision.reasoning}"
                for decision in relevant_decisions
            ]
            memory_parts.append("Relevant prior decisions:\n" + "\n".join(decision_lines))

        try:
            from opennova.memory.layered import LayeredMemoryManager
            from opennova.memory.project_guide import ProjectGuideManager

            project_path = getattr(self.project_memory, "project_path", Path(os.getcwd()))
            guide_manager = ProjectGuideManager(project_path=project_path)
            guide_text = guide_manager.load_for_context(max_chars=5000)
            exclude_hashes = set()
            if guide_text:
                exclude_hashes.add(LayeredMemoryManager.content_hash(guide_text))
                memory_parts.append(
                    "Project guide (OPENNOVA.md) — follow these project-specific conventions when relevant:\n"
                    + guide_text
                )
            layered_text = LayeredMemoryManager(project_path=project_path).load_for_context(
                max_chars=5000,
                exclude_hashes=exclude_hashes,
            )
            if layered_text:
                memory_parts.append(
                    "Layered project memory (.opennova/memory) — additional maintained project notes:\n"
                    + layered_text
                )
        except Exception:
            pass

        memory_text = "\n\n".join(part for part in memory_parts if part)
        if not memory_text.strip():
            return []

        return [
            Message(
                role="system",
                content="Use this project memory when it is relevant to the current task:\n\n"
                + memory_text,
            )
        ]

    def _record_run_session(self, task: str, success: bool, started_at: float) -> None:
        """Persist a lightweight session summary for the completed run."""
        self.project_memory.record_session(
            task=task,
            success=success,
            duration_seconds=max(0.0, perf_counter() - started_at),
        )

    async def execute_approved_plan(self, stream: bool = True) -> str:
        """Execute the current approved plan step by step."""
        plan = self.state.current_plan
        if not plan:
            return "No plan available for execution"

        if self.state.plan_approval_status not in {
            PlanApprovalStatus.APPROVED,
            PlanApprovalStatus.EXECUTING,
        }:
            return "Plan approval required before execution"

        self.state.mark_plan_executing()
        plan.status = PlanStatus.EXECUTING
        self._sync_plan_progress(plan)
        self._emit_plan_update(plan)
        self._persist_current_plan()

        while True:
            if self.state.is_complete:
                break

            refreshed_plan = self._refresh_plan_from_file()
            if refreshed_plan is not None:
                plan = refreshed_plan

            step = plan.get_next_step()
            if not step:
                break

            plan.mark_step_running(step.id)
            self._sync_plan_progress(plan, active_step_id=step.id)
            self._emit_plan_update(plan)
            self._persist_current_plan()
            self._emit("thought", f"Executing plan step {step.id}: {step.description}")

            step_task = self._build_step_execution_task(plan, step)
            result = await self._run_act_mode(
                step_task,
                stream=stream,
                preserve_plan_state=True,
            )

            if not result:
                plan.mark_step_failed(step.id, "No result returned")
                self.state.mark_plan_failed()
                self._sync_plan_progress(plan)
                self._emit_plan_update(plan)
                self._persist_current_plan()
                return self.state.last_result or "Plan execution complete"

            if result.startswith("Task incomplete:") or result.startswith("Task failed:"):
                plan.mark_step_failed(step.id, result)
                self.state.mark_plan_failed()
                self._sync_plan_progress(plan)
                self._emit_plan_update(plan)
                self._persist_current_plan()
                if not self._should_continue_on_failure():
                    return self.state.last_result or result
                continue

            plan.mark_step_done(step.id, result)
            self._sync_plan_progress(plan)
            self._emit_plan_update(plan)
            self._persist_current_plan()

        final_result = self.state.last_result or "Plan execution complete"
        if plan.status == PlanStatus.DONE:
            self._sync_plan_progress(plan)
            self._emit_plan_update(plan)
            self.state.clear_plan_state()
        elif plan.status == PlanStatus.FAILED:
            self._sync_plan_progress(plan)
            self.state.mark_plan_failed()
            self._emit_plan_update(plan)

        return final_result

    def _is_plan_execution_approval(self, text: str) -> bool:
        """Return whether user text should approve and execute the current plan."""
        if not self.state.current_plan:
            return False
        if self.state.plan_approval_status not in {
            PlanApprovalStatus.AWAITING_APPROVAL,
            PlanApprovalStatus.APPROVED,
        }:
            return False
        normalized = text.strip().lower()
        if not normalized:
            return False
        approval_tokens = {
            "y",
            "yes",
            "approve",
            "approved",
            "execute",
            "run",
            "start",
            "go",
            "continue",
            "开始",
            "开始执行",
            "开始写代码",
            "执行",
            "执行计划",
            "继续",
            "继续执行",
            "同意",
            "批准",
        }
        if normalized in approval_tokens:
            return True
        return any(token in normalized for token in ("start coding", "execute plan", "开始写代码", "执行计划"))

    def _emit_plan_update(self, plan: Plan) -> None:
        """Notify UI/listeners that plan and mirrored todos changed."""
        with suppress(Exception):
            self._emit("plan", plan, self.state.plan_file_path)

    async def _create_plan(self, task: str) -> Plan:
        """
        Create a plan from a task.

        Args:
            task: Task description

        Returns:
            Generated Plan
        """
        plan = await self.planner.create_plan(task)
        return self.planner.optimize_plan(plan)

    def _save_plan_to_project(self, plan: Plan) -> Path:
        """Save a generated plan to the project-local .opennova/plan directory."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        plan_dir = Path(".opennova") / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plan_dir / f"plan_{timestamp}.md"
        plan_path.write_text(self._render_saved_plan(plan, plan_path), encoding="utf-8")
        return plan_path

    def _render_saved_plan(self, plan: Plan, plan_path: Path) -> str:
        """Render a generated plan into a readable markdown document."""
        summary = self._render_plan_snapshot(plan)
        lines = [
            f"# Saved Plan: {plan.task}",
            "",
            f"- Task: {self.state.current_task or plan.task}",
            f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
            f"- Saved path: {plan_path}",
            "",
            "## Summary",
            "",
            summary,
            "",
            "## Steps",
            "",
        ]

        for step in plan.steps:
            lines.append(f"### {step.id}")
            lines.append(f"- Description: {step.description}")
            if step.tool_hint:
                lines.append(f"- Tool hint: `{step.tool_hint}`")
            lines.append(f"- Status: `{step.status.value}`")
            if step.result_summary:
                lines.append(f"- Result: {step.result_summary}")
            if step.error:
                lines.append(f"- Error: {step.error}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _render_plan_snapshot(self, plan: Plan) -> str:
        """Render a compact human-readable status snapshot for a plan."""
        lines = []
        for step in plan.steps:
            parts = [f"- [{step.status.value}] {step.id}: {step.description}"]
            if step.tool_hint:
                parts.append(f"(tool: {step.tool_hint})")
            if step.result_summary:
                parts.append(f"result={step.result_summary}")
            if step.error:
                parts.append(f"error={step.error}")
            lines.append(" ".join(parts))
        return "\n".join(lines) if lines else "- (no steps)"

    def _load_plan_from_markdown(self, content: str) -> Plan:
        """Parse a saved plan markdown document into a Plan."""
        task = ""
        task_match = re.search(r"^# Saved Plan:\s*(.+)$", content, re.MULTILINE)
        if task_match:
            task = task_match.group(1).strip()
        task_line_match = re.search(r"^- Task:\s*(.+)$", content, re.MULTILINE)
        if task_line_match:
            task = task or task_line_match.group(1).strip()

        steps: list[PlanStep] = []
        canonical_lines = content.splitlines()
        current_step: PlanStep | None = None
        saw_canonical = False
        for raw_line in canonical_lines:
            line = raw_line.strip()
            heading_match = re.match(r"^###\s+(.+)$", line)
            if heading_match:
                saw_canonical = True
                if current_step is not None:
                    steps.append(current_step)
                current_step = PlanStep(id=heading_match.group(1).strip(), description="")
                continue
            if current_step is None:
                continue
            if line.startswith("- Description:"):
                current_step.description = line.split(":", 1)[1].strip()
            elif line.startswith("- Tool hint:"):
                current_step.tool_hint = line.split(":", 1)[1].strip().strip("`")
            elif line.startswith("- Status:"):
                status_value = line.split(":", 1)[1].strip().strip("`")
                current_step.status = current_step.status.__class__(status_value)
            elif line.startswith("- Result:"):
                current_step.result_summary = line.split(":", 1)[1].strip()
            elif line.startswith("- Error:"):
                current_step.error = line.split(":", 1)[1].strip()
        if current_step is not None:
            steps.append(current_step)

        if not saw_canonical:
            steps = self._load_legacy_plan_steps(content)

        plan = Plan(task=task or "Saved plan", steps=steps)
        plan._update_plan_status()
        return plan

    def _load_legacy_plan_steps(self, content: str) -> list[PlanStep]:
        """Parse the original numbered-list saved plan format."""
        steps: list[PlanStep] = []
        current_step: PlanStep | None = None
        for raw_line in content.splitlines():
            line = raw_line.rstrip()
            step_match = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s+—\s+(.+)$", line.strip())
            if step_match:
                if current_step is not None:
                    steps.append(current_step)
                current_step = PlanStep(id=step_match.group(1).strip(), description=step_match.group(2).strip())
                continue
            if current_step is None:
                continue
            stripped = line.strip()
            if stripped.startswith("- Tool hint:"):
                current_step.tool_hint = stripped.split(":", 1)[1].strip().strip("`")
            elif stripped.startswith("- Status:"):
                status_value = stripped.split(":", 1)[1].strip().strip("`")
                current_step.status = current_step.status.__class__(status_value)
            elif stripped.startswith("- Result:"):
                current_step.result_summary = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("- Error:"):
                current_step.error = stripped.split(":", 1)[1].strip()
        if current_step is not None:
            steps.append(current_step)
        return steps

    def _persist_current_plan(self) -> None:
        """Rewrite the current plan file using the canonical plan markdown format."""
        plan = self.state.current_plan
        plan_path = self.state.plan_file_path
        if not plan or not plan_path:
            return
        path = Path(plan_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render_saved_plan(plan, path), encoding="utf-8")

    def _refresh_plan_from_file(self) -> Plan | None:
        """Reload the current plan from disk if a saved plan file exists."""
        plan_path = self.state.plan_file_path
        if not plan_path:
            return self.state.current_plan
        path = Path(plan_path)
        if not path.exists():
            return self.state.current_plan
        try:
            plan = self._load_plan_from_markdown(path.read_text(encoding="utf-8"))
        except Exception:
            return self.state.current_plan
        self.state.current_plan = plan
        return plan

    def _sync_plan_progress(self, plan: Plan, active_step_id: str | None = None) -> None:
        """Mirror the top-level plan steps into the shared todo list state."""
        from opennova.tools.todo_tools import TodoWriteTool

        todos = []
        for step in plan.steps:
            status = "pending"
            if step.status.value == "running" or step.id == active_step_id:
                status = "in_progress"
            elif step.status.value == "done":
                status = "done"
            elif step.status.value == "failed":
                status = "cancelled"
            todos.append({"id": step.id, "content": step.description, "status": status})
        TodoWriteTool.replace_todos(todos)

    async def _confirm_plan(self, plan: Plan) -> bool:
        """
        Confirm plan execution with user.

        Override this method or register a 'plan_confirm' callback
        for custom confirmation UI.

        Args:
            plan: Plan to confirm

        Returns:
            True if confirmed, False otherwise
        """
        if "plan_confirm" in self._callbacks:
            return self._callbacks["plan_confirm"](plan)
        return True

    def _should_continue_on_failure(self) -> bool:
        """Whether to continue execution after a step failure."""
        return False

    async def _run_act_mode(
        self,
        task: str,
        stream: bool = True,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        preserve_plan_state: bool = False,
        preserve_context: bool = False,
    ) -> str:
        """
        Run in act mode: execute directly without planning.

        Args:
            task: Task description
            stream: Whether to stream output
            preserve_context: If True, keep existing messages (e.g., skill prompt
                from /skill command) instead of clearing context.

        Returns:
            Final result string
        """
        await self._ensure_mcp_ready()

        self.loop = ReActLoop(
            llm=self.llm,
            tool_registry=self.tool_registry,
            state=self.state,
            max_iterations=self.max_iterations,
            stream=stream,
            progress_callback=progress_callback,
            iteration_start_callback=lambda messages: self._emit("iteration_start", messages),
            interaction_callback=self._callbacks.get("interaction"),
            skill_registry=getattr(self, "skill_registry", None),
            context_manager=self.context_manager,
            working_memory=self.working_memory,
            guardrails=getattr(self, "guardrails", None),
            working_dir=os.getcwd(),
            hook_manager=getattr(self, "hook_manager", None),
            audit_logger=getattr(self, "security_audit_logger", None),
        )
        started_at = perf_counter()
        if not preserve_context:
            self.context_manager.clear()
        self.working_memory.set_task(task)
        self.working_memory.start_task()
        if not preserve_context:
            self.loop.set_context(self._build_memory_messages(task))
        elif not self.context_manager.messages:
            # First turn: inject project memory directly (set_context clears, so we add manually)
            for msg in self._build_memory_messages(task):
                self.context_manager.add_message(msg)

        def on_thought(thought: str) -> None:
            if self.show_thinking:
                self._emit("thought", thought)

        def on_action(tool_name: str, args: dict) -> None:
            self._emit("action", tool_name, args)

        def on_result(result: ToolResult) -> None:
            self._emit("result", result)

        def on_stream(chunk: StreamChunk) -> None:
            self._emit("stream", chunk)

        def on_tool_event(event: ToolEvent) -> None:
            self.tool_events.append(event.to_dict())
            self._emit("tool_event", event)

        try:
            result = await self.loop.run(
                task,
                on_thought=on_thought if self.show_thinking else None,
                on_action=on_action,
                on_result=on_result,
                on_stream=on_stream if stream else None,
                on_tool_event=on_tool_event,
                preserve_plan_state=preserve_plan_state,
                preserve_context=preserve_context,
            )
        except Exception:
            self.working_memory.complete_task(success=False, error="Act mode execution failed")
            self._record_run_session(task, success=False, started_at=started_at)
            self._save_session_messages()
            raise

        success = not (
            result.startswith("Task incomplete:")
            or result.startswith("Task failed:")
            or result == "Plan approval required before execution"
        )
        self.working_memory.complete_task(success=success, error=None if success else result)
        self._record_run_session(task, success=success, started_at=started_at)
        self._save_session_messages()
        return result

    async def chat(self, message: str, stream: bool = True) -> str:
        """
        Simple chat interaction without tool execution.

        Args:
            message: User message
            stream: Whether to stream output

        Returns:
            Assistant response
        """
        messages = [
            Message(role="system", content="You are a helpful AI assistant."),
            Message(role="user", content=message),
        ]

        if stream:
            full_content = ""
            async for chunk in self.llm.stream_chat(messages, temperature=0.7):
                if chunk.content:
                    full_content += chunk.content
                    self._emit("stream", chunk)
            return full_content
        else:
            response = await self.llm.chat(messages, temperature=0.7)
            return response.content

    def clear_conversation(self) -> None:
        """Clear current conversation context and start a fresh session."""
        self._save_session_messages()
        self.context_manager.clear()
        self.context_manager.set_compressed_summary(None)
        self.state.reset("")
        self.session_transcript = []
        sm = getattr(self, "session_manager", None)
        if sm is not None:
            sm.clear_session()
            sm.start_session()

    def _save_session_messages(self) -> None:
        """Persist all context messages and compression markers to session JSONL."""
        try:
            summary = self.context_manager.get_compressed_summary()
            self.session_manager.save_snapshot(
                self.context_manager.messages,
                compression_summary=summary,
                transcript_events=self.session_transcript,
                plan_state=self._serialize_plan_state(),
            )
        except Exception:
            pass

    def record_session_transcript_event(self, kind: str, **payload: Any) -> None:
        """Record a replayable TUI transcript event for the active session."""
        self.session_transcript.append({"kind": kind, **payload})

    def resume_session(self, session_id: str) -> Any:
        """Load a past session's messages into the context manager.

        Restores compression state from session markers so the compact
        context view (summary + recent messages) is reconstructed.
        """
        loaded = self.session_manager.load_session_with_summary(session_id)
        self.context_manager.clear()
        self.context_manager.set_compressed_summary(loaded.compression_summary)
        for msg in loaded.messages:
            self.context_manager.add_message(msg)
        # Keep writing to the resumed session instead of spawning a duplicate file.
        self.session_manager.resume_session(session_id)
        self.session_transcript = [dict(event.payload) for event in loaded.transcript_events]
        self._restore_plan_state(loaded.plan_state)
        self._save_session_messages()
        return loaded

    def _serialize_plan_state(self) -> dict[str, Any]:
        """Return the minimal persisted plan-state payload for session resume."""
        state = getattr(self, "state", None)
        if state is None:
            return {}
        return {
            "current_plan": state.current_plan.to_dict() if getattr(state, "current_plan", None) else None,
            "plan_file_path": str(state.plan_file_path) if getattr(state, "plan_file_path", None) else None,
            "plan_approval_status": getattr(getattr(state, "plan_approval_status", None), "value", None),
        }

    def _restore_plan_state(self, plan_state: dict[str, Any] | None) -> None:
        """Restore persisted plan state, preferring the saved plan file when present."""
        if not plan_state:
            return

        current_plan = plan_state.get("current_plan")
        plan_file_path = plan_state.get("plan_file_path")
        approval_status = plan_state.get("plan_approval_status")

        if plan_file_path and hasattr(self.state, "set_plan_file_path"):
            self.state.set_plan_file_path(plan_file_path)

        restored_plan: Plan | None = None
        if plan_file_path and Path(plan_file_path).exists():
            try:
                restored_plan = self._load_plan_from_markdown(
                    Path(plan_file_path).read_text(encoding="utf-8")
                )
            except Exception:
                restored_plan = None
        elif isinstance(current_plan, dict):
            restored_plan = Plan.from_dict(current_plan)

        if restored_plan is not None and hasattr(self.state, "set_plan"):
            self.state.set_plan(restored_plan)

        if approval_status:
            with suppress(ValueError):
                self.state.plan_approval_status = PlanApprovalStatus(approval_status)

    def get_sessions(self) -> list[Any]:
        """List all saved sessions for the current project."""
        return self.session_manager.list_sessions()

    def get_state(self) -> AgentState:
        """Get current agent state."""
        return self.state

    def get_tools(self) -> list[str]:
        """Get list of registered tool names."""
        return self.tool_registry.list_names()

    async def init_project_guide_async(self, force: bool = False) -> ToolResult:
        """Initialize OPENNOVA.md using LLM-driven project understanding."""
        from opennova.memory.project_guide import ProjectGuideManager

        project_memory = getattr(self, "project_memory", None)
        project_path = getattr(project_memory, "project_path", Path(os.getcwd()))
        manager = ProjectGuideManager(project_path=project_path)

        if manager.exists() and not force:
            result = manager.create_or_skip(force=False)
            return ToolResult(
                success=True,
                output=result.message,
                metadata={
                    "status": result.status,
                    "file_path": str(result.path),
                    "overwritten": result.overwritten,
                    "force": force,
                    "source": "skip",
                },
            )

        brief = manager.build_generation_brief()
        messages = [
            Message(
                role="system",
                content=(
                    "You are generating an OPENNOVA.md project guide for an AI coding assistant. "
                    "Analyze the provided project facts and write a practical, high-signal guide in Markdown. "
                    "Do not output code fences around the whole document."
                ),
            ),
            Message(
                role="user",
                content=(
                    "Write OPENNOVA.md for this repository.\n\n"
                    "Required coverage (you decide structure/detail):\n"
                    "- project overview and goals\n"
                    "- tech stack and architecture conventions\n"
                    "- directory structure highlights\n"
                    "- common development commands\n"
                    "- coding standards and workflow preferences\n"
                    "- testing expectations\n"
                    "- environment variables and third-party services\n"
                    "- known issues / risks / forbidden operations\n"
                    "- practical collaboration guidance for the AI assistant\n\n"
                    "Requirements:\n"
                    "1. Be specific to the current repository, avoid generic filler.\n"
                    "2. If facts are unknown, explicitly mark as TODO rather than inventing.\n"
                    "3. Keep it concise but actionable.\n"
                    "4. Write in Chinese by default.\n\n"
                    f"Project facts:\n{brief}"
                ),
            ),
        ]

        try:
            response = await self.llm.chat(messages, temperature=0.2)
            content = ProjectGuideManager.normalize_generated_markdown(response.content)
            if not content.strip():
                raise ValueError("LLM returned empty content")
            result = manager.create_or_skip(force=force, content=content + "\n")
            source = "llm"
        except Exception:
            # Fallback only when LLM generation fails.
            result = manager.create_or_skip(force=force)
            source = "fallback_template"

        return ToolResult(
            success=True,
            output=result.message,
            metadata={
                "status": result.status,
                "file_path": str(result.path),
                "overwritten": result.overwritten,
                "force": force,
                "source": source,
            },
        )

    def get_model_info(self) -> dict[str, Any]:
        """Get information about the current LLM model."""
        return self.llm.get_model_info()

    def get_skills(self) -> list[str]:
        """Get list of loaded skills."""
        if self.skill_registry:
            return self.skill_registry.list_user_invocable_skills()
        return []

    def get_skill_argument_hint(self, skill_name: str, typed_args: str = "") -> str | None:
        """Get a progressive argument hint for a loaded skill."""
        if not self.skill_registry:
            return None
        return self.skill_registry.get_skill_argument_hint(skill_name, typed_args)

    def notify_file_paths_touched(self, paths: list[str]) -> dict[str, list[str]]:
        """Let the skill registry react to file paths observed during execution."""
        if not self.skill_registry:
            return {"activated": [], "discovered": []}
        cwd = os.getcwd()
        discovered = self.skill_registry.discover_for_paths(paths, cwd)
        activated = self.skill_registry.activate_for_paths(paths, cwd)
        return {"activated": activated, "discovered": discovered}

    def invoke_skill(self, skill_name: str, skill_args: str = "", caller: str = "user") -> ToolResult:
        """Invoke a loaded skill for either the user or the model."""
        if not self.skill_registry:
            return ToolResult(success=False, output="", error="Skill registry is not available")

        normalized_name = str(skill_name).strip().lstrip("/")
        normalized_args = str(skill_args).strip()
        resolution = self.skill_registry.resolve_skill_name(normalized_name)
        if resolution.is_ambiguous:
            matches = ", ".join(resolution.matches)
            return ToolResult(
                success=False,
                output="",
                error=f"Ambiguous skill '{normalized_name}'. Use one of: {matches}",
            )
        if not resolution.resolved_name:
            return ToolResult(success=False, output="", error=f"Skill '{normalized_name}' is unavailable")
        resolved_name = resolution.resolved_name

        if caller == "model":
            if not self.skill_registry.can_model_invoke(resolved_name):
                return ToolResult(success=False, output="", error=f"Skill '{resolved_name}' cannot be invoked by the model")
        else:
            if not self.skill_registry.can_user_invoke(resolved_name):
                return ToolResult(success=False, output="", error=f"Skill '{resolved_name}' cannot be invoked directly by the user")

        prompt = self.skill_registry.materialize_skill_prompt(resolved_name, normalized_args)
        if prompt is None:
            return ToolResult(success=False, output="", error=f"Skill '{resolved_name}' is unavailable")

        return ToolResult(
            success=True,
            output=f"Invoked skill: {resolved_name}",
            metadata={
                "skill": normalized_name,
                "resolved_skill": resolved_name,
                "args": normalized_args,
                "skill_prompt": prompt.prompt,
                "allowed_tools": prompt.allowed_tools,
                "model": prompt.model,
                "argument_names": prompt.argument_names,
                "hooks": prompt.hooks,
                "activation_state": prompt.activation_state,
                "source_path": prompt.source_path,
                "skill_dir": prompt.skill_dir,
                "caller": caller,
            },
        )

    def get_mcp_servers(self) -> list[str]:
        """Get list of connected MCP servers."""
        if self.mcp_manager:
            return self.mcp_manager.get_server_names()
        return []

    def reload_skills(self) -> int:
        """
        Reload all markdown skills from disk.

        Returns:
            Number of skills loaded
        """
        from opennova.skills.examples import get_builtin_skill_dirs
        from opennova.skills.registry import SkillRegistry

        skills_config = self.config.get("skills", {})
        if not skills_config.get("enabled", True):
            if self.skill_registry:
                self.skill_registry.clear()
            return 0

        if not self.skill_registry:
            self.skill_registry = SkillRegistry()

        configured_dirs = [Path(path) for path in skills_config.get("dirs", [])]
        excluded = skills_config.get("exclude", [])

        self.skill_registry.load_all(
            directories=[*get_builtin_skill_dirs(), *configured_dirs],
            sources=self.plugin_manager.get_skill_sources(),
            excluded=excluded,
        )
        return len(self.skill_registry)
