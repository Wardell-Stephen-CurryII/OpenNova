"""Model-driven workflow routing for natural-language turns."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from opennova.providers.base import BaseLLMProvider, Message, ToolSchema


class WorkflowDecision(StrEnum):
    """Execution workflow selected for the current user turn."""

    PLAN = "plan"
    ACT = "act"


@dataclass(frozen=True)
class WorkflowRoutingResult:
    """Validated result of the model-driven workflow routing call."""

    decision: WorkflowDecision | None
    reason: str = ""
    confidence: float = 0.0
    error: str | None = None

    @property
    def resolved(self) -> bool:
        return self.decision is not None and self.error is None


class WorkflowRouter:
    """Ask the active model to choose plan or act using a forced tool call."""

    TOOL_NAME = "select_execution_mode"
    SYSTEM_MESSAGE_NAME = "opennova_workflow_router"
    ROUTER_PROMPT = """You are OpenNova's execution workflow controller.

Choose the workflow that best matches the user's semantic intent and the conversation context.

- Choose `plan` when the user wants an implementation plan, design review, or explicit approval
  before project changes begin.
- Choose `act` when the user wants direct execution, asks a question, requests analysis only, or
  does not require approval before changes.
- Interpret the complete request and prior conversation. Do not rely on isolated words.
- This decision controls execution safety. Do not perform the task and do not answer in prose.

You must call `select_execution_mode` exactly once."""

    TOOL_SCHEMA = ToolSchema(
        name=TOOL_NAME,
        description="Select whether OpenNova should plan for approval or act directly.",
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [WorkflowDecision.PLAN.value, WorkflowDecision.ACT.value],
                    "description": "The execution workflow for the current user turn.",
                },
                "reason": {
                    "type": "string",
                    "description": "A concise semantic reason for the selected workflow.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Confidence in the workflow decision from 0 to 1.",
                },
            },
            "required": ["mode", "reason", "confidence"],
            "additionalProperties": False,
        },
    )

    def __init__(self, llm: BaseLLMProvider):
        self.llm = llm

    async def route(
        self,
        messages: Sequence[Message],
        task: str,
    ) -> WorkflowRoutingResult:
        """Resolve a workflow without mutating the conversation transcript."""
        routing_messages = [
            Message(
                role="system",
                content=self.ROUTER_PROMPT,
                name=self.SYSTEM_MESSAGE_NAME,
            ),
            *messages,
        ]
        if not any(message.role == "user" for message in routing_messages):
            routing_messages.append(Message(role="user", content=f"Task: {task}"))

        try:
            response = await self.llm.chat(
                routing_messages,
                tools=[self.TOOL_SCHEMA],
                temperature=0,
                tool_choice="required",
            )
        except Exception as exc:
            return WorkflowRoutingResult(
                decision=None,
                error=f"Workflow routing failed: {type(exc).__name__}: {exc}",
            )

        tool_calls = response.tool_calls or []
        if len(tool_calls) != 1 or tool_calls[0].name != self.TOOL_NAME:
            return WorkflowRoutingResult(
                decision=None,
                error="Workflow routing did not return the required control tool call.",
            )

        arguments = tool_calls[0].arguments or {}
        try:
            decision = WorkflowDecision(str(arguments.get("mode", "")))
            confidence = max(0.0, min(1.0, float(arguments.get("confidence", 0.0))))
        except (TypeError, ValueError) as exc:
            return WorkflowRoutingResult(
                decision=None,
                error=f"Workflow routing returned invalid arguments: {exc}",
            )

        return WorkflowRoutingResult(
            decision=decision,
            reason=str(arguments.get("reason", "")).strip(),
            confidence=confidence,
        )
