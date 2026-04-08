"""
Agent Tool - Spawn and manage worker agents.

Provides:
- Agent tool for spawning worker agents
- Agent execution lifecycle management
- Progress tracking and results
- Background task support
"""

import asyncio
from datetime import datetime
from typing import Any

from opennova.providers.base import Message
from opennova.tasks import Task, TaskStatus, TaskType
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
                result = asyncio.run(self._run_agent_sync(task, prompt))

                if result["success"]:
                    manager.update_task_status(task.id, TaskStatus.COMPLETED)
                    manager.set_session_state(task.id, child_session_state=result.get("session_state", {}))
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
                    manager.update_task_status(task.id, TaskStatus.FAILED)
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
                loop_messages.append(
                    Message(
                        role="user",
                        content=(
                            "Additional instruction from the parent conversation:\n"
                            f"{combined_followup}"
                        ),
                    )
                )
                manager.set_session_state(task.id, last_user_message=followups[-1])

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
            }
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            return {
                "success": False,
                "error": str(e),
                "duration_ms": duration_ms,
                "tool_count": task.usage.tool_uses,
                "token_count": task.usage.total_tokens,
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
                manager.update_task_status(task.id, TaskStatus.COMPLETED)

                # Write completion notification
                notification = self._format_completion_notification(
                    task.id,
                    task.description,
                    result["output"],
                    result["duration_ms"],
                    result["tool_count"],
                )
                write_task_output(task.id, notification)

                # Mark as notified
                task.notified = True
                manager.update_task_progress(task.id, activity="Agent completed", mark_complete=True)
                manager.set_session_state(task.id, child_session_state=result.get("session_state", {}), pending_messages=0)

            else:
                manager.update_task_status(task.id, TaskStatus.FAILED)

                # Write failure notification
                notification = self._format_failure_notification(
                    task.id,
                    task.description,
                    result["error"],
                )
                write_task_output(task.id, notification)

                task.notified = True
                manager.update_task_progress(task.id, activity="Agent failed")

        except asyncio.CancelledError:
            manager.update_task_status(task.id, TaskStatus.KILLED)
            manager.update_task_progress(task.id, activity="Agent was stopped", mark_complete=True)
            write_task_output(
                task.id,
                f"<task_notification>\n<task-id>{task.id}</task-id>\n<status>killed</status>\n<summary>Agent was stopped</summary>\n</task_notification>\n",
            )
        except Exception as e:
            manager.update_task_status(task.id, TaskStatus.FAILED)
            manager.update_task_progress(task.id, activity="Agent failed", mark_complete=True)
            write_task_output(
                task.id,
                f"<task_notification>\n<task-id>{task.id}</task-id>\n<status>failed</status>\n<summary>Agent failed with error</summary>\n<error>{str(e)}</error>\n</task_notification>\n",
            )

    def _format_completion_notification(
        self,
        agent_id: str,
        description: str,
        result: str,
        duration_ms: int,
        tool_count: int,
    ) -> str:
        """Format completion notification."""
        return f"""<task_notification>
<task-id>{agent_id}</task-id>
<status>completed</status>
<summary>Agent "{description}" completed</summary>
<result>{result[:5000]}{'...' if len(result) > 5000 else ''}</result>
<usage>
  <total_tokens>N</total_tokens>
  <tool_uses>{tool_count}</tool_uses>
  <duration_ms>{duration_ms}</duration_ms>
</usage>
</task_notification>
"""

    def _format_failure_notification(
        self,
        agent_id: str,
        description: str,
        error: str,
    ) -> str:
        """Format failure notification."""
        return f"""<task_notification>
<task-id>{agent_id}</task-id>
<status>failed</status>
<summary>Agent "{description}" failed</summary>
<error>{error[:1000]}</error>
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

            # For now, append message to task's messages so active/background agents can consume it.
            # This keeps follow-ups visible to the running task until fully interactive messaging is added.
            manager.add_message(
                to,
                {
                    "type": "user_message",
                    "content": message,
                    "timestamp": datetime.now().isoformat(),
                },
            )
            task.message_queue.append(
                {
                    "type": "user_message",
                    "content": message,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            manager.update_task_progress(to, activity="Received follow-up message")
            manager.set_session_state(to, last_user_message=message, pending_messages=len(task.message_queue))

            return ToolResult(
                success=True,
                output=f"Sent message to agent {to}",
                metadata={
                    "agent_id": to,
                    "queued_message": message,
                    "message_count": len(task.messages),
                    "pending_messages": len(task.message_queue),
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
