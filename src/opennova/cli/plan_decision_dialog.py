"""TUI dialog for deciding what to do with a pending plan."""

from __future__ import annotations

from typing import Literal

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static

PlanDecision = Literal["execute", "discard", "revise"]


class PlanDecisionDialog(ModalScreen[PlanDecision]):
    """Modal card that asks the user how to handle a pending plan."""

    CSS = """
    PlanDecisionDialog {
        align: center middle;
    }

    #plan-decision-card {
        width: 76;
        max-width: 92%;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    #plan-decision-title {
        margin-bottom: 1;
    }

    #plan-decision-options {
        height: auto;
        max-height: 8;
        margin-top: 1;
        margin-bottom: 1;
    }

    #plan-decision-help {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "revise", "Continue conversation"),
    ]

    _OPTIONS: tuple[tuple[PlanDecision, str, str], ...] = (
        ("execute", "执行计划", "Approve the current plan and run its steps now."),
        ("discard", "放弃计划", "Clear the pending plan and todos."),
        ("revise", "继续交谈修改计划", "Send your message to OpenNova to revise the plan."),
    )

    def __init__(self, *, plan_title: str = "", user_message: str = "") -> None:
        super().__init__()
        self.plan_title = plan_title
        self.user_message = user_message

    def compose(self) -> ComposeResult:
        with Container(id="plan-decision-card"):
            yield Static("[bold]Pending Plan[/bold]", id="plan-decision-title", markup=True)
            if self.plan_title:
                yield Static(f"[cyan]{self.plan_title}[/cyan]", markup=True)
            if self.user_message:
                yield Static(f"[dim]Your message:[/dim] {self.user_message}", markup=True)
            yield Static("[dim]Choose what to do with the current plan.[/dim]", markup=True)
            yield ListView(
                *[
                    ListItem(
                        Static(self._render_option(label, description), markup=True),
                        id=f"plan-decision-{decision}",
                    )
                    for decision, label, description in self._OPTIONS
                ],
                id="plan-decision-options",
            )
            yield Static(
                "↑/↓ choose · Enter confirm · Esc continue conversation",
                id="plan-decision-help",
            )

    def on_mount(self) -> None:
        self.query_one("#plan-decision-options", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        decision = self._OPTIONS[event.index][0]
        self.dismiss(decision)

    def action_revise(self) -> None:
        self.dismiss("revise")

    @staticmethod
    def _render_option(label: str, description: str) -> str:
        return f"[bold]{label}[/bold]\n[dim]{description}[/dim]"
