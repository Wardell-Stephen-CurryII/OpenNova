"""
ReAct Loop Implementation.

Implements the core Reason-Act-Observe cycle:
1. Reason: LLM thinks about what to do
2. Act: Execute a tool/action
3. Observe: Capture and process results
4. Repeat until complete or max iterations reached
"""

import asyncio
import os
import re
import traceback
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from opennova.hooks import HookManager
from opennova.memory.context import ContextCapacityError, ContextManager, MessageAddResult
from opennova.memory.working import ActionStatus, WorkingMemory
from opennova.providers.base import (
    BaseLLMProvider,
    FinishReason,
    LLMResponse,
    Message,
    ToolCall,
)
from opennova.runtime.cancellation import CancellationToken
from opennova.runtime.events import (
    ToolEvent,
    ToolEventType,
    ToolUseContext,
    reset_current_tool_context,
    set_current_tool_context,
)
from opennova.runtime.state import AgentState
from opennova.runtime.workflow import WorkflowDecision, WorkflowRouter, WorkflowRoutingResult
from opennova.security.guardrails import Guardrails, GuardResult, RiskLevel
from opennova.security.secrets import redact_sensitive_data
from opennova.skills.hook_adapter import register_skill_hooks
from opennova.skills.registry import SkillRegistry
from opennova.tools.base import ToolRegistry, ToolResult

PLAN_MODE_IMPLEMENTATION_TOOLS = {
    "write_file",
    "create_file",
    "edit_file",
    "multi_edit_file",
    "delete_file",
    "execute_command",
    "git_commit",
    "git_branch",
    "git_push",
    "enter_worktree",
    "exit_worktree",
    "init_project_guide",
}
BATCH_BARRIER_TOOLS = {
    "skill",
    "ask_user_question",
    "enter_plan_mode",
    "exit_plan_mode",
}
RUNTIME_SYSTEM_MESSAGE_NAME = "opennova_runtime"
LEGACY_RUNTIME_SYSTEM_PROMPT_PREFIX = (
    "You are an AI coding assistant that helps users with software engineering tasks."
)


@dataclass
class ParsedAction:
    """Parsed action from LLM response."""

    tool_name: str
    arguments: dict[str, Any]
    thought: str | None = None
    requires_confirmation: bool = False
    is_final: bool = False
    raw_response: str = ""
    tool_call_id: str | None = None


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

    _SKILL_CREATOR_TRIGGER_RE = re.compile(
        r"("
        r"\b(create|write|design|build|generate|make|improve|optimi[sz]e|modify|edit)\b[\s\S]{0,80}\b(skill|skills|skill\.md)\b"
        r"|\b(skill|skills|skill\.md)\b[\s\S]{0,80}\b(create|write|design|build|generate|make|improve|optimi[sz]e|modify|edit)\b"
        r"|创建[\s\S]{0,40}(技能|skill|skills|SKILL\.md)"
        r"|写[\s\S]{0,40}(技能|skill|skills|SKILL\.md)"
        r"|设计[\s\S]{0,40}(技能|skill|skills|SKILL\.md)"
        r"|优化[\s\S]{0,40}(技能|skill|skills|SKILL\.md)"
        r"|改进[\s\S]{0,40}(技能|skill|skills|SKILL\.md)"
        r")",
        re.IGNORECASE,
    )

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
        guardrails: Guardrails | None = None,
        working_dir: str | None = None,
        hook_manager: HookManager | None = None,
        audit_logger: Any | None = None,
        cancellation_token: CancellationToken | None = None,
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
        self.context_manager = (
            context_manager if context_manager is not None else ContextManager(model=llm.model)
        )
        self.working_memory = working_memory
        self.guardrails = guardrails
        self.working_dir = working_dir
        self.hook_manager = hook_manager
        self.audit_logger = audit_logger
        self.cancellation_token = cancellation_token or CancellationToken()
        self.on_thought: Callable | None = None
        self.on_action: Callable | None = None
        self.on_result: Callable | None = None
        self.on_stream: Callable | None = None
        self.on_tool_event: Callable[[ToolEvent], None] | None = None
        self._current_tool_context: ToolUseContext | None = None
        self._errors: list[str] = []
        self._tool_event_sequence = 0
        self._skill_listing_sent: bool = False
        self._skill_routed: bool = False
        self._project_init_routed: bool = False
        self._active_skill_allowed_tools: set[str] | None = None
        self._active_skill_model: str | None = None
        self._base_model: str = getattr(llm, "model", "")
        self._workflow_resolved: bool = True
        self._workflow_decision: WorkflowDecision | None = None
        self._workflow_routing_error: str | None = None

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
        """Add a message to the context and fail loudly on capacity rejection."""
        result = self.context_manager.add_message(message)
        if isinstance(result, MessageAddResult) and not result:
            raise ContextCapacityError(result.reason or "Message did not fit in context")

    async def run(
        self,
        task: str,
        on_thought: Callable | None = None,
        on_action: Callable | None = None,
        on_result: Callable | None = None,
        on_stream: Callable | None = None,
        on_tool_event: Callable[[ToolEvent], None] | None = None,
        preserve_plan_state: bool = False,
        preserve_context: bool = False,
        route_workflow: bool = False,
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

        if preserve_plan_state:
            self.state.reset_execution(task)
        else:
            self.state.reset(task)
        self.active_run_id = self.state.run_id or uuid.uuid4().hex
        self.on_thought = on_thought
        self.on_action = on_action
        self.on_result = on_result
        self.on_stream = on_stream
        self.on_tool_event = on_tool_event
        self._errors = []
        self._tool_event_sequence = 0
        self._workflow_resolved = not route_workflow
        self._workflow_decision = WorkflowDecision.ACT if not route_workflow else None
        self._workflow_routing_error = None

        self._upsert_runtime_system_prompt()

        self._inject_skill_listing()

        self.add_message(Message(role="user", content=f"Task: {task}"))
        self._report_progress(activity=f"Started task: {task}")
        pending_routed_action: ParsedAction | None = None
        if route_workflow:
            workflow = await self._resolve_workflow(task)
            if workflow.decision == WorkflowDecision.PLAN:
                pending_routed_action = ParsedAction(
                    tool_name="enter_plan_mode",
                    arguments={},
                    thought=workflow.reason
                    or "The user wants a reviewable plan before implementation.",
                )
            elif not workflow.resolved:
                self.add_message(
                    Message(
                        role="user",
                        content=(
                            "OpenNova could not resolve the execution workflow for this turn. "
                            "You may answer, inspect, search, or ask for clarification, but project "
                            "modifications are blocked until the workflow is resolved. You may call "
                            "enter_plan_mode if planning is the safe choice."
                        ),
                    )
                )

        if pending_routed_action is None and self._workflow_resolved:
            pending_routed_action = self._route_task_to_project_init(task)
            if pending_routed_action is None:
                pending_routed_action = self._route_task_to_skill(task)

        try:
            while (
                not self.state.is_complete
                and self.state.run_id == self.active_run_id
                and self.state.iteration < self.max_iterations
                and not self.state.has_too_many_errors()
            ):
                self._emit_iteration_start()
                self.state.increment_iteration(self.active_run_id)

                try:
                    if pending_routed_action:
                        actions = [pending_routed_action]
                        pending_routed_action = None
                        response = LLMResponse(
                            content=actions[0].thought or "",
                            finish_reason=FinishReason.TOOL_CALL,
                        )
                    else:
                        response = await self._think()
                        actions = self._parse_actions(response, task)

                    if actions[0].is_final and self._plan_submission_required():
                        if response.content:
                            self.add_message(
                                Message(
                                    role="assistant",
                                    content=response.content,
                                    reasoning_content=response.reasoning_content,
                                )
                            )
                        self.add_message(
                            Message(
                                role="user",
                                content=(
                                    "Plan mode is active. Do not finish with plan text alone. "
                                    "Continue research or call exit_plan_mode with a complete "
                                    "structured plan so the user can review it."
                                ),
                            )
                        )
                        continue

                    if actions[0].is_final:
                        self.state.mark_complete(
                            actions[0].thought or response.content or "",
                            run_id=self.active_run_id,
                        )
                        self._report_progress(activity="Completed task", mark_complete=True)
                        break

                    completed_actions: list[ParsedAction] = []
                    completed_results: list[ToolResult] = []
                    barrier_index = self._first_batch_barrier_index(actions)
                    usage_reported = False
                    for action_index, action in enumerate(actions):
                        if barrier_index is not None and action_index != barrier_index:
                            completed_actions.append(action)
                            completed_results.append(
                                self._deferred_batch_result(action, actions[barrier_index])
                            )
                            continue

                        if not action.tool_name or action.tool_name not in self.tool_registry:
                            observation = Message(
                                role="user",
                                content="Please use an available tool or skill to complete the task. "
                                "Available tools: " + ", ".join(self._available_tool_names()),
                            )
                            self.add_message(observation)
                            continue

                        tool_context = self._start_tool_context(action)
                        try:
                            self._emit_tool_event(
                                ToolEvent(
                                    type="tool_start",
                                    tool_id=tool_context.tool_id,
                                    tool_name=action.tool_name,
                                    arguments=dict(tool_context.arguments),
                                    started_at=tool_context.started_at,
                                    risk_level=tool_context.risk_level,
                                )
                            )
                            if self.on_action:
                                with suppress(Exception):
                                    self.on_action(action.tool_name, tool_context.arguments)

                            self._report_progress(
                                activity=f"Running tool: {action.tool_name}",
                                last_tool_name=action.tool_name,
                            )
                            result = await self._act(action)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            result = ToolResult(
                                success=False,
                                output="",
                                error=f"Tool execution callback failed: {exc}",
                            )
                        result = self._redact_tool_result_for_observation(result)
                        self._finish_tool_context(result)
                        if self.state.run_id != self.active_run_id:
                            break

                        if self.on_result:
                            with suppress(Exception):
                                self.on_result(result)

                        self._report_progress(
                            activity=f"Completed tool: {action.tool_name}",
                            last_tool_name=action.tool_name,
                            tool_use_increment=1,
                            token_count=(
                                response.usage.total_tokens
                                if response.usage and not usage_reported
                                else 0
                            ),
                        )
                        usage_reported = True
                        completed_actions.append(action)
                        completed_results.append(result)

                    if completed_actions:
                        await self._observe_many(
                            completed_actions,
                            completed_results,
                            response.reasoning_content,
                        )

                except Exception as e:
                    self.state.increment_error(self.active_run_id)
                    error_detail = self._redacted_text(
                        f"Error in iteration {self.state.iteration}: {type(e).__name__}: {e}"
                    )
                    tb = self._redacted_text(traceback.format_exc())
                    full_error = f"{error_detail}\n\nTraceback:\n{tb}"
                    self._errors.append(full_error)
                    print(f"\n[ERROR] {full_error}\n")
                    self.add_message(
                        Message(
                            role="user",
                            content=f"An error occurred: {error_detail}. Please try a different approach.",
                        )
                    )
        except asyncio.CancelledError:
            self.cancellation_token.cancel("Run cancelled")
            self.state.cancel_run(self.active_run_id)
            self._cancel_tool_context(self.cancellation_token.reason)
            raise
        finally:
            self._clear_skill_execution_context()

        if self.state.iteration >= self.max_iterations:
            return f"Task incomplete: reached maximum iterations ({self.max_iterations})"

        if self.state.has_too_many_errors():
            error_summary = "\n\n".join(self._errors)
            return f"Task failed: too many errors ({self.state.error_count})\n\nDetailed errors:\n{error_summary}"

        return self.state.last_result or "Task completed"

    @staticmethod
    def _first_batch_barrier_index(actions: list[ParsedAction]) -> int | None:
        """Return the first action that must execute alone in its model turn."""
        if len(actions) <= 1:
            return None
        return next(
            (
                index
                for index, action in enumerate(actions)
                if action.tool_name in BATCH_BARRIER_TOOLS
            ),
            None,
        )

    @staticmethod
    def _deferred_batch_result(action: ParsedAction, barrier: ParsedAction) -> ToolResult:
        """Explain why a model-emitted call was not executed across a batch barrier."""
        return ToolResult(
            success=False,
            output="",
            error=(
                f"Tool call '{action.tool_name}' was not executed because "
                f"'{barrier.tool_name}' must execute alone. Reconsider this call after "
                "observing the updated skill, user response, or workflow state."
            ),
            metadata={
                "batch_deferred": True,
                "barrier_tool": barrier.tool_name,
            },
        )

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
        with suppress(Exception):
            self.progress_callback(payload)

    def _emit_iteration_start(self) -> None:
        """Notify listeners before a new iteration begins."""
        if self.iteration_start_callback:
            self.iteration_start_callback(self.messages)

    def _start_tool_context(self, action: ParsedAction) -> ToolUseContext:
        """Create and store canonical context for the current tool call."""
        tool = self.tool_registry.get(action.tool_name)
        self._tool_event_sequence += 1
        run_id = getattr(self, "active_run_id", None) or uuid.uuid4().hex
        tool_id = f"tool_{run_id}_{self._tool_event_sequence:04d}"
        max_result_chars = getattr(tool, "max_result_chars", None)
        self._current_tool_context = ToolUseContext(
            tool_id=tool_id,
            tool_name=action.tool_name,
            arguments=self._redacted_arguments(action.arguments),
            started_at=perf_counter(),
            max_result_chars=max_result_chars,
            abort_signal=self.cancellation_token,
        )
        return self._current_tool_context

    def _redaction_enabled(self) -> bool:
        """Return whether tool observations should be sanitized before persistence."""
        guardrails = getattr(self, "guardrails", None)
        if not guardrails:
            return False
        policy = guardrails.secrets_policy
        return bool(policy.get("enabled", True)) and bool(policy.get("redact_tool_outputs", True))

    def _redacted_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Build an event-safe argument copy without changing execution inputs."""
        guardrails = getattr(self, "guardrails", None)
        if not self._redaction_enabled() or not guardrails:
            return dict(arguments)
        redacted = redact_sensitive_data(
            arguments,
            scanner=guardrails.secret_scanner,
        )
        return redacted if isinstance(redacted, dict) else {}

    def _redacted_text(self, text: str) -> str:
        """Sanitize one log or observation string under the active secret policy."""
        guardrails = getattr(self, "guardrails", None)
        if not self._redaction_enabled() or not guardrails:
            return text
        return str(guardrails.secret_scanner.redact(text))

    def _redact_tool_result_for_observation(self, result: ToolResult) -> ToolResult:
        """Prevent tool-produced secrets from reaching events, memory, or transcripts."""
        guardrails = getattr(self, "guardrails", None)
        if not self._redaction_enabled() or not guardrails:
            return result
        result.output = self._redacted_text(result.output or "")
        if result.error:
            result.error = self._redacted_text(result.error)
        redacted_metadata = redact_sensitive_data(
            result.metadata,
            scanner=guardrails.secret_scanner,
        )
        if isinstance(redacted_metadata, dict):
            result.metadata = redacted_metadata
        return result

    def _finish_tool_context(self, result: ToolResult) -> None:
        """Emit the final canonical event for a tool invocation."""
        context = self._current_tool_context
        if not context:
            return
        elapsed = max(0.0, perf_counter() - context.started_at)
        output = result.output or ""
        diff = result.metadata.get("diff") if isinstance(result.metadata, dict) else None
        risk_level = str(
            result.metadata.get("risk_level", context.risk_level)
            if isinstance(result.metadata, dict)
            else context.risk_level
        )
        event_type: ToolEventType = (
            "tool_cancelled"
            if result.metadata.get("cancelled")
            else "tool_result"
            if result.success
            else "tool_error"
        )
        event = ToolEvent(
            type=event_type,
            tool_id=context.tool_id,
            tool_name=context.tool_name,
            arguments=dict(context.arguments),
            started_at=context.started_at,
            duration_ms=int(elapsed * 1000),
            risk_level=risk_level,
            success=result.success,
            output=output,
            error=result.error,
            diff=diff,
            collapsible=len(output) > 1200,
            metadata=dict(result.metadata or {}),
        )
        result.metadata.setdefault("tool_id", context.tool_id)
        result.metadata.setdefault("duration_ms", int(elapsed * 1000))
        self._current_tool_context = None
        self._emit_tool_event(event)

    def _cancel_tool_context(self, reason: str) -> None:
        """Emit one terminal cancellation event for the active tool."""
        context = self._current_tool_context
        if context is None:
            return
        elapsed = max(0.0, perf_counter() - context.started_at)
        event = ToolEvent(
            type="tool_cancelled",
            tool_id=context.tool_id,
            tool_name=context.tool_name,
            arguments=dict(context.arguments),
            started_at=context.started_at,
            duration_ms=int(elapsed * 1000),
            risk_level=context.risk_level,
            success=False,
            error=reason,
            metadata={"cancelled": True},
        )
        self._current_tool_context = None
        self._emit_tool_event(event)

    def _emit_tool_event(self, event: ToolEvent) -> None:
        if self.on_tool_event:
            with suppress(Exception):
                self.on_tool_event(event)

    async def _think(self) -> LLMResponse:
        """
        Think step: Get LLM response.

        Returns:
            LLM response with potential tool calls
        """
        self._upsert_runtime_system_prompt()
        tools = self._available_tools()

        if self.stream and self.on_stream:
            full_content = ""
            tool_calls: list[ToolCall] = []
            usage = None
            reasoning_content: str | None = None

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
                if chunk.reasoning_content:
                    if reasoning_content is None:
                        reasoning_content = chunk.reasoning_content
                    else:
                        reasoning_content += chunk.reasoning_content

            return LLMResponse(
                content=full_content,
                tool_calls=tool_calls if tool_calls else None,
                usage=usage,
                finish_reason=FinishReason.TOOL_CALL if tool_calls else FinishReason.STOP,
                model=self.llm.model,
                reasoning_content=reasoning_content,
            )
        else:
            response = await self.llm.chat(
                self.context_manager.get_messages_for_llm(),
                tools=tools,
                temperature=0.7,
            )
            return response

    def _available_tools(self) -> list[Any]:
        """Return the currently allowed tool schemas."""
        schemas = self.tool_registry.list_tools()
        mode = getattr(getattr(self.state, "mode", None), "value", getattr(self.state, "mode", ""))
        if not self._workflow_resolved or mode == "plan":
            schemas = [
                schema for schema in schemas if schema.name not in PLAN_MODE_IMPLEMENTATION_TOOLS
            ]
        if not self._active_skill_allowed_tools:
            return schemas
        return [schema for schema in schemas if schema.name in self._active_skill_allowed_tools]

    async def _resolve_workflow(self, task: str) -> WorkflowRoutingResult:
        """Resolve and retain the model-selected workflow for this turn."""
        result = await WorkflowRouter(self.llm).route(
            self.context_manager.get_messages_for_llm(),
            task,
        )
        self._workflow_resolved = result.resolved
        self._workflow_decision = result.decision
        self._workflow_routing_error = result.error
        self._upsert_runtime_system_prompt()
        return result

    def _plan_submission_required(self) -> bool:
        """Return whether plan mode still requires a structured exit tool call."""
        mode = getattr(getattr(self.state, "mode", None), "value", getattr(self.state, "mode", ""))
        approval = getattr(
            getattr(self.state, "plan_approval_status", None),
            "value",
            getattr(self.state, "plan_approval_status", ""),
        )
        return mode == "plan" and approval not in {
            "awaiting_approval",
            "approved",
            "executing",
            "completed",
        }

    def _available_tool_names(self) -> list[str]:
        return [schema.name for schema in self._available_tools()]

    def _parse_response(self, response: LLMResponse, task: str = "") -> ParsedAction:
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

        if (
            response.finish_reason == FinishReason.STOP
            and not response.tool_calls
            and not action.tool_name
        ):
            routed_action = self._route_task_to_project_init(task)
            if not routed_action:
                routed_action = self._route_task_to_skill(task)
            if routed_action:
                return routed_action
            action.is_final = True

        return action

    def _parse_actions(self, response: LLMResponse, task: str = "") -> list[ParsedAction]:
        """Parse every tool call in one model response without dropping later calls."""
        if not response.tool_calls:
            return [self._parse_response(response, task)]

        actions: list[ParsedAction] = []
        for tool_call in response.tool_calls:
            action = ParsedAction(
                tool_name=tool_call.name,
                arguments=tool_call.arguments or {},
                thought=response.content or "",
                raw_response=response.content or "",
                tool_call_id=tool_call.id,
            )
            if self._is_dangerous_action(action.tool_name, action.arguments):
                action.requires_confirmation = True
            actions.append(action)
        return actions

    def _route_task_to_project_init(self, task: str) -> ParsedAction | None:
        """Route obvious project-initialization requests to init_project_guide."""
        if self._project_init_routed:
            return None
        if "init_project_guide" not in self.tool_registry:
            return None

        from opennova.memory.project_guide import ProjectGuideManager

        guide_manager = ProjectGuideManager(project_path=".")
        if guide_manager.exists():
            return None
        if not guide_manager.is_high_confidence_init_request(task):
            return None

        self._project_init_routed = True
        return ParsedAction(
            tool_name="init_project_guide",
            arguments={"force": False},
            thought=(
                "The user asked to initialize project onboarding context, "
                "so I will create OPENNOVA.md first."
            ),
        )

    def _route_task_to_skill(self, task: str) -> ParsedAction | None:
        """Route obvious natural-language skill requests before accepting prose answers."""
        if self._skill_routed:
            return None
        if "skill" not in self.tool_registry:
            return None
        if not self.skill_registry or not self.skill_registry.can_model_invoke("skill-creator"):
            return None
        if not self._is_skill_creator_request(task):
            return None

        self._skill_routed = True

        return ParsedAction(
            tool_name="skill",
            arguments={"skill": "skill-creator", "args": task},
            thought="The user's request is to create or improve a skill, so I will invoke skill-creator first.",
        )

    def _is_skill_creator_request(self, task: str) -> bool:
        """Return whether a task is a high-confidence request for skill-creator."""
        return bool(self._SKILL_CREATOR_TRIGGER_RE.search(task))

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
            pass

        observed_paths: list[str] = []
        file_path = None
        if isinstance(result.metadata, dict):
            file_path = result.metadata.get("file_path")
            if isinstance(file_path, str):
                observed_paths.append(file_path)
            directory = result.metadata.get("directory")
            if isinstance(directory, str):
                observed_paths.append(directory)
        argument_path = action.arguments.get("file_path")
        if isinstance(argument_path, str):
            observed_paths.append(argument_path)
        argument_directory = action.arguments.get("directory")
        if isinstance(argument_directory, str):
            observed_paths.append(argument_directory)

        change_types = {
            "read_file": "read",
            "write_file": "modified",
            "create_file": "created",
            "delete_file": "deleted",
        }
        change_type = change_types.get(action.tool_name)
        if file_path and change_type and self.working_memory and result.success:
            preview = (result.output or result.error or "")[:200] or None
            self.working_memory.observe_file(file_path, change_type, preview)

        if self.skill_registry and observed_paths:
            cwd = self.working_dir or os.getcwd()
            self.skill_registry.discover_for_paths(observed_paths, cwd)
            self.skill_registry.activate_for_paths(observed_paths, cwd)

    async def _act(self, action: ParsedAction) -> ToolResult:
        """
        Act step: Execute a tool.

        Args:
            action: Action to execute

        Returns:
            Tool execution result
        """
        tool = self.tool_registry.get(action.tool_name)
        self.cancellation_token.raise_if_cancelled()
        action.arguments = self._normalize_tool_arguments(tool, action.arguments)
        started_at = perf_counter()
        guard_result: GuardResult | None = None
        checkpoint_metadata: dict[str, Any] = {}
        action_record = None
        context_token = set_current_tool_context(self._current_tool_context)
        if self.working_memory:
            action_record = self.working_memory.record_action(action.tool_name, action.arguments)

        try:
            if self.hook_manager:
                hook_event = {
                    "tool_name": action.tool_name,
                    "arguments": dict(action.arguments),
                    "metadata": {},
                }
                hook_result = self.hook_manager.run_pre_tool_use(hook_event)
                if isinstance(hook_result, ToolResult):
                    return hook_result
                action.arguments = dict(hook_result.get("arguments", action.arguments))
                action.arguments = self._normalize_tool_arguments(tool, action.arguments)

            guard_result = self._check_tool_guard(action)
            if not guard_result.allowed:
                if self.working_memory and action_record:
                    self.working_memory.update_action(
                        action_record.id,
                        ActionStatus.FAILED,
                        error=guard_result.reason,
                    )
                blocked_result = ToolResult(
                    success=False,
                    output="",
                    error=guard_result.reason,
                    metadata={
                        "guard_blocked": True,
                        "risk_level": guard_result.risk_level.value,
                        "requires_confirmation": guard_result.requires_confirmation,
                        "suggestions": guard_result.suggestions,
                        **guard_result.metadata,
                    },
                )
                self._audit_tool_action(
                    action,
                    guard_result,
                    blocked_result,
                    confirmation_outcome="blocked",
                    checkpoint_metadata=checkpoint_metadata,
                    started_at=started_at,
                )
                return blocked_result

            if guard_result.requires_confirmation:
                confirm_result = await self._confirm_warn_action(action, guard_result)
                if not confirm_result.success:
                    if self.working_memory and action_record:
                        self.working_memory.update_action(
                            action_record.id,
                            ActionStatus.FAILED,
                            error=confirm_result.error or "User declined action",
                        )
                    self._audit_tool_action(
                        action,
                        guard_result,
                        confirm_result,
                        confirmation_outcome="declined",
                        checkpoint_metadata=checkpoint_metadata,
                        started_at=started_at,
                    )
                    return confirm_result
                confirmation_outcome = "confirmed"
            else:
                confirmation_outcome = None

            checkpoint_metadata = self._create_checkpoint_for_action(action)
            if hasattr(tool, "async_execute"):
                result = await tool.async_execute(**action.arguments)
            else:
                result = tool.execute(**action.arguments)
            if not isinstance(result, ToolResult):
                raise TypeError(
                    f"Tool '{action.tool_name}' returned {type(result).__name__}, expected ToolResult"
                )
            if checkpoint_metadata:
                result.metadata.update(checkpoint_metadata)

            if result.success and result.metadata.get("interaction_required"):
                result = await self._resolve_interaction(result)
            if self.hook_manager:
                hook_result = self.hook_manager.run_post_tool_use(
                    {
                        "tool_name": action.tool_name,
                        "arguments": dict(action.arguments),
                        "result": result,
                        "metadata": {},
                    }
                )
                if isinstance(hook_result, ToolResult):
                    result = hook_result
                elif isinstance(hook_result, dict) and isinstance(
                    hook_result.get("result"), ToolResult
                ):
                    result = hook_result["result"]
            if not isinstance(result, ToolResult):
                raise TypeError(
                    f"Post-tool hook for '{action.tool_name}' returned an invalid result"
                )
            result = self._redact_tool_result_for_observation(result)
            if self.working_memory and action_record:
                status = ActionStatus.SUCCESS if result.success else ActionStatus.FAILED
                self.working_memory.update_action(
                    action_record.id,
                    status,
                    result=result.output,
                    error=result.error,
                )
                self._record_file_observation(action, result)
            self._audit_tool_action(
                action,
                guard_result,
                result,
                confirmation_outcome=confirmation_outcome,
                checkpoint_metadata=checkpoint_metadata,
                started_at=started_at,
            )
            return result
        except Exception as e:
            if self.working_memory and action_record:
                self.working_memory.update_action(
                    action_record.id,
                    ActionStatus.FAILED,
                    error=str(e),
                )
            error_result = ToolResult(
                success=False,
                output="",
                error=f"Tool execution failed: {e}",
            )
            self._audit_tool_action(
                action,
                guard_result,
                error_result,
                confirmation_outcome="error",
                checkpoint_metadata=checkpoint_metadata,
                started_at=started_at,
            )
            return error_result
        finally:
            reset_current_tool_context(context_token)

    def _audit_tool_action(
        self,
        action: ParsedAction,
        guard_result: GuardResult | None,
        result: ToolResult,
        *,
        confirmation_outcome: str | None,
        checkpoint_metadata: dict[str, Any],
        started_at: float,
    ) -> None:
        """Best-effort security audit event emission."""
        if not self.audit_logger:
            return
        checkpoint_id = None
        if checkpoint_metadata:
            checkpoint_id = checkpoint_metadata.get("checkpoint_id")
        self.audit_logger.log_tool_event(
            tool_name=action.tool_name,
            arguments=dict(action.arguments),
            guard_result=guard_result,
            result=result,
            confirmation_outcome=confirmation_outcome,
            checkpoint_id=checkpoint_id,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
        )

    def _create_checkpoint_for_action(self, action: ParsedAction) -> dict[str, Any]:
        """Create a best-effort checkpoint before destructive file mutations."""
        if action.tool_name not in {"write_file", "edit_file", "multi_edit_file", "delete_file"}:
            return {}

        file_path = action.arguments.get("file_path")
        if not file_path:
            return {}

        try:
            from pathlib import Path

            from opennova.checkpoints import CheckpointManager

            project_path = Path(self.working_dir or ".").resolve()
            target = Path(file_path).expanduser().resolve()
            if not target.exists():
                return {"checkpoint_warning": f"No existing file to checkpoint: {file_path}"}
            checkpoint_id = CheckpointManager(project_path).create(
                f"Before {action.tool_name}",
                [target],
            )
            return {"checkpoint_id": checkpoint_id}
        except Exception as exc:
            return {"checkpoint_warning": str(exc)}

    def _normalize_tool_arguments(
        self,
        tool: Any,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Let tools normalize common model argument variants before guards execute."""
        normalizer = getattr(tool, "normalize_arguments", None)
        if not callable(normalizer):
            return arguments
        normalized = normalizer(arguments)
        return normalized if isinstance(normalized, dict) else arguments

    def _check_tool_guard(self, action: ParsedAction) -> GuardResult:
        """Run guardrails for a pending tool action."""
        if (
            self._active_skill_allowed_tools is not None
            and action.tool_name not in self._active_skill_allowed_tools
        ):
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason=(
                    f"Tool '{action.tool_name}' is not allowed by the currently active skill. "
                    f"Allowed tools: {', '.join(sorted(self._active_skill_allowed_tools))}"
                ),
            )
        if not self._workflow_resolved and action.tool_name in PLAN_MODE_IMPLEMENTATION_TOOLS:
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason=(
                    f"Tool '{action.tool_name}' is blocked because the execution workflow "
                    "has not been resolved for this turn."
                ),
                requires_confirmation=False,
                suggestions=[
                    "Answer without modifying files, continue inspecting, or call enter_plan_mode."
                ],
                metadata={"workflow_unresolved": True},
            )
        if (
            self._workflow_resolved
            and self._workflow_decision == WorkflowDecision.ACT
            and action.tool_name == "enter_plan_mode"
        ):
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason="Plan mode is disabled for this explicitly direct execution turn.",
                requires_confirmation=False,
                metadata={"workflow_decision": WorkflowDecision.ACT.value},
            )
        if (
            self._workflow_decision == WorkflowDecision.PLAN
            and action.tool_name in PLAN_MODE_IMPLEMENTATION_TOOLS
        ):
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason=f"Tool '{action.tool_name}' is blocked until the plan is approved.",
                requires_confirmation=False,
                metadata={"workflow_decision": WorkflowDecision.PLAN.value},
            )
        plan_mode = getattr(
            getattr(self.state, "mode", None), "value", getattr(self.state, "mode", "")
        )
        if plan_mode == "plan" and action.tool_name in PLAN_MODE_IMPLEMENTATION_TOOLS:
            return GuardResult(
                allowed=False,
                risk_level=RiskLevel.BLOCK,
                reason=(
                    f"Tool '{action.tool_name}' is blocked in plan mode. "
                    "Continue researching with read/search tools, then call exit_plan_mode "
                    "with a concrete plan and wait for user approval before implementation."
                ),
                requires_confirmation=False,
                suggestions=[
                    "Use read_file, list_directory, glob_files, grep_code, or ask_user_question to finish the plan.",
                    "Call exit_plan_mode with the proposed plan before modifying files.",
                ],
                metadata={"plan_mode_blocked": True},
            )
        if not self.guardrails:
            return GuardResult(
                allowed=True,
                risk_level=RiskLevel.SAFE,
                reason="Guardrails disabled",
                requires_confirmation=False,
            )
        tool = self.tool_registry.get(action.tool_name)
        tool_context_provider = getattr(tool, "get_security_context", None)
        tool_context = tool_context_provider() if callable(tool_context_provider) else None
        return self.guardrails.check_tool_call(
            action.tool_name,
            action.arguments,
            working_dir=self.working_dir,
            tool_context=tool_context,
        )

    async def _confirm_warn_action(
        self, action: ParsedAction, guard_result: GuardResult
    ) -> ToolResult:
        """Request user confirmation for WARN-level operations."""
        prompt_result = ToolResult(
            success=True,
            output=(
                f"Confirmation required: {guard_result.reason}\n"
                "Proceed only if this is intentional."
            ),
            metadata={
                "interaction_required": True,
                "interaction_type": "ask_user_question",
                "questions": [
                    {
                        "question": (
                            f"{guard_result.reason}\n"
                            f"Tool: {action.tool_name}\n"
                            "Do you want to proceed?"
                        ),
                        "header": "Confirm",
                        "options": [
                            {
                                "index": 1,
                                "label": "Proceed",
                                "description": "Execute this action now.",
                            },
                            {
                                "index": 2,
                                "label": "Cancel",
                                "description": "Skip this action and continue safely.",
                            },
                        ],
                        "multiSelect": False,
                        "free_text": False,
                        "allow_custom_answer": False,
                    }
                ],
                "prompt_payload": {
                    "question": (
                        f"{guard_result.reason}\nTool: {action.tool_name}\nDo you want to proceed?"
                    ),
                    "header": "Confirm",
                    "options": [
                        {"index": 1, "label": "Proceed", "description": "Execute this action now."},
                        {
                            "index": 2,
                            "label": "Cancel",
                            "description": "Skip this action and continue safely.",
                        },
                    ],
                    "multi_select": False,
                    "free_text": False,
                    "allow_custom_answer": False,
                },
            },
        )
        if self._current_tool_context:
            self._emit_tool_event(
                ToolEvent(
                    type="permission_request",
                    tool_id=self._current_tool_context.tool_id,
                    tool_name=action.tool_name,
                    arguments=dict(action.arguments),
                    started_at=self._current_tool_context.started_at,
                    risk_level=guard_result.risk_level.value,
                    metadata={
                        "reason": guard_result.reason,
                        "suggestions": guard_result.suggestions,
                    },
                )
            )
        resolved = await self._resolve_interaction(prompt_result)
        if not resolved.success:
            return ToolResult(
                success=False,
                output="",
                error=resolved.error or "Confirmation failed",
                metadata={**resolved.metadata, "guard_confirmation_failed": True},
            )

        all_answers = resolved.metadata.get("all_answers", [])
        selected_answer = ""
        if all_answers:
            selected_answer = str(all_answers[0].get("answer") or "")
        if not selected_answer:
            selected_answer = str(resolved.metadata.get("answer") or "")
        if selected_answer.strip().lower() not in {"proceed", "yes", "y", "1"}:
            return ToolResult(
                success=False,
                output="Action cancelled by user confirmation policy.",
                error="User declined confirmation",
                metadata={"guard_confirmation_declined": True},
            )

        return ToolResult(success=True, output="User confirmed action")

    async def _resolve_interaction(self, result: ToolResult) -> ToolResult:
        """Resolve an interactive tool result through the registered runtime callback."""
        self.state.begin_interaction("tool_confirmation")
        if not self.interaction_callback:
            self.state.end_interaction()
            return ToolResult(
                success=False,
                output=result.output,
                error="Interactive response required but no interaction handler is available.",
                metadata={**result.metadata, "interaction_unresolved": True},
            )

        try:
            try:
                interaction_result = self.interaction_callback(result.metadata)
                if asyncio.iscoroutine(interaction_result):
                    interaction_result = await interaction_result
            except Exception as e:
                return ToolResult(
                    success=False,
                    output=result.output,
                    error=f"Interaction failed: {e}",
                    metadata={**result.metadata, "interaction_unresolved": True},
                )
        finally:
            self.state.end_interaction()

        if not isinstance(interaction_result, dict):
            return ToolResult(
                success=False,
                output=result.output,
                error=f"Interaction callback returned unexpected type: {type(interaction_result).__name__}",
                metadata={**result.metadata, "interaction_unresolved": True},
            )

        all_answers = interaction_result.get("all_answers", [])
        skipped = interaction_result.get("skipped", False)

        # Legacy callback format (single question, no all_answers)
        if not all_answers:
            prompt_payload = result.metadata.get("prompt_payload", {})
            question = prompt_payload.get("question", "")
            if skipped:
                return ToolResult(
                    success=True,
                    output=f"Question: {question}\n"
                    "User did not provide an answer. Please make the best decision.",
                    metadata={
                        **result.metadata,
                        "interaction_required": False,
                        "skipped": True,
                        "skipped_question": question,
                    },
                )
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

        # New multi-question format
        if skipped and all(a.get("skipped") for a in all_answers):
            questions = result.metadata.get("questions", [])
            first_q = questions[0].get("question", "") if questions else ""
            return ToolResult(
                success=True,
                output=f"Question: {first_q}\n"
                "User did not provide an answer. Please make the best decision.",
                metadata={
                    **result.metadata,
                    "interaction_required": False,
                    "skipped": True,
                    "skipped_question": first_q,
                },
            )

        # Build Claude Code-style output: 'User has answered your questions: "q"="a". ...'
        answer_parts = [
            f'"{a.get("question", "")}"="{a.get("answer", "(skipped)")}"' for a in all_answers
        ]
        output = (
            f"User has answered your questions: {'; '.join(answer_parts)}. "
            "You can now continue with the user's answers in mind."
        )

        return ToolResult(
            success=True,
            output=output,
            metadata={
                **result.metadata,
                "interaction_required": False,
                "answers": interaction_result.get("answers", {}),
                "all_answers": all_answers,
                "display": interaction_result.get("display", ""),
            },
        )

    async def _observe(
        self, action: ParsedAction, result: ToolResult, reasoning_content: str | None = None
    ) -> None:
        """Observe one tool result while preserving the legacy helper API."""
        await self._observe_many([action], [result], reasoning_content)

    async def _observe_many(
        self,
        actions: list[ParsedAction],
        results: list[ToolResult],
        reasoning_content: str | None = None,
    ) -> None:
        """
        Observe all tool results from one assistant response as one protocol turn.

        Args:
            actions: Actions emitted by the assistant in one response
            results: Results corresponding to actions by position
            reasoning_content: Optional reasoning content from the LLM (DeepSeek thinking mode)
        """
        if len(actions) != len(results):
            raise ValueError("Actions and results must have the same length")

        tool_calls = [
            ToolCall(
                id=getattr(action, "tool_call_id", None) or f"call_{self.state.iteration}_{index}",
                name=action.tool_name,
                arguments=self._redacted_arguments(action.arguments),
            )
            for index, action in enumerate(actions, start=1)
        ]
        assistant_msg = Message(
            role="assistant",
            content=actions[0].thought or "",
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )
        protocol_messages = [assistant_msg]
        for action, result, tool_call in zip(actions, results, tool_calls, strict=True):
            protocol_messages.append(
                Message(
                    role="tool",
                    content=result.to_string(),
                    tool_call_id=tool_call.id,
                    name=action.tool_name,
                )
            )

        add_group = getattr(self.context_manager, "add_messages_and_compress", None)
        if callable(add_group):
            insertion = await add_group(protocol_messages)
            if isinstance(insertion, MessageAddResult) and not insertion:
                raise ContextCapacityError(insertion.reason or "Tool protocol group did not fit")
        else:
            # Compatibility path for lightweight context doubles.
            self.add_message(assistant_msg)
            for tool_message in protocol_messages[1:-1]:
                self.add_message(tool_message)
            await self.context_manager.add_message_and_compress(protocol_messages[-1])

        for action, result in zip(actions, results, strict=True):
            if not result.metadata.get("batch_deferred"):
                self._post_observation(action, result)

    def _post_observation(self, action: ParsedAction, result: ToolResult) -> None:
        """Apply result-specific state changes after a tool observation."""

        # When user skips a question, give the LLM explicit permission to decide.
        if result.metadata.get("skipped"):
            question = result.metadata.get("skipped_question", "")
            if question:
                self.add_message(
                    Message(
                        role="user",
                        content=f"I'll let you decide on this: {question}",
                    )
                )
        # For partially-skipped multi-question: tell LLM which were skipped.
        all_answers = result.metadata.get("all_answers", [])
        skipped_questions = [a for a in all_answers if a.get("skipped")]
        if skipped_questions and not result.metadata.get("skipped"):
            skipped_texts = [f'"{a.get("question", "")}"' for a in skipped_questions]
            self.add_message(
                Message(
                    role="user",
                    content=f"I'll let you decide on: {', '.join(skipped_texts)}",
                )
            )

        # For skill invocations, add the skill prompt as a user message
        # AFTER the tool result, matching Claude Code's message ordering.
        if action.tool_name == "skill" and result.success and "skill_prompt" in result.metadata:
            skill_name = result.metadata.get("resolved_skill") or result.metadata.get(
                "skill", "unknown"
            )
            skill_prompt = result.metadata["skill_prompt"]
            if self.skill_registry:
                self.skill_registry.record_skill_usage(skill_name)
            self._apply_skill_execution_context(result.metadata)
            if self.hook_manager and isinstance(result.metadata.get("hooks"), dict):
                register_skill_hooks(
                    self.hook_manager,
                    result.metadata["hooks"],
                    skill_name=skill_name,
                    skill_root=result.metadata.get("skill_dir"),
                )
            self.add_message(
                Message(
                    role="user",
                    content=f"Invoked skill '{skill_name}':\n\n{skill_prompt}",
                )
            )

        self.state.record_action_result(
            action.tool_name,
            result.output,
            run_id=getattr(self, "active_run_id", None),
        )
        if (
            action.tool_name == "exit_plan_mode"
            and result.success
            and result.metadata.get("status") == "awaiting_approval"
        ):
            self.state.mark_complete(
                result.output or "Plan ready for approval",
                run_id=getattr(self, "active_run_id", None),
            )
        elif action.tool_name == "enter_plan_mode" and result.success:
            self._workflow_resolved = True
            self._workflow_decision = WorkflowDecision.PLAN
            self._workflow_routing_error = None
            self._upsert_runtime_system_prompt()

    def _apply_skill_execution_context(self, metadata: dict[str, Any]) -> None:
        """Apply temporary tool/model constraints for the active skill."""
        allowed_tools = metadata.get("allowed_tools") or []
        self._active_skill_allowed_tools = set(allowed_tools) if allowed_tools else None

        model = str(metadata.get("model") or "").strip()
        if model:
            if self._active_skill_model is None:
                self._base_model = getattr(self.llm, "model", self._base_model)
            self._active_skill_model = model
            self.llm.model = model

    def _clear_skill_execution_context(self) -> None:
        """Restore baseline runtime state after skill-scoped execution."""
        self._active_skill_allowed_tools = None
        if self._active_skill_model is not None:
            self.llm.model = self._base_model
            self._active_skill_model = None

    def _build_system_prompt(self) -> str:
        """Build system prompt for the agent."""
        tools_description = []
        for schema in self._available_tools():
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

        prompt = f"""You are an AI coding assistant that helps users with software engineering tasks.

You have access to the following tools:
{chr(10).join(tools_description)}
"""

        # Include skill listing directly in the system prompt for reliable discovery.
        # Previously skills were only listed in a user-role message, which LLMs
        # treat as conversation history rather than authoritative instructions.
        if self.skill_registry:
            model_skills = self.skill_registry.list_model_invocable_skills()
            if model_skills:
                skill_entries: list[str] = []
                for name in model_skills[:20]:
                    skill = self.skill_registry.get_skill(name)
                    if skill is None:
                        continue
                    meta = skill.metadata
                    entry = f"- {meta.name}: {meta.description}"
                    if meta.when_to_use:
                        entry += f"\n  When to use: {meta.when_to_use}"
                    if meta.argument_hint:
                        entry += f"\n  Arguments: {meta.argument_hint}"
                    skill_entries.append(entry)

                prompt += f"""
In addition to tools, you have access to specialized skills. Each skill provides
domain-specific instructions that are loaded on invocation.

Available skills:
{chr(10).join(skill_entries)}

How to invoke a skill: call the Skill tool with skill="<skill-name>" and optional args.
Example: Skill("code_review", "src/main.py")

IMPORTANT: Skill invocation is a BLOCKING REQUIREMENT. When a listed skill matches
the user's request, invoke the Skill tool BEFORE generating any other response.
Do not mention a skill in prose without calling the Skill tool.
"""

        prompt += """
Use these tools and skills to complete the user's task. When you have completed the task,
provide a summary of what was done.

Rules:
1. Always explain what you are doing before executing a tool or skill
2. If a tool fails, try to understand the error and attempt a different approach
3. Be careful with file operations - read before write when modifying existing files
4. For multi-step implementation work, maintain explicit progress with todo/progress tracking
5. If you are executing an approved plan, follow the current plan instead of silently re-planning
6. If the user asks you to plan before coding, write a plan first, make a plan first,
   or otherwise requests approval before implementation, call enter_plan_mode before any implementation or file modification tool.
   Do not modify files before exit_plan_mode has requested user approval.
7. When the task is complete, provide a clear summary
"""
        return prompt

    def _upsert_runtime_system_prompt(self) -> None:
        """Keep exactly one current OpenNova runtime prompt at context position zero."""
        runtime_message = Message(
            role="system",
            content=self._build_system_prompt(),
            name=RUNTIME_SYSTEM_MESSAGE_NAME,
        )
        retained = [
            message
            for message in self.context_manager.messages
            if not (
                message.role == "system"
                and (
                    message.name == RUNTIME_SYSTEM_MESSAGE_NAME
                    or (
                        message.name is None
                        and message.content.startswith(LEGACY_RUNTIME_SYSTEM_PROMPT_PREFIX)
                    )
                )
            )
        ]
        self.context_manager.messages[:] = [runtime_message, *retained]

    def _is_dangerous_action(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Check if an action is potentially dangerous."""
        dangerous_tools = {"delete_file", "execute_command", "write_file"}
        return tool_name in dangerous_tools

    def _inject_skill_listing(self) -> None:
        """Inject the first-layer skill listing as a system-reminder message.

        Skills are now listed directly in the system prompt via _build_system_prompt(),
        so this separate user-message injection is skipped to avoid redundancy.
        """
        pass


async def run_simple_task(
    llm: BaseLLMProvider,
    tool_registry: ToolRegistry,
    task: str,
    max_iterations: int = 200,
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
    return await loop.run(task, on_stream=on_stream, route_workflow=True)
