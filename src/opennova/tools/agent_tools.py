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

from opennova.tasks import Task, TaskManager, TaskStatus, TaskType, TaskResult
from opennova.tools.base import BaseTool, ToolResult
from opennova.tools.task_tools import get_global_task_manager


class AgentTool(BaseTool):
    """Launch a new agent to perform a task."""

    name = "agent"
    description = "Launch a new agent (worker) to perform a task. Workers execute autonomously - especially research, implementation, or verification tasks. Use parallel agent launches for independent work."

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
        tool_count = 0

        try:
            # Import runtime components
            from opennova.runtime.agent import AgentRuntime
            from opennova.runtime.loop import ReActLoop

            # Create a new runtime for this agent
            # In production, this should use the parent runtime's config
            agent_runtime = AgentRuntime(
                config={},  # Will use default config
                register_default_tools=True,
                enable_mcp=False,  # Simplified for now
                enable_skills=False,
            )

            # Run the agent with the prompt
            result = await agent_runtime.run(prompt, mode="act", stream=False)

            # Count tool uses from result
            # This is simplified - in production we'd track actual tool usage

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            return {
                "success": True,
                "output": result,
                "duration_ms": duration_ms,
                "tool_count": tool_count,
            }
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            return {
                "success": False,
                "error": str(e),
                "duration_ms": duration_ms,
                "tool_count": tool_count,
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

        except asyncio.CancelledError:
            manager.update_task_status(task.id, TaskStatus.KILLED)
            write_task_output(
                task.id,
                f"<task_notification>\n<task-id>{task.id}</task-id>\n<status>killed</status>\n<summary>Agent was stopped</summary>\n</task_notification>\n",
            )
        except Exception as e:
            manager.update_task_status(task.id, TaskStatus.FAILED)
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

            # For now, append message to task's messages
            # In production, this would actually communicate with the running agent
            manager.add_message(
                to,
                {
                    "type": "user_message",
                    "content": message,
                    "timestamp": datetime.now().isoformat(),
                },
            )

            return ToolResult(
                success=True,
                output=f"Sent message to agent {to}",
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
