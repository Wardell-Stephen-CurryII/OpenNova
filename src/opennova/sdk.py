"""Headless Python SDK for driving OpenNova sessions programmatically."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from opennova.cli.tool_progress import ToolProgressTracker
from opennova.config import Config
from opennova.runtime.agent import AgentRuntime
from opennova.tools.base import ToolResult


@dataclass
class SDKEvent:
    """Event emitted by the headless OpenNova SDK."""

    type: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable event payload."""
        return {
            "type": self.type,
            "session_id": self.session_id,
            "data": self.data,
        }


class OpenNovaClient:
    """Small session-oriented API for embedding OpenNova in scripts or services."""

    def __init__(
        self,
        config: Config | dict[str, Any],
        runtime_factory: Callable[[dict[str, Any]], AgentRuntime] = AgentRuntime,
    ):
        self.config = config.data if isinstance(config, Config) else config
        self.runtime_factory = runtime_factory
        self._sessions: dict[str, Any] = {}

    def create_session(self) -> str:
        """Create an isolated runtime session and return its session id."""
        runtime = self.runtime_factory(self.config)
        session_id = getattr(getattr(runtime, "session_manager", None), "session_id", None)
        if not session_id:
            session_id = str(uuid.uuid4())
        self._sessions[session_id] = runtime
        return session_id

    def get_runtime(self, session_id: str) -> Any:
        """Return the runtime backing a session."""
        if session_id not in self._sessions:
            raise KeyError(f"Unknown OpenNova SDK session: {session_id}")
        return self._sessions[session_id]

    def list_sessions(self) -> list[dict[str, Any]]:
        """List active SDK sessions."""
        return [{"session_id": session_id} for session_id in self._sessions]

    def resume_session(self, session_id: str) -> str:
        """Create a runtime and load a persisted OpenNova session into it."""
        runtime = self.runtime_factory(self.config)
        runtime.resume_session(session_id)
        self._sessions[session_id] = runtime
        return session_id

    async def submit_message(
        self,
        session_id: str,
        message: str,
        mode: str = "act",
        stream: bool = True,
    ) -> str:
        """Run a message to completion and return the final result."""
        final_result = ""
        async for event in self.stream_message(session_id, message, mode=mode, stream=stream):
            if event.type == "run_complete":
                final_result = str(event.data.get("result", ""))
            elif event.type == "run_error":
                raise RuntimeError(str(event.data.get("error", "OpenNova SDK run failed")))
        return final_result

    async def stream_message(
        self,
        session_id: str,
        message: str,
        mode: str = "act",
        stream: bool = True,
    ) -> AsyncIterator[SDKEvent]:
        """Run a message and yield normalized headless events."""
        runtime = self.get_runtime(session_id)
        queue: asyncio.Queue[SDKEvent] = asyncio.Queue()
        tool_progress = ToolProgressTracker()
        saw_canonical_tool_events = False

        def enqueue(event_type: str, **data: Any) -> None:
            queue.put_nowait(SDKEvent(type=event_type, session_id=session_id, data=data))

        runtime.register_callback("thought", lambda thought: enqueue("thought", content=thought))
        def on_action(tool_name: str, args: dict[str, Any]) -> None:
            if saw_canonical_tool_events:
                return
            enqueue("tool_start", **tool_progress.start_tool(tool_name, args))

        def on_result(result: ToolResult) -> None:
            if saw_canonical_tool_events:
                return
            data = self._tool_result_data(result)
            data.update(tool_progress.finish_tool(result))
            enqueue("tool_result", **data)

        def on_tool_event(event: Any) -> None:
            nonlocal saw_canonical_tool_events
            saw_canonical_tool_events = True
            payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            event_type = str(payload.pop("type"))
            enqueue(event_type, **payload)

        runtime.register_callback("action", on_action)
        runtime.register_callback("result", on_result)
        runtime.register_callback("tool_event", on_tool_event)
        runtime.register_callback("stream", lambda chunk: enqueue("text_delta", content=getattr(chunk, "content", "") or ""))
        runtime.register_callback(
            "plan",
            lambda plan, plan_file_path=None: enqueue(
                "plan",
                plan=getattr(plan, "to_dict", lambda: str(plan))(),
                plan_file_path=str(plan_file_path) if plan_file_path else None,
            ),
        )

        yield SDKEvent(
            type="run_start",
            session_id=session_id,
            data={"message": message, "mode": mode, "stream": stream},
        )

        async def run_to_queue() -> None:
            try:
                result = await runtime.run(message, mode=mode, stream=stream)
                await queue.put(SDKEvent("run_complete", session_id, {"result": result}))
            except Exception as e:
                await queue.put(SDKEvent("run_error", session_id, {"error": str(e)}))

        task = asyncio.create_task(run_to_queue())
        try:
            while True:
                event = await queue.get()
                yield event
                if event.type in {"run_complete", "run_error"}:
                    break
        finally:
            if not task.done():
                task.cancel()

    @staticmethod
    def _tool_result_data(result: ToolResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "metadata": result.metadata,
        }
