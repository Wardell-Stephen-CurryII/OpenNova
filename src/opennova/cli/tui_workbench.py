"""Session-scoped workbench state for the OpenNova TUI side panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from opennova.cli.tool_cards import ToolCardPanelState, ToolCardStore, build_tool_card_panel
from opennova.tools.todo_tools import TodoWriteTool

WorkbenchTab = Literal["tools", "plan", "todos"]
WORKBENCH_TABS: tuple[WorkbenchTab, ...] = ("tools", "plan", "todos")


@dataclass
class PlanStepSnapshot:
    """UI-safe snapshot of one plan step."""

    id: str
    description: str
    status: str
    result_summary: str = ""
    error: str = ""


@dataclass
class PlanWorkbenchSnapshot:
    """UI-safe snapshot of active plan state."""

    task: str
    status: str
    approval_status: str
    plan_file_path: str = ""
    steps: list[PlanStepSnapshot] = field(default_factory=list)


@dataclass
class WorkbenchPanelState:
    """Complete render state for the TUI workbench side panel."""

    active_tab: WorkbenchTab
    tools: ToolCardPanelState
    plan: PlanWorkbenchSnapshot | None
    todos: list[dict[str, Any]]
    key_hint: str = "alt+1 tools  alt+2 plan  alt+3 todos  alt+t hide"


def next_workbench_tab(tab: WorkbenchTab) -> WorkbenchTab:
    """Return the next workbench tab in stable display order."""
    index = WORKBENCH_TABS.index(tab) if tab in WORKBENCH_TABS else 0
    return WORKBENCH_TABS[(index + 1) % len(WORKBENCH_TABS)]


def previous_workbench_tab(tab: WorkbenchTab) -> WorkbenchTab:
    """Return the previous workbench tab in stable display order."""
    index = WORKBENCH_TABS.index(tab) if tab in WORKBENCH_TABS else 0
    return WORKBENCH_TABS[(index - 1) % len(WORKBENCH_TABS)]


def build_workbench_panel_state(
    *,
    agent: Any,
    tool_cards: ToolCardStore,
    active_tab: WorkbenchTab,
) -> WorkbenchPanelState:
    """Build a side-panel render state from existing runtime data sources."""
    return WorkbenchPanelState(
        active_tab=active_tab if active_tab in WORKBENCH_TABS else "tools",
        tools=build_tool_card_panel(tool_cards),
        plan=_snapshot_plan(agent),
        todos=TodoWriteTool.current_todos(),
    )


def _snapshot_plan(agent: Any) -> PlanWorkbenchSnapshot | None:
    state = getattr(agent, "state", None)
    plan = getattr(state, "current_plan", None)
    if plan is None:
        return None

    approval = getattr(getattr(state, "plan_approval_status", None), "value", None)
    plan_path = getattr(state, "plan_file_path", None)
    steps: list[PlanStepSnapshot] = []
    for step in getattr(plan, "steps", []) or []:
        steps.append(
            PlanStepSnapshot(
                id=str(getattr(step, "id", "")),
                description=str(getattr(step, "description", "")),
                status=str(getattr(getattr(step, "status", None), "value", getattr(step, "status", ""))),
                result_summary=str(getattr(step, "result_summary", "") or ""),
                error=str(getattr(step, "error", "") or ""),
            )
        )

    return PlanWorkbenchSnapshot(
        task=str(getattr(plan, "task", "") or "(untitled plan)"),
        status=str(getattr(getattr(plan, "status", None), "value", getattr(plan, "status", "planning"))),
        approval_status=str(approval or "none"),
        plan_file_path=str(Path(plan_path)) if plan_path else "",
        steps=steps,
    )
