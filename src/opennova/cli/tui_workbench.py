"""Session-scoped workbench state for the OpenNova TUI side panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from opennova.cli.tool_cards import ToolCardPanelState, ToolCardStore, build_tool_card_panel
from opennova.tools.todo_tools import TodoWriteTool

WorkbenchTab = Literal["context", "tasks", "activity"]
WORKBENCH_TABS: tuple[WorkbenchTab, ...] = ("context", "tasks", "activity")
LEGACY_WORKBENCH_TABS = {
    "tools": "activity",
    "plan": "tasks",
    "todos": "tasks",
}


@dataclass(frozen=True)
class ActiveFileSnapshot:
    """One recently observed file and its latest activity."""

    path: str
    activity: str


@dataclass(frozen=True)
class ContextWorkbenchSnapshot:
    """UI-safe view of the context currently driving the agent."""

    task: str = ""
    run_phase: str = "idle"
    current_step: str = ""
    total_messages: int = 0
    total_tokens: int = 0
    context_window: int = 0
    utilization_percent: float = 0.0
    compression_count: int = 0
    has_compressed_summary: bool = False
    compression_threshold_percent: float = 55.0
    active_files: tuple[ActiveFileSnapshot, ...] = ()
    recent_decisions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class TaskWorkbenchSnapshot:
    """Combined plan and todo progress for the Tasks tab."""

    plan: PlanWorkbenchSnapshot | None
    todos: tuple[dict[str, Any], ...]
    completed: int = 0
    total: int = 0
    current_item: str = ""
    status_counts: tuple[tuple[str, int], ...] = ()


@dataclass
class WorkbenchPanelState:
    """Complete render state for the TUI workbench side panel."""

    active_tab: WorkbenchTab
    tools: ToolCardPanelState
    plan: PlanWorkbenchSnapshot | None
    todos: list[dict[str, Any]]
    context: ContextWorkbenchSnapshot | None = None
    tasks: TaskWorkbenchSnapshot | None = None
    key_hint: str = "alt+1 context  alt+2 tasks  alt+3 activity  alt+t hide"

    @property
    def activity(self) -> ToolCardPanelState:
        """Return the existing tool-card state under its new Activity name."""
        return self.tools


def normalize_workbench_tab(tab: str) -> WorkbenchTab:
    """Map legacy tab names to the current information architecture."""
    normalized = LEGACY_WORKBENCH_TABS.get(tab, tab)
    return cast(WorkbenchTab, normalized if normalized in WORKBENCH_TABS else "context")


def next_workbench_tab(tab: WorkbenchTab | str) -> WorkbenchTab:
    """Return the next workbench tab in stable display order."""
    current = normalize_workbench_tab(tab)
    index = WORKBENCH_TABS.index(current)
    return WORKBENCH_TABS[(index + 1) % len(WORKBENCH_TABS)]


def previous_workbench_tab(tab: WorkbenchTab | str) -> WorkbenchTab:
    """Return the previous workbench tab in stable display order."""
    current = normalize_workbench_tab(tab)
    index = WORKBENCH_TABS.index(current)
    return WORKBENCH_TABS[(index - 1) % len(WORKBENCH_TABS)]


def build_workbench_panel_state(
    *,
    agent: Any,
    tool_cards: ToolCardStore,
    active_tab: WorkbenchTab,
    last_plan: PlanWorkbenchSnapshot | None = None,
) -> WorkbenchPanelState:
    """Build a side-panel render state from existing runtime data sources."""
    plan = _snapshot_plan(agent) or last_plan
    todos = TodoWriteTool.current_todos(getattr(agent, "state_store", None))
    return WorkbenchPanelState(
        active_tab=normalize_workbench_tab(active_tab),
        tools=build_tool_card_panel(tool_cards),
        plan=plan,
        todos=todos,
        context=_snapshot_context(agent, plan),
        tasks=snapshot_tasks(plan, todos),
    )


def _snapshot_context(
    agent: Any,
    plan: PlanWorkbenchSnapshot | None,
) -> ContextWorkbenchSnapshot:
    context_manager = getattr(agent, "context_manager", None)
    presentation = (
        context_manager.get_presentation_snapshot()
        if context_manager and hasattr(context_manager, "get_presentation_snapshot")
        else None
    )
    working_memory = getattr(agent, "working_memory", None)
    state = getattr(agent, "state", None)
    task = str(
        getattr(getattr(working_memory, "task_state", None), "description", "")
        or getattr(state, "current_task", "")
        or (plan.task if plan else "")
    )
    run_phase = _runtime_phase(agent, working_memory)
    current_step = ""
    if plan:
        active = next(
            (
                step
                for step in plan.steps
                if step.status in {"running", "in_progress", "executing", "interrupted"}
            ),
            None,
        )
        if active:
            current_step = f"{active.id} · {active.description}"

    active_files = _active_files(working_memory)
    decisions = tuple(str(item) for item in (getattr(working_memory, "decisions", []) or [])[-5:])
    sources: list[str] = []
    if presentation and presentation.total_messages:
        sources.append(f"conversation · {presentation.total_messages} messages")
    if getattr(context_manager, "system_prompt", None):
        sources.append("system instructions")
    if plan:
        sources.append(f"plan · {len(plan.steps)} steps")
    if presentation and presentation.has_compressed_summary:
        sources.append("compressed summary")
    if active_files:
        sources.append(f"working files · {len(active_files)}")
    if getattr(agent, "project_memory", None) is not None:
        sources.append("project memory")

    return ContextWorkbenchSnapshot(
        task=task,
        run_phase=run_phase,
        current_step=current_step,
        total_messages=int(getattr(presentation, "total_messages", 0)),
        total_tokens=int(getattr(presentation, "total_tokens", 0)),
        context_window=int(getattr(presentation, "context_window", 0)),
        utilization_percent=float(getattr(presentation, "utilization_percent", 0.0)),
        compression_count=int(getattr(presentation, "compression_count", 0)),
        has_compressed_summary=bool(getattr(presentation, "has_compressed_summary", False)),
        compression_threshold_percent=float(
            getattr(presentation, "compression_threshold_percent", 55.0)
        ),
        active_files=active_files,
        recent_decisions=decisions,
        sources=tuple(sources),
    )


def _runtime_phase(agent: Any, working_memory: Any) -> str:
    store = getattr(agent, "state_store", None)
    try:
        phase = store.get_state().run.phase
        return str(getattr(phase, "value", phase))
    except Exception:
        return str(getattr(getattr(working_memory, "task_state", None), "status", "idle"))


def _active_files(working_memory: Any, limit: int = 8) -> tuple[ActiveFileSnapshot, ...]:
    observations = list(getattr(working_memory, "observations", []) or [])
    latest: dict[str, str] = {}
    ordered: list[str] = []
    for observation in reversed(observations):
        path = str(getattr(observation, "file_path", "") or "")
        if not path or path in latest:
            continue
        latest[path] = str(getattr(observation, "change_type", "observed") or "observed")
        ordered.append(path)
        if len(ordered) >= limit:
            break
    return tuple(ActiveFileSnapshot(path=path, activity=latest[path]) for path in ordered)


def snapshot_tasks(
    plan: PlanWorkbenchSnapshot | None,
    todos: list[dict[str, Any]],
) -> TaskWorkbenchSnapshot:
    items: list[tuple[str, str]] = []
    if plan and plan.steps:
        items = [(step.status, step.description) for step in plan.steps]
        plan_ids = {str(getattr(step, "id", "")) for step in plan.steps}
        items.extend(
            (str(todo.get("status", "pending")), str(todo.get("content", "")))
            for todo in todos
            if todo.get("source") != "plan" and str(todo.get("id", "")) not in plan_ids
        )
    else:
        items = [
            (str(todo.get("status", "pending")), str(todo.get("content", "")))
            for todo in todos
        ]
    counts: dict[str, int] = {}
    for status, _ in items:
        counts[status] = counts.get(status, 0) + 1
    completed = sum(counts.get(status, 0) for status in ("done", "completed", "skipped"))
    current = next(
        (
            content
            for status, content in items
            if status in {"running", "in_progress", "executing", "interrupted"}
        ),
        "",
    )
    return TaskWorkbenchSnapshot(
        plan=plan,
        todos=tuple(dict(todo) for todo in todos),
        completed=completed,
        total=len(items),
        current_item=current,
        status_counts=tuple(sorted(counts.items())),
    )


def _snapshot_plan(agent: Any) -> PlanWorkbenchSnapshot | None:
    state = getattr(agent, "state", None)
    plan = getattr(state, "current_plan", None)
    if plan is None:
        return None

    approval = getattr(getattr(state, "plan_approval_status", None), "value", None)
    plan_path = getattr(state, "plan_file_path", None)
    return snapshot_plan(plan, plan_file_path=plan_path, approval_status=approval)


def snapshot_plan(
    plan: Any,
    *,
    plan_file_path: str | Path | None = None,
    approval_status: str | None = None,
) -> PlanWorkbenchSnapshot:
    """Create a durable UI snapshot from a runtime plan object."""
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
        approval_status=str(approval_status or "none"),
        plan_file_path=str(Path(plan_file_path)) if plan_file_path else "",
        steps=steps,
    )
