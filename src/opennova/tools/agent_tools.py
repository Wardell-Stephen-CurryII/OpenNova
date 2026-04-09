"""
Agent Tool - Spawn and manage worker agents.

Provides:
- Agent tool for spawning worker agents
- Agent execution lifecycle management
- Progress tracking and results
- Background task support
"""

import asyncio
import concurrent.futures
from datetime import datetime
from typing import Any

from opennova.providers.base import Message
from opennova.tasks import Task, TaskStatus, TaskType
from opennova.tasks.task import generate_message_id
from opennova.tools.base import BaseTool, ToolResult
from opennova.tools.task_tools import get_global_task_manager


class AgentTool(BaseTool):
    """Launch a new agent to perform a task."""

    name = "agent"
    description = "Launch a new agent (worker) to perform a task. Workers execute autonomously - especially research, implementation, or verification tasks. Use parallel agent launches for independent work."

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize the agent tool with optional runtime context."""
        super().__init__(config)
        self.runtime = self.config.get("runtime")

    def _apply_result_to_task(self, task: Task, result: dict[str, Any], status: TaskStatus) -> None:
        """Persist final task state consistently for foreground and background agents."""
        manager = get_global_task_manager()
        manager.update_task_status(task.id, status)

        pending_messages = len(task.message_queue)
        delivered_messages = len(task.delivered_messages)
        delivered_follow_up_batches = len(task.follow_up_batches)
        session_updates = {
            "pending_messages": pending_messages,
            "delivered_messages": delivered_messages,
            "delivered_follow_up_batches": delivered_follow_up_batches,
            "completed_at": datetime.now().isoformat(),
        }
        if result.get("session_state"):
            session_updates["child_session_state"] = result["session_state"]
        if result.get("output"):
            session_updates["last_agent_result"] = result["output"]
        if result.get("error"):
            session_updates["last_error"] = result["error"]

        manager.set_session_state(task.id, **session_updates)
        task.usage.duration_ms = result.get("duration_ms", task.usage.duration_ms)

    def _run_agent_sync_blocking(self, task: Task, prompt: str) -> dict[str, Any]:
        """Run the async agent workflow from synchronous contexts."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._run_agent_sync(task, prompt))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(self._run_agent_sync(task, prompt)))
            return future.result()

    def execute(
        self,
        description: str,
        prompt: str,
        subagent_type: str | None = None,
        run_in_background: bool = False,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """
        Launch a new agent.

        Args:
            description: Short (3-5 word) description of task
            prompt: The task for the agent to perform
            subagent_type: The type of specialized agent to use (default: general-purpose)
            run_in_background: Set to true to run in background
            model: Optional model override for this agent
            metadata: Additional metadata for the task

        Returns:
            ToolResult with agent ID or task information
        """
        try:
            manager = get_global_task_manager()
            full_description = f"Agent: {description}"

            # Create task for this agent
            task = manager.create_task(
                task_type=TaskType.LOCAL_AGENT,
                description=full_description,
                metadata={
                    "subject": description,
                    "active_form": f"Running agent: {description}",
                    "subagent_type": subagent_type,
                    "model": model,
                    "prompt": prompt,
                    **(metadata or {}),
                },
            )
            manager.set_session_state(
                task.id,
                prompt=prompt,
                description=description,
                subagent_type=subagent_type,
                model=model,
                parent_runtime_available=self.runtime is not None,
            )

            if run_in_background:
                # Register as async agent task
                manager.update_task_status(task.id, TaskStatus.RUNNING)

                # Start background execution
                asyncio.create_task(self._run_agent_background(task, prompt))

                return ToolResult(
                    success=True,
                    output=f"Launched agent {task.id} in background: {description}",
                    metadata={
                        "status": "async_launched",
                        "agentId": task.id,
                        "description": description,
                        "prompt": prompt,
                        "outputFile": task.output_file,
                    },
                )
            else:
                # Run synchronously
                manager.update_task_status(task.id, TaskStatus.RUNNING)
                result = self._run_agent_sync_blocking(task, prompt)

                if result["success"]:
                    self._apply_result_to_task(task, result, TaskStatus.COMPLETED)
                    return ToolResult(
                        success=True,
                        output=result.get("output", "Agent completed successfully"),
                        metadata={
                            "status": "completed",
                            "agentId": task.id,
                            "content": result.get("output"),
                            "totalDurationMs": result.get("duration_ms", 0),
                            "totalToolUseCount": result.get("tool_count", 0),
                            "totalTokens": result.get("token_count", 0),
                            "messageCount": len(task.messages),
                        },
                    )
                else:
                    self._apply_result_to_task(task, result, TaskStatus.FAILED)
                    return ToolResult(
                        success=False,
                        output="",
                        error=result.get("error", "Agent execution failed"),
                    )

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    async def _run_agent_sync(
        self,
        task: Task,
        prompt: str,
    ) -> dict[str, Any]:
        """Run agent synchronously and return result."""
        start_time = datetime.now()
        manager = get_global_task_manager()

        def on_progress(progress: dict[str, Any]) -> None:
            manager.update_task_progress(
                task.id,
                activity=progress.get("activity"),
                token_count=progress.get("token_count", 0),
                tool_use_increment=progress.get("tool_use_increment", 0),
                last_tool_name=progress.get("last_tool_name"),
                mark_complete=progress.get("is_complete", False),
            )

        try:
            # Import runtime components
            from opennova.runtime.agent import AgentRuntime

            # Create a child runtime that inherits the parent runtime configuration
            if self.runtime is not None:
                agent_runtime = self.runtime.create_child_runtime()
            else:
                agent_runtime = AgentRuntime(
                    config={},
                    register_default_tools=True,
                    enable_mcp=False,
                    enable_skills=False,
                )

            injected_messages: list[str] = []
            for message in task.messages:
                if message.get("type") == "user_message":
                    content = message.get("content")
                    if content:
                        injected_messages.append(content)

            if injected_messages:
                combined_prompt = "\n\n".join(injected_messages)
                agent_runtime.state.last_result = combined_prompt

            def on_iteration_start(loop_messages: list[Message]) -> None:
                queued_messages = manager.dequeue_messages(task.id)
                if not queued_messages:
                    return

                followups = [
                    message.get("content", "")
                    for message in queued_messages
                    if message.get("type") == "user_message" and message.get("content")
                ]
                if not followups:
                    return

                combined_followup = "\n\n".join(followups)
                rendered_followup = (
                    "Additional instruction from the parent conversation:\n"
                    f"{combined_followup}"
                )
                loop_messages.append(
                    Message(
                        role="user",
                        content=rendered_followup,
                    )
                )
                manager.mark_messages_delivered(task.id, queued_messages)
                for delivered_message in task.delivered_messages[-len(queued_messages):]:
                    delivered_message["delivery_state"] = "delivered"
                    delivered_message["delivered_at"] = datetime.now().isoformat()
                batch = manager.record_follow_up_batch(task.id, queued_messages, rendered_followup)
                batch_id = batch.get("batch_id") if batch else None
                delivered_message_ids = [
                    message.get("message_id") for message in queued_messages if message.get("message_id")
                ]
                manager.set_session_state(
                    task.id,
                    last_user_message=followups[-1],
                    last_follow_up_batch=rendered_followup,
                    last_follow_up_batch_id=batch_id,
                    last_delivered_message_ids=delivered_message_ids,
                    pending_messages=len(task.message_queue),
                    delivered_messages=len(task.delivered_messages),
                    delivered_follow_up_batches=len(task.follow_up_batches),
                )

            agent_runtime.register_callback("iteration_start", on_iteration_start)

            # Run the agent with progress reporting
            result = await agent_runtime.run(prompt, mode="act", stream=False, progress_callback=on_progress)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            usage = getattr(task, "usage", None)

            return {
                "success": True,
                "output": result,
                "duration_ms": duration_ms,
                "tool_count": usage.tool_uses if usage else 0,
                "token_count": usage.total_tokens if usage else 0,
                "session_state": dict(task.session_state),
                "pending_messages": len(task.message_queue),
                "delivered_messages": len(task.delivered_messages),
                "delivered_follow_up_batches": len(task.follow_up_batches),
            }
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            return {
                "success": False,
                "error": str(e),
                "duration_ms": duration_ms,
                "tool_count": task.usage.tool_uses,
                "token_count": task.usage.total_tokens,
                "pending_messages": len(task.message_queue),
                "delivered_messages": len(task.delivered_messages),
                "delivered_follow_up_batches": len(task.follow_up_batches),
            }

    async def _run_agent_background(
        self,
        task: Task,
        prompt: str,
    ) -> None:
        """Run agent in background and handle completion."""
        start_time = datetime.now()
        manager = get_global_task_manager()

        try:
            # Write task notification to output file
            from opennova.utils.task_output import write_task_output
            write_task_output(
                task.id,
                f"<task_notification>\n<task-id>{task.id}</task-id>\n"
            )

            # Run the agent
            result = await self._run_agent_sync(task, prompt)

            if result["success"]:
                # Update final status
                self._apply_result_to_task(task, result, TaskStatus.COMPLETED)

                # Write completion notification
                notification = self._format_completion_notification(
                    task.id,
                    task.description,
                    result["output"],
                    result["duration_ms"],
                    result["tool_count"],
                    result.get("token_count", 0),
                    result.get("pending_messages", 0),
                    result.get("delivered_messages", 0),
                    result.get("delivered_follow_up_batches", 0),
                )
                write_task_output(task.id, notification)

                # Mark as notified
                task.notified = True
                manager.update_task_progress(task.id, activity="Agent completed", mark_complete=True)

            else:
                self._apply_result_to_task(task, result, TaskStatus.FAILED)

                # Write failure notification
                notification = self._format_failure_notification(
                    task.id,
                    task.description,
                    result["error"],
                    result.get("duration_ms", 0),
                    result.get("pending_messages", 0),
                    result.get("delivered_messages", 0),
                    result.get("delivered_follow_up_batches", 0),
                )
                write_task_output(task.id, notification)

                task.notified = True
                manager.update_task_progress(task.id, activity="Agent failed")

        except asyncio.CancelledError:
            manager.update_task_status(task.id, TaskStatus.KILLED)
            manager.update_task_progress(task.id, activity="Agent was stopped", mark_complete=True)
            manager.set_session_state(
                task.id,
                last_error="Agent was stopped",
                pending_messages=len(task.message_queue),
                delivered_messages=len(task.delivered_messages),
                delivered_follow_up_batches=len(task.follow_up_batches),
            )
            write_task_output(
                task.id,
                f"<task_notification>\n<task-id>{task.id}</task-id>\n<status>killed</status>\n<summary>Agent was stopped</summary>\n<pending_messages>{len(task.message_queue)}</pending_messages>\n<delivered_messages>{len(task.delivered_messages)}</delivered_messages>\n<delivered_follow_up_batches>{len(task.follow_up_batches)}</delivered_follow_up_batches>\n</task_notification>\n",
            )
        except Exception as e:
            self._apply_result_to_task(
                task,
                {
                    "error": str(e),
                    "duration_ms": int((datetime.now() - start_time).total_seconds() * 1000),
                    "pending_messages": len(task.message_queue),
                    "delivered_messages": len(task.delivered_messages),
                    "delivered_follow_up_batches": len(task.follow_up_batches),
                },
                TaskStatus.FAILED,
            )
            manager.update_task_progress(task.id, activity="Agent failed", mark_complete=True)
            write_task_output(
                task.id,
                self._format_failure_notification(
                    task.id,
                    task.description,
                    str(e),
                    int((datetime.now() - start_time).total_seconds() * 1000),
                    len(task.message_queue),
                    len(task.delivered_messages),
                    len(task.follow_up_batches),
                ),
            )

    def _format_completion_notification(
        self,
        agent_id: str,
        description: str,
        result: str,
        duration_ms: int,
        tool_count: int,
        token_count: int,
        pending_messages: int,
        delivered_messages: int,
        delivered_follow_up_batches: int,
    ) -> str:
        """Format completion notification."""
        return f"""<task_notification>
<task-id>{agent_id}</task-id>
<status>completed</status>
<summary>Agent \"{description}\" completed</summary>
<result>{result[:5000]}{'...' if len(result) > 5000 else ''}</result>
<usage>
  <total_tokens>{token_count}</total_tokens>
  <tool_uses>{tool_count}</tool_uses>
  <duration_ms>{duration_ms}</duration_ms>
</usage>
<pending_messages>{pending_messages}</pending_messages>
<delivered_messages>{delivered_messages}</delivered_messages>
<delivered_follow_up_batches>{delivered_follow_up_batches}</delivered_follow_up_batches>
</task_notification>
"""

    def _format_failure_notification(
        self,
        agent_id: str,
        description: str,
        error: str,
        duration_ms: int,
        pending_messages: int,
        delivered_messages: int,
        delivered_follow_up_batches: int,
    ) -> str:
        """Format failure notification."""
        return f"""<task_notification>
<task-id>{agent_id}</task-id>
<status>failed</status>
<summary>Agent \"{description}\" failed</summary>
<error>{error[:1000]}</error>
<usage>
  <duration_ms>{duration_ms}</duration_ms>
</usage>
<pending_messages>{pending_messages}</pending_messages>
<delivered_messages>{delivered_messages}</delivered_messages>
<delivered_follow_up_batches>{delivered_follow_up_batches}</delivered_follow_up_batches>
</task_notification>
"""


class SendMessageTool(BaseTool):
    """Send a follow-up message to an existing agent."""

    name = "send_message"
    description = "Send a follow-up message to an existing agent. Use this to continue a worker's work or send additional instructions."

    def execute(
        self,
        to: str,
        message: str,
        **kwargs: Any,
    ) -> ToolResult:
        """
        Send message to agent.

        Args:
            to: Agent ID to send message to
            message: Message to send

        Returns:
            ToolResult with response or error
        """
        try:
            manager = get_global_task_manager()
            task = manager.get_task(to)

            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Agent '{to}' not found",
                )

            if task.status != TaskStatus.RUNNING:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Agent '{to}' is not running (status: {task.status.value}). Use task_create to start a new agent.",
                )

            message_record = {
                "type": "user_message",
                "content": message,
                "timestamp": datetime.now().isoformat(),
            }
            message_record["message_id"] = generate_message_id(message_record["content"], message_record["timestamp"])
            message_record["delivery_state"] = "queued"

            manager.add_message(to, message_record.copy())
            task.message_queue.append(message_record.copy())
            manager.update_task_progress(to, activity="Received follow-up message")
            manager.set_session_state(
                to,
                last_user_message=message,
                last_queued_message_id=message_record["message_id"],
                pending_messages=len(task.message_queue),
                delivered_messages=len(task.delivered_messages),
                delivered_follow_up_batches=len(task.follow_up_batches),
            )

            return ToolResult(
                success=True,
                output=f"Sent message to agent {to}",
                metadata={
                    "agent_id": to,
                    "queued_message": message,
                    "message_id": message_record["message_id"],
                    "delivery_state": message_record["delivery_state"],
                    "message_count": len(task.messages),
                    "pending_messages": len(task.message_queue),
                    "delivered_messages": len(task.delivered_messages),
                    "delivered_follow_up_batches": len(task.follow_up_batches),
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
