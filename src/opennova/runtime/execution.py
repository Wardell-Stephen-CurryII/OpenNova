"""Canonical tool execution pipeline and bounded scheduler."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from opennova.memory.working import ActionStatus, WorkingMemory
from opennova.runtime.artifacts import ToolResultBudget
from opennova.runtime.cancellation import CancellationToken
from opennova.runtime.events import (
    ToolEvent,
    ToolEventType,
    ToolUseContext,
    reset_current_tool_context,
    set_current_tool_context,
)
from opennova.runtime.file_state import FileVersionCache
from opennova.security.guardrails import GuardResult
from opennova.tools.base import ToolRegistry, ToolResult


@dataclass
class ToolExecutionOutcome:
    """Result and context for one action, preserving model-call order."""

    action: Any
    result: ToolResult
    context: ToolUseContext


class ToolExecutionEngine:
    """Run every tool through one hook/security/checkpoint/audit pipeline."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        cancellation_token: CancellationToken,
        result_budget: ToolResultBudget,
        guard_checker: Callable[[Any], GuardResult],
        confirmation_handler: Callable[[Any, GuardResult, ToolUseContext], Awaitable[ToolResult]],
        interaction_handler: Callable[[ToolResult], Awaitable[ToolResult]],
        checkpoint_before: Callable[[Any, ToolUseContext], dict[str, Any]],
        checkpoint_after: Callable[[Any, ToolResult, dict[str, Any], ToolUseContext], None],
        audit_handler: Callable[..., None],
        result_redactor: Callable[[ToolResult], ToolResult],
        event_handler: Callable[[ToolEvent], None],
        argument_redactor: Callable[[dict[str, Any]], dict[str, Any]],
        hook_manager: Any | None = None,
        working_memory: WorkingMemory | None = None,
        file_observer: Callable[[Any, ToolResult], None] | None = None,
        session_id: str | None = None,
        run_id_provider: Callable[[], str] | None = None,
        parallel_limit: int = 4,
        file_cache: FileVersionCache | None = None,
    ) -> None:
        self.registry = registry
        self.cancellation_token = cancellation_token
        self.result_budget = result_budget
        self.guard_checker = guard_checker
        self.confirmation_handler = confirmation_handler
        self.interaction_handler = interaction_handler
        self.checkpoint_before = checkpoint_before
        self.checkpoint_after = checkpoint_after
        self.audit_handler = audit_handler
        self.result_redactor = result_redactor
        self.event_handler = event_handler
        self.argument_redactor = argument_redactor
        self.hook_manager = hook_manager
        self.working_memory = working_memory
        self.file_observer = file_observer
        self.session_id = session_id
        self.run_id_provider = run_id_provider or (lambda: uuid.uuid4().hex)
        self.parallel_limit = max(1, int(parallel_limit))
        self.file_cache = file_cache or FileVersionCache()
        self._sequence = 0

    def reset_run(self) -> None:
        self._sequence = 0

    def create_context(self, action: Any) -> ToolUseContext:
        self._sequence += 1
        tool = self.registry.get(action.tool_name)
        run_id = self.run_id_provider() or uuid.uuid4().hex
        return ToolUseContext(
            tool_id=f"tool_{run_id}_{self._sequence:04d}",
            tool_name=action.tool_name,
            arguments=self.argument_redactor(action.arguments),
            session_id=self.session_id,
            read_file_cache=self.file_cache,
            abort_signal=self.cancellation_token,
            started_at=perf_counter(),
            max_result_chars=getattr(tool, "max_result_chars", None),
        )

    async def execute_one(self, action: Any) -> ToolExecutionOutcome:
        context = self.create_context(action)
        try:
            result = await self._execute_core(action, context)
            result = self.result_budget.apply_one(result, context.tool_id, context.max_result_chars)
            self._emit_terminal(context, result)
            return ToolExecutionOutcome(action, result, context)
        except asyncio.CancelledError:
            self._emit_cancelled(context, self.cancellation_token.reason or "Run cancelled")
            raise

    async def execute_many(self, actions: list[Any]) -> list[ToolExecutionOutcome]:
        """Execute consecutive concurrency-safe actions in bounded groups."""
        contexts = [self.create_context(action) for action in actions]
        outcomes: list[ToolExecutionOutcome | None] = [None] * len(actions)
        semaphore = asyncio.Semaphore(self.parallel_limit)

        async def run_at(index: int) -> None:
            action = actions[index]
            context = contexts[index]
            async with semaphore:
                try:
                    result = await self._execute_core(action, context)
                    result = self.result_budget.apply_one(
                        result, context.tool_id, context.max_result_chars
                    )
                    outcomes[index] = ToolExecutionOutcome(action, result, context)
                except asyncio.CancelledError:
                    self._emit_cancelled(context, self.cancellation_token.reason or "Run cancelled")
                    raise

        index = 0
        while index < len(actions):
            if self._is_concurrency_safe(actions[index]):
                end = index + 1
                while end < len(actions) and self._is_concurrency_safe(actions[end]):
                    end += 1
                await asyncio.gather(*(run_at(item) for item in range(index, end)))
                index = end
            else:
                await run_at(index)
                index += 1

        completed = [outcome for outcome in outcomes if outcome is not None]
        if len(completed) != len(actions):
            raise RuntimeError("Tool scheduler did not produce every result")
        results = self.result_budget.apply_turn(
            [outcome.result for outcome in completed],
            [outcome.context.tool_id for outcome in completed],
        )
        for outcome, result in zip(completed, results, strict=True):
            outcome.result = result
            self._emit_terminal(outcome.context, result)
        return completed

    def _is_concurrency_safe(self, action: Any) -> bool:
        tool = self.registry.get(action.tool_name)
        with suppress(Exception):
            return bool(tool.is_concurrency_safe(**action.arguments))
        return False

    async def _execute_core(self, action: Any, context: ToolUseContext) -> ToolResult:
        tool = self.registry.get(action.tool_name)
        self.cancellation_token.raise_if_cancelled()
        action.arguments = self._normalize_arguments(tool, action.arguments)
        context.arguments = self.argument_redactor(action.arguments)
        action_record = None
        guard_result: GuardResult | None = None
        checkpoint_metadata: dict[str, Any] = {}
        confirmation_outcome: str | None = None
        started_at = perf_counter()
        context_token = set_current_tool_context(context)
        self._emit_start(context)

        if self.working_memory:
            action_record = self.working_memory.record_action(action.tool_name, action.arguments)

        try:
            if self.hook_manager:
                hook_result = self.hook_manager.run_pre_tool_use(
                    {
                        "tool_name": action.tool_name,
                        "arguments": dict(action.arguments),
                        "metadata": {"tool_id": context.tool_id},
                    }
                )
                if isinstance(hook_result, ToolResult):
                    return self._complete_memory(action, hook_result, action_record)
                action.arguments = self._normalize_arguments(
                    tool, dict(hook_result.get("arguments", action.arguments))
                )
                context.arguments = self.argument_redactor(action.arguments)

            guard_result = self.guard_checker(action)
            context.risk_level = guard_result.risk_level.value
            if not guard_result.allowed:
                result = ToolResult(
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
                confirmation_outcome = "blocked"
                return self._finalize_pipeline(
                    action,
                    result,
                    action_record,
                    guard_result,
                    confirmation_outcome,
                    checkpoint_metadata,
                    context,
                    started_at,
                )

            if guard_result.requires_confirmation:
                self.event_handler(
                    ToolEvent(
                        type="permission_request",
                        tool_id=context.tool_id,
                        tool_name=context.tool_name,
                        arguments=dict(context.arguments),
                        started_at=context.started_at,
                        risk_level=context.risk_level,
                        metadata={
                            "reason": guard_result.reason,
                            "suggestions": guard_result.suggestions,
                        },
                    )
                )
                confirmation = await self.confirmation_handler(action, guard_result, context)
                if not confirmation.success:
                    return self._finalize_pipeline(
                        action,
                        confirmation,
                        action_record,
                        guard_result,
                        "declined",
                        checkpoint_metadata,
                        context,
                        started_at,
                    )
                confirmation_outcome = "confirmed"

            checkpoint_metadata = self.checkpoint_before(action, context)
            async_executor = getattr(tool, "async_execute", None)
            if callable(async_executor):
                result = await async_executor(**action.arguments)
            else:
                result = await asyncio.to_thread(tool.execute, **action.arguments)
            if not isinstance(result, ToolResult):
                raise TypeError(
                    f"Tool '{action.tool_name}' returned {type(result).__name__}, expected ToolResult"
                )
            if checkpoint_metadata:
                result.metadata.update(checkpoint_metadata)
            if result.success and result.metadata.get("interaction_required"):
                result = await self.interaction_handler(result)

            if self.hook_manager:
                hook_result = self.hook_manager.run_post_tool_use(
                    {
                        "tool_name": action.tool_name,
                        "arguments": dict(action.arguments),
                        "result": result,
                        "metadata": {"tool_id": context.tool_id},
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
            return self._finalize_pipeline(
                action,
                result,
                action_record,
                guard_result,
                confirmation_outcome,
                checkpoint_metadata,
                context,
                started_at,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = ToolResult(
                success=False,
                output="",
                error=str(exc),
            )
            return self._finalize_pipeline(
                action,
                result,
                action_record,
                guard_result,
                "error",
                checkpoint_metadata,
                context,
                started_at,
            )
        finally:
            reset_current_tool_context(context_token)

    def _finalize_pipeline(
        self,
        action: Any,
        result: ToolResult,
        action_record: Any,
        guard_result: GuardResult | None,
        confirmation_outcome: str | None,
        checkpoint_metadata: dict[str, Any],
        context: ToolUseContext,
        started_at: float,
    ) -> ToolResult:
        result = self.result_redactor(result)
        self.checkpoint_after(action, result, checkpoint_metadata, context)
        result = self._complete_memory(action, result, action_record)
        self.audit_handler(
            action,
            guard_result,
            result,
            confirmation_outcome=confirmation_outcome,
            checkpoint_metadata=checkpoint_metadata,
            started_at=started_at,
        )
        return result

    def _complete_memory(self, action: Any, result: ToolResult, action_record: Any) -> ToolResult:
        if self.working_memory and action_record:
            status = ActionStatus.SUCCESS if result.success else ActionStatus.FAILED
            self.working_memory.update_action(
                action_record.id,
                status,
                result=result.output,
                error=result.error,
            )
        if self.file_observer:
            self.file_observer(action, result)
        return result

    @staticmethod
    def _normalize_arguments(tool: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        normalizer = getattr(tool, "normalize_arguments", None)
        if not callable(normalizer):
            return arguments
        normalized = normalizer(arguments)
        return normalized if isinstance(normalized, dict) else arguments

    def _emit_start(self, context: ToolUseContext) -> None:
        self.event_handler(
            ToolEvent(
                type="tool_start",
                tool_id=context.tool_id,
                tool_name=context.tool_name,
                arguments=dict(context.arguments),
                started_at=context.started_at,
                risk_level=context.risk_level,
            )
        )

    def _emit_terminal(self, context: ToolUseContext, result: ToolResult) -> None:
        if context.metadata.get("terminal_emitted"):
            return
        context.metadata["terminal_emitted"] = True
        elapsed = max(0.0, perf_counter() - context.started_at)
        event_type: ToolEventType = (
            "tool_cancelled"
            if result.metadata.get("cancelled")
            else "tool_result"
            if result.success
            else "tool_error"
        )
        result.metadata.setdefault("tool_id", context.tool_id)
        result.metadata.setdefault("duration_ms", int(elapsed * 1000))
        self.event_handler(
            ToolEvent(
                type=event_type,
                tool_id=context.tool_id,
                tool_name=context.tool_name,
                arguments=dict(context.arguments),
                started_at=context.started_at,
                duration_ms=int(elapsed * 1000),
                risk_level=str(result.metadata.get("risk_level", context.risk_level)),
                success=result.success,
                output=result.output or "",
                error=result.error,
                diff=result.metadata.get("diff"),
                collapsible=len(result.output or "") > 1200,
                metadata=dict(result.metadata),
            )
        )

    def _emit_cancelled(self, context: ToolUseContext, reason: str) -> None:
        if context.metadata.get("terminal_emitted"):
            return
        context.metadata["terminal_emitted"] = True
        elapsed = max(0.0, perf_counter() - context.started_at)
        self.event_handler(
            ToolEvent(
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
        )
