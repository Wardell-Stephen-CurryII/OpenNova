"""
ReAct Loop Implementation.

Implements the core Reason-Act-Observe cycle:
1. Reason: LLM thinks about what to do
2. Act: Execute a tool/action
3. Observe: Capture and process results
4. Repeat until complete or max iterations reached
"""

import asyncio
import json
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from opennova.memory.context import ContextManager
from opennova.memory.working import ActionStatus, WorkingMemory
from opennova.providers.base import (
    BaseLLMProvider,
    FinishReason,
    LLMResponse,
    Message,
    StreamChunk,
    ToolCall,
    ToolSchema,
)
from opennova.runtime.state import AgentState, Plan
from opennova.skills.registry import SkillRegistry
from opennova.tools.base import BaseTool, ToolRegistry, ToolResult


@dataclass
class ParsedAction:
    """Parsed action from LLM response."""

    tool_name: str
    arguments: dict[str, Any]
    thought: str | None = None
    requires_confirmation: bool = False
    is_final: bool = False
    raw_response: str = ""


class ReActLoop:
    """
    ReAct (Reason-Act-Observe) Loop Implementation.

    The core execution loop that:
    1. Sends context to LLM for reasoning
    2. Parses tool calls from LLM response
    3. Executes tools and captures results
    4. Updates context with observations
    5. Repeats until task complete or max iterations
    """

    def __init__(
        self,
        llm: BaseLLMProvider,
        tool_registry: ToolRegistry,
        state: AgentState,
        max_iterations: int = 500,
        stream: bool = True,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        iteration_start_callback: Callable[[list[Message]], None] | None = None,
        interaction_callback: Callable[[dict[str, Any]], Any] | None = None,
        skill_registry: SkillRegistry | None = None,
        context_manager: ContextManager | None = None,
        working_memory: WorkingMemory | None = None,
    ):
        """
        Initialize ReAct loop.

        Args:
            llm: LLM provider for reasoning
            tool_registry: Registry of available tools
            state: Agent state to track execution
            max_iterations: Maximum number of iterations
            stream: Whether to use streaming output
        """
        self.llm = llm
        self.tool_registry = tool_registry
        self.state = state
        self.max_iterations = max_iterations
        self.stream = stream
        self.progress_callback = progress_callback
        self.iteration_start_callback = iteration_start_callback
        self.interaction_callback = interaction_callback
        self.skill_registry = skill_registry
        self.context_manager = context_manager or ContextManager(model=llm.model)
        self.working_memory = working_memory
        self.on_thought: Callable | None = None
        self.on_action: Callable | None = None
        self.on_result: Callable | None = None
        self.on_stream: Callable | None = None
        self._errors: list[str] = []

    @property
    def messages(self) -> list[Message]:
        """Expose the current context-manager message list."""
        return self.context_manager.messages

    @messages.setter
    def messages(self, messages: list[Message]) -> None:
        self.context_manager.messages = messages

    def set_context(self, messages: list[Message]) -> None:
        """Set initial conversation context."""
        self.context_manager.clear()
        for message in messages:
            self.context_manager.add_message(message)

    def add_message(self, message: Message) -> None:
        """Add a message to the context."""
        self.context_manager.add_message(message)

    async def run(
        self,
        task: str,
        on_thought: Callable | None = None,
        on_action: Callable | None = None,
        on_result: Callable | None = None,
        on_stream: Callable | None = None,
        preserve_plan_state: bool = False,
    ) -> str:
        """
        Run the ReAct loop for a task.

        Args:
            task: Task description to execute
            on_thought: Callback for thought output
            on_action: Callback for action execution
            on_result: Callback for tool results
            on_stream: Callback for streaming chunks

        Returns:
            Final result string
        """
        import traceback

        if preserve_plan_state:
            self.state.reset_execution(task)
        else:
            self.state.reset(task)
        self.on_thought = on_thought
        self.on_action = on_action
        self.on_result = on_result
        self.on_stream = on_stream
        self._errors = []

        if not self.messages:
            self.add_message(
                Message(
                    role="system",
                    content=self._build_system_prompt(),
                )
            )

        self.add_message(Message(role="user", content=f"Task: {task}"))
        self._report_progress(activity=f"Started task: {task}")

        while (
            not self.state.is_complete
            and self.state.iteration < self.max_iterations
            and not self.state.has_too_many_errors()
        ):
            self._emit_iteration_start()
            self.state.increment_iteration()

            try:
                response = await self._think()

                action = self._parse_response(response)

                if action.is_final:
                    self.state.mark_complete(action.thought or response.content or "")
                    self._report_progress(activity="Completed task", mark_complete=True)
                    break

                if action.tool_name and action.tool_name in self.tool_registry:
                    if self.on_action:
                        self.on_action(action.tool_name, action.arguments)

                    self._report_progress(activity=f"Running tool: {action.tool_name}", last_tool_name=action.tool_name)
                    result = await self._act(action)

                    if self.on_result:
                        self.on_result(result)

                    self._report_progress(
                        activity=f"Completed tool: {action.tool_name}",
                        last_tool_name=action.tool_name,
                        tool_use_increment=1,
                        token_count=response.usage.total_tokens if response.usage else 0,
                    )
                    self._observe(action, result)
                else:
                    observation = Message(
                        role="user",
                        content="Please use an available tool or skill to complete the task. "
                        "Available tools: " + ", ".join(self.tool_registry.list_names()),
                    )
                    self.add_message(observation)

            except Exception as e:
                self.state.increment_error()
                error_detail = f"Error in iteration {self.state.iteration}: {type(e).__name__}: {e}"
                tb = traceback.format_exc()
                full_error = f"{error_detail}\n\nTraceback:\n{tb}"
                self._errors.append(full_error)
                print(f"\n[ERROR] {full_error}\n")
                self.add_message(
                    Message(
                        role="user",
                        content=f"An error occurred: {error_detail}. Please try a different approach.",
                    )
                )

        if self.state.iteration >= self.max_iterations:
            return f"Task incomplete: reached maximum iterations ({self.max_iterations})"

        if self.state.has_too_many_errors():
            error_summary = "\n\n".join(self._errors)
            return f"Task failed: too many errors ({self.state.error_count})\n\nDetailed errors:\n{error_summary}"

        return self.state.last_result or "Task completed"

    def _report_progress(
        self,
        activity: str,
        last_tool_name: str | None = None,
        token_count: int = 0,
        tool_use_increment: int = 0,
        mark_complete: bool = False,
    ) -> None:
        """Report execution progress to the caller."""
        if not self.progress_callback:
            return

        payload = {
            "activity": activity,
            "last_tool_name": last_tool_name,
            "token_count": token_count,
            "tool_use_increment": tool_use_increment,
            "iteration": self.state.iteration,
            "is_complete": mark_complete,
        }
        self.progress_callback(payload)

    def _emit_iteration_start(self) -> None:
        """Notify listeners before a new iteration begins."""
        if self.iteration_start_callback:
            self.iteration_start_callback(self.messages)

    async def _think(self) -> LLMResponse:
        """
        Think step: Get LLM response.

        Returns:
            LLM response with potential tool calls
        """
        tools = self.tool_registry.list_tools()

        if self.stream and self.on_stream:
            full_content = ""
            tool_calls: list[ToolCall] = []
            usage = None

            async for chunk in self.llm.stream_chat(
                self.context_manager.get_messages_for_llm(),
                tools=tools,
                temperature=0.7,
            ):
                self.on_stream(chunk)

                if chunk.content:
                    full_content += chunk.content
                if chunk.tool_call:
                    tool_calls.append(chunk.tool_call)
                if chunk.usage:
                    usage = chunk.usage

            return LLMResponse(
                content=full_content,
                tool_calls=tool_calls if tool_calls else None,
                usage=usage,
                finish_reason=FinishReason.TOOL_CALL if tool_calls else FinishReason.STOP,
                model=self.llm.model,
            )
        else:
            response = await self.llm.chat(
                self.context_manager.get_messages_for_llm(),
                tools=tools,
                temperature=0.7,
            )
            return response

    def _parse_response(self, response: LLMResponse) -> ParsedAction:
        """
        Parse LLM response into an action.

        Args:
            response: LLM response to parse

        Returns:
            Parsed action with tool name and arguments
        """
        content = response.content or ""
        action = ParsedAction(
            thought=content,
            tool_name="",
            arguments={},
            raw_response=content,
        )

        if response.tool_calls:
            tool_call = response.tool_calls[0]
            action.tool_name = tool_call.name
            action.arguments = tool_call.arguments or {}

            if self._is_dangerous_action(action.tool_name, action.arguments):
                action.requires_confirmation = True
        elif self.skill_registry:
            action = self._parse_skill_invocation(action, content)

        if response.finish_reason == FinishReason.STOP and not response.tool_calls and not action.tool_name:
            action.is_final = True

        return action

    def _parse_skill_invocation(self, action: ParsedAction, content: str) -> ParsedAction:
        """Detect a markdown skill invocation from assistant text."""
        if not self.skill_registry:
            return action

        stripped = content.strip()
        if not stripped.lower().startswith("/skill"):
            return action

        parts = stripped.split(maxsplit=2)
        if len(parts) < 2:
            return action

        skill_name = parts[1].strip()
        skill_args = parts[2].strip() if len(parts) > 2 else ""
        if not self.skill_registry.can_model_invoke(skill_name):
            return action

        action.tool_name = "skill"
        action.arguments = {"skill": skill_name, "args": skill_args}
        return action

    def _record_file_observation(self, action: ParsedAction, result: ToolResult) -> None:
        """Record file observations from file-oriented tool executions."""
        if not self.working_memory or not result.success:
            return

        file_path = None
        if isinstance(result.metadata, dict):
            file_path = result.metadata.get("file_path")
        if not file_path:
            file_path = action.arguments.get("file_path")
        if not file_path:
            return

        change_types = {
            "read_file": "read",
            "write_file": "modified",
            "create_file": "created",
            "delete_file": "deleted",
        }
        change_type = change_types.get(action.tool_name)
        if not change_type:
            return

        preview = (result.output or result.error or "")[:200] or None
        self.working_memory.observe_file(file_path, change_type, preview)

    async def _act(self, action: ParsedAction) -> ToolResult:
        """
        Act step: Execute a tool.

        Args:
            action: Action to execute

        Returns:
            Tool execution result
        """
        tool = self.tool_registry.get(action.tool_name)
        action_record = None
        if self.working_memory:
            action_record = self.working_memory.record_action(action.tool_name, action.arguments)

        try:
            if hasattr(tool, "async_execute"):
                result = await tool.async_execute(**action.arguments)
            else:
                result = tool.execute(**action.arguments)

            if result.success and result.metadata.get("interaction_required"):
                result = await self._resolve_interaction(result)
            if self.working_memory and action_record:
                status = ActionStatus.SUCCESS if result.success else ActionStatus.FAILED
                self.working_memory.update_action(
                    action_record.id,
                    status,
                    result=result.output,
                    error=result.error,
                )
                self._record_file_observation(action, result)
            return result
        except Exception as e:
            if self.working_memory and action_record:
                self.working_memory.update_action(
                    action_record.id,
                    ActionStatus.FAILED,
                    error=str(e),
                )
            return ToolResult(
                success=False,
                output="",
                error=f"Tool execution failed: {e}",
            )
    async def _resolve_interaction(self, result: ToolResult) -> ToolResult:
        """Resolve an interactive tool result through the registered runtime callback."""
        if not self.interaction_callback:
            return ToolResult(
                success=False,
                output=result.output,
                error="Interactive response required but no interaction handler is available.",
                metadata={**result.metadata, "interaction_unresolved": True},
            )

        interaction_result = self.interaction_callback(result.metadata)
        if asyncio.iscoroutine(interaction_result):
            interaction_result = await interaction_result

        prompt_payload = result.metadata.get("prompt_payload", {})
        question = prompt_payload.get("question", "")
        return ToolResult(
            success=True,
            output=f"Answer to: {question}\n{interaction_result.get('display', '')}".strip(),
            metadata={
                **result.metadata,
                "interaction_required": False,
                "answers": interaction_result.get("answers", {}),
                "answer": interaction_result.get("answer"),
                "selected_options": interaction_result.get("selected_options", []),
            },
        )

    def _observe(self, action: ParsedAction, result: ToolResult) -> None:
        """
        Observe step: Add results to context.

        Args:
            action: The action that was executed
            result: The tool execution result
        """
        self.add_message(
            Message(
                role="assistant",
                content=action.thought or "",
                tool_calls=[
                    ToolCall(
                        id=f"call_{self.state.iteration}",
                        name=action.tool_name,
                        arguments=action.arguments,
                    )
                ]
                if action.tool_name
                else None,
            )
        )

        self.add_message(
            Message(
                role="tool",
                content=result.to_string(),
                tool_call_id=f"call_{self.state.iteration}",
                name=action.tool_name,
            )
        )

        self.state.last_action = action.tool_name
        self.state.last_result = result.output

    def _build_system_prompt(self) -> str:
        """Build system prompt for the agent."""
        tools_description = []
        for schema in self.tool_registry.list_tools():
            params_desc = []
            props = schema.parameters.get("properties", {})
            required = schema.parameters.get("required", [])

            for name, prop in props.items():
                req = " (required)" if name in required else ""
                params_desc.append(
                    f"    - {name}: {prop.get('description', prop.get('type', ''))}{req}"
                )

            params_str = "\n".join(params_desc) if params_desc else "    No parameters"
            tools_description.append(f"- {schema.name}: {schema.description}\n{params_str}")

        skills_description = []
        if self.skill_registry:
            for name in self.skill_registry.list_model_invocable_skills():
                info = self.skill_registry.get_skill_info(name) or {}
                skill_line = f"- {name}: {info.get('description', '')}"
                if info.get("when_to_use"):
                    skill_line += f" - {info.get('when_to_use')}"
                skills_description.append(skill_line)

        prompt = f"""You are an AI coding assistant that helps users with software engineering tasks.

You have access to the following tools:
{chr(10).join(tools_description)}
"""

        if skills_description:
            prompt += f"""

You also have access to reusable skills through the skill tool:
{chr(10).join(skills_description)}

When one of these skills clearly matches the user's request, invoke the skill tool before continuing.
Do not rely on emitting literal /skill text when the skill tool is available.
"""

        prompt += """

Use these tools and skills to complete the user's task. When you have completed the task,
provide a summary of what was done.

Rules:
1. Always explain what you are doing before executing a tool or skill
2. If a tool fails, try to understand the error and attempt a different approach
3. Be careful with file operations - read before write when modifying existing files
4. When the task is complete, provide a clear summary
"""
        return prompt

    def _is_dangerous_action(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Check if an action is potentially dangerous."""
        dangerous_tools = {"delete_file", "execute_command", "write_file"}
        return tool_name in dangerous_tools


async def run_simple_task(
    llm: BaseLLMProvider,
    tool_registry: ToolRegistry,
    task: str,
    max_iterations: int = 20,
    stream: bool = True,
    on_stream: Callable | None = None,
) -> str:
    """
    Convenience function to run a simple task.

    Args:
        llm: LLM provider
        tool_registry: Tool registry with registered tools
        task: Task description
        max_iterations: Maximum iterations
        stream: Whether to stream output
        on_stream: Callback for streaming

    Returns:
        Final result string
    """
    state = AgentState()
    loop = ReActLoop(
        llm=llm,
        tool_registry=tool_registry,
        state=state,
        max_iterations=max_iterations,
        stream=stream,
    )
    return await loop.run(task, on_stream=on_stream)
