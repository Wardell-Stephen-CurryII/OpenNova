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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from opennova.providers.base import BaseLLMProvider, Message, StreamChunk
from opennova.providers.factory import ProviderFactory
from opennova.planning.planner import Planner
from opennova.runtime.loop import ParsedAction, ReActLoop, run_simple_task
from opennova.runtime.state import AgentState, AgentMode, Plan, PlanApprovalStatus, PlanStatus, PlanStep
from opennova.tasks import TaskManager
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult, register_builtin_tools


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

        self.llm = ProviderFactory.create_provider(config)

        self.loop: ReActLoop | None = None
        self._callbacks: dict[str, Callable] = {}
        self.planner = Planner(self.llm)

        self.mcp_manager = None
        self.skill_registry = None

        # Set global task manager for task tools
        from opennova.tools.task_tools import set_global_task_manager
        set_global_task_manager(self.task_manager)

        if register_default_tools:
            self._register_builtin_tools()

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
        }

        from opennova.tools.file_tools import (
            CreateFileTool,
            DeleteFileTool,
            ListDirectoryTool,
            ReadFileTool,
            WriteFileTool,
        )
        from opennova.tools.shell_tools import ExecuteCommandTool
        from opennova.tools.task_tools import (
            TaskCreateTool,
            TaskGetTool,
            TaskListTool,
            TaskOutputTool,
            TaskStopTool,
            TaskUpdateTool,
        )
        from opennova.tools.agent_tools import AgentTool, SendMessageTool
        from opennova.tools.ask_question_tool import AskUserQuestionTool
        from opennova.tools.plan_mode_tools import (
            EnterPlanModeTool,
            ExitPlanModeTool,
        )
        from opennova.tools.web_tools import WebFetchTool, WebSearchTool
        from opennova.tools.git_tools import (
            GitBranchTool,
            GitCommitTool,
            GitDiffTool,
            GitLogTool,
            GitStatusTool,
        )

        # File and shell tools
        self.tool_registry.register(ReadFileTool())
        self.tool_registry.register(WriteFileTool())
        self.tool_registry.register(CreateFileTool())
        self.tool_registry.register(DeleteFileTool())
        self.tool_registry.register(ListDirectoryTool())
        self.tool_registry.register(ExecuteCommandTool(config=tool_config))

        # Task management tools (Claude Code-style)
        self.tool_registry.register(TaskCreateTool())
        self.tool_registry.register(TaskListTool())
        self.tool_registry.register(TaskGetTool())
        self.tool_registry.register(TaskUpdateTool())
        self.tool_registry.register(TaskStopTool())
        self.tool_registry.register(TaskOutputTool())

        # Agent tools (Claude Code-style)
        self.tool_registry.register(AgentTool(config={"runtime": self}))
        self.tool_registry.register(SendMessageTool())

        # User interaction tools
        self.tool_registry.register(AskUserQuestionTool())

        # Plan mode tools
        self.tool_registry.register(EnterPlanModeTool(config={"state": self.state}))
        self.tool_registry.register(ExitPlanModeTool(config={"state": self.state}))

        # Web tools
        self.tool_registry.register(WebSearchTool())
        self.tool_registry.register(WebFetchTool())

        # Git tools
        self.tool_registry.register(GitCommitTool())
        self.tool_registry.register(GitStatusTool())
        self.tool_registry.register(GitDiffTool())
        self.tool_registry.register(GitLogTool())
        self.tool_registry.register(GitBranchTool())

    def _init_skills(self) -> None:
        """Initialize skill loading."""
        from opennova.skills.examples import get_builtin_skill_classes
        from opennova.skills.registry import SkillRegistry

        skills_config = self.config.get("skills", {})
        if not skills_config.get("enabled", True):
            return

        self.skill_registry = SkillRegistry(self.tool_registry)

        skill_dirs = skills_config.get("dirs", [])
        excluded = skills_config.get("exclude", [])

        self.skill_registry.load_all(
            directories=skill_dirs if skill_dirs else None,
            builtins=get_builtin_skill_classes(),
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

        servers = mcp_config.get("servers", [])
        for server_data in servers:
            try:
                server_config = MCPServerConfig.from_dict(server_data)
                # Store config for later connection
                self._mcp_server_configs.append(server_config)
            except Exception:
                pass

    async def connect_mcp_servers(self) -> dict[str, bool]:
        """
        Connect to all configured MCP servers.

        Returns:
            Dict of server names to connection status
        """
        if not self.mcp_manager:
            return {}

        # Use stored configs instead of re-parsing from config
        return await self.mcp_manager.connect_all(self._mcp_server_configs)

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
        return self._prepare_plan_for_approval(plan)

    def _prepare_plan_for_approval(self, plan: Plan) -> str:
        """Persist plan state and return an approval-gated response."""
        self.state.set_plan(plan)
        plan_file_path = self._save_plan_to_project(plan)
        self.state.set_plan_file_path(plan_file_path)
        self.state.mark_plan_awaiting_approval()

        self._emit("plan", plan, plan_file_path)
        return "Plan ready for approval"

    def _build_step_execution_task(self, plan: Plan, step: PlanStep) -> str:
        """Build an act-mode task prompt for an approved plan step."""
        remaining_steps = [
            pending_step.description
            for pending_step in plan.steps
            if pending_step.id != step.id and pending_step.status.value == "pending"
        ]

        lines = [
            "Execute the approved development plan step.",
            f"Overall plan: {plan.task}",
            f"Current step ({step.id}): {step.description}",
        ]

        if remaining_steps:
            lines.append("Remaining planned steps:")
            lines.extend(f"- {description}" for description in remaining_steps)

        lines.extend(
            [
                "Work on the current step while keeping the approved plan in mind.",
                "Do not re-plan from scratch unless execution reveals a concrete blocker.",
            ]
        )

        return "\n".join(lines)

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

        if plan.steps:
            for step in plan.steps:
                if self.state.is_complete:
                    break

                plan.mark_step_running(step.id)

                step_task = self._build_step_execution_task(plan, step)
                result = await self._run_act_mode(step_task, stream=stream)

                if result and ("error" in result.lower() or "failed" in result.lower()):
                    plan.mark_step_failed(step.id, result)
                    if not self._should_continue_on_failure():
                        break
                elif result:
                    plan.mark_step_done(step.id, result)
                else:
                    plan.mark_step_failed(step.id, "No result returned")

        return self.state.last_result or "Plan execution complete"

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
        summary = Planner(self.llm).get_plan_summary(plan)
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

        for index, step in enumerate(plan.steps, start=1):
            lines.append(f"{index}. **{step.id}** — {step.description}")
            if step.tool_hint:
                lines.append(f"   - Tool hint: `{step.tool_hint}`")
            lines.append(f"   - Status: `{step.status.value}`")
            if step.result_summary:
                lines.append(f"   - Result: {step.result_summary}")
            if step.error:
                lines.append(f"   - Error: {step.error}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

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
    ) -> str:
        """
        Run in act mode: execute directly without planning.

        Args:
            task: Task description
            stream: Whether to stream output

        Returns:
            Final result string
        """
        self.loop = ReActLoop(
            llm=self.llm,
            tool_registry=self.tool_registry,
            state=self.state,
            max_iterations=self.max_iterations,
            stream=stream,
            progress_callback=progress_callback,
            iteration_start_callback=lambda messages: self._emit("iteration_start", messages),
        )

        def on_thought(thought: str) -> None:
            if self.show_thinking:
                self._emit("thought", thought)

        def on_action(tool_name: str, args: dict) -> None:
            self._emit("action", tool_name, args)

        def on_result(result: ToolResult) -> None:
            self._emit("result", result)

        def on_stream(chunk: StreamChunk) -> None:
            self._emit("stream", chunk)

        return await self.loop.run(
            task,
            on_thought=on_thought if self.show_thinking else None,
            on_action=on_action,
            on_result=on_result,
            on_stream=on_stream if stream else None,
        )

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

    def get_state(self) -> AgentState:
        """Get current agent state."""
        return self.state

    def get_tools(self) -> list[str]:
        """Get list of registered tool names."""
        return self.tool_registry.list_names()

    def get_model_info(self) -> dict[str, Any]:
        """Get information about the current LLM model."""
        return self.llm.get_model_info()

    def get_skills(self) -> list[str]:
        """Get list of loaded skills."""
        if self.skill_registry:
            return self.skill_registry.list_skills()
        return []

    def get_mcp_servers(self) -> list[str]:
        """Get list of connected MCP servers."""
        if self.mcp_manager:
            return self.mcp_manager.get_server_names()
        return []

    def reload_skills(self) -> int:
        """
        Reload all skills from disk.

        Returns:
            Number of skills loaded
        """
        from opennova.skills.examples import get_builtin_skill_classes
        from opennova.skills.registry import SkillRegistry

        if not self.skill_registry:
            self.skill_registry = SkillRegistry(self.tool_registry)

        skills_config = self.config.get("skills", {})
        skill_dirs = skills_config.get("dirs", [])
        excluded = skills_config.get("exclude", [])

        self.skill_registry.load_all(
            directories=skill_dirs if skill_dirs else None,
            builtins=get_builtin_skill_classes(),
            excluded=excluded,
        )
        return len(self.skill_registry)
