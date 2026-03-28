"""
Agent Runtime - Main orchestrator for OpenNova agent.

Manages the agent lifecycle:
- Initialization with configuration
- Mode switching (plan/act)
- Tool registration
- ReAct loop coordination
"""

import os
from typing import Any, Callable

from opennova.providers.base import BaseLLMProvider, Message, StreamChunk
from opennova.providers.factory import ProviderFactory
from opennova.runtime.loop import ParsedAction, ReActLoop, run_simple_task
from opennova.runtime.state import AgentState, AgentMode, Plan
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult, register_builtin_tools


class AgentRuntime:
    """
    Main Agent Runtime class.

    Orchestrates all components:
    - LLM Provider
    - Tool Registry
    - State Management
    - ReAct Loop
    """

    def __init__(
        self,
        config: dict[str, Any],
        register_default_tools: bool = True,
    ):
        """
        Initialize the agent runtime.

        Args:
            config: Configuration dictionary with providers and agent settings
            register_default_tools: Whether to register built-in tools
        """
        self.config = config
        self.state = AgentState()
        self.tool_registry = ToolRegistry()

        agent_config = config.get("agent", {})
        self.max_iterations = agent_config.get("max_iterations", 20)
        self.show_thinking = agent_config.get("show_thinking", True)
        self.auto_confirm = agent_config.get("auto_confirm", False)

        self.llm = ProviderFactory.create_provider(config)

        self.loop: ReActLoop | None = None
        self._callbacks: dict[str, Callable] = {}

        if register_default_tools:
            self._register_builtin_tools()

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

        self.tool_registry.register(ReadFileTool())
        self.tool_registry.register(WriteFileTool())
        self.tool_registry.register(CreateFileTool())
        self.tool_registry.register(DeleteFileTool())
        self.tool_registry.register(ListDirectoryTool())
        self.tool_registry.register(ExecuteCommandTool(config=tool_config))

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
            return await self._run_act_mode(task, stream=stream)

    async def _run_plan_mode(self, task: str, stream: bool = True) -> str:
        """
        Run in plan mode: generate plan first, then execute.

        Args:
            task: Task description
            stream: Whether to stream output

        Returns:
            Final result string
        """
        plan = await self._create_plan(task)

        self.state.set_plan(plan)

        self._emit("plan", plan)

        if not self.auto_confirm:
            confirmed = await self._confirm_plan(plan)
            if not confirmed:
                return "Plan cancelled by user"

        self.state.set_mode("act")

        if plan.steps:
            for step in plan.steps:
                if self.state.is_complete:
                    break

                plan.mark_step_running(step.id)

                step_task = step.description
                result = await self._run_act_mode(step_task, stream=stream)

                if "error" in result.lower() or "failed" in result.lower():
                    plan.mark_step_failed(step.id, result)
                    if not self._should_continue_on_failure():
                        break
                else:
                    plan.mark_step_done(step.id, result)

        return self.state.last_result or "Plan execution complete"

    async def _create_plan(self, task: str) -> Plan:
        """
        Create a plan from a task.

        Args:
            task: Task description

        Returns:
            Generated Plan
        """
        from opennova.runtime.state import PlanStep, PlanStatus

        plan_prompt = f"""Break down the following task into clear, actionable steps.

Task: {task}

Respond with a JSON object in this format:
{{
    "task_summary": "Brief task description",
    "steps": [
        {{"id": "step_1", "description": "Step description", "tool_hint": "suggested_tool"}},
        ...
    ]
}}

Only respond with the JSON object, no other text."""

        messages = [
            Message(role="system", content="You are a helpful assistant that creates task plans."),
            Message(role="user", content=plan_prompt),
        ]

        response = await self.llm.chat(messages, temperature=0.7)

        try:
            import json

            data = json.loads(response.content)

            steps = [
                PlanStep(
                    id=s.get("id", f"step_{i + 1}"),
                    description=s.get("description", ""),
                    tool_hint=s.get("tool_hint"),
                )
                for i, s in enumerate(data.get("steps", []))
            ]

            return Plan(
                task=data.get("task_summary", task),
                steps=steps,
            )
        except json.JSONDecodeError:
            step = PlanStep(
                id="step_1",
                description=task,
            )
            return Plan(task=task, steps=[step])

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

    async def _run_act_mode(self, task: str, stream: bool = True) -> str:
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
