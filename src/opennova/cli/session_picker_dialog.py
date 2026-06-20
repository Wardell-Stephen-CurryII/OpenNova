"""TUI dialog for selecting a saved session to resume."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static

from opennova.session import SessionMeta, format_session_title_snippet


class SessionPickerDialog(ModalScreen[str | None]):
    """Modal session picker for TUI resume flows."""

    CSS = """
    SessionPickerDialog {
        align: center middle;
    }

    #session-picker-card {
        width: 84;
        max-width: 94%;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }

    #session-picker-title {
        margin-bottom: 1;
    }

    #session-picker-list {
        height: auto;
        max-height: 18;
        margin-top: 1;
        margin-bottom: 1;
    }

    #session-picker-help {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, sessions: list[SessionMeta]) -> None:
        super().__init__()
        self.sessions = sessions

    def compose(self) -> ComposeResult:
        with Container(id="session-picker-card"):
            yield Static("[bold]Resume Session[/bold]", id="session-picker-title", markup=True)
            yield Static(
                "[dim]Choose a saved session to restore into the TUI.[/dim]",
                markup=True,
            )
            yield ListView(
                *[
                    ListItem(
                        Static(self._render_session(session), markup=True),
                        id=f"session-option-{index}",
                    )
                    for index, session in enumerate(self.sessions)
                ],
                id="session-picker-list",
            )
            yield Static(
                "↑/↓ choose · Enter resume · Esc cancel",
                id="session-picker-help",
            )

    def on_mount(self) -> None:
        self.query_one("#session-picker-list", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(self.sessions[event.index].session_id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _render_session(self, session: SessionMeta) -> str:
        title = format_session_title_snippet(session.first_prompt or "Untitled session")
        timestamp = datetime.fromtimestamp(session.modified).strftime("%Y-%m-%d %H:%M")
        return (
            f"[bold]{title}[/bold]\n"
            f"[dim]{timestamp}  ·  {session.message_count} msgs  ·  {session.session_id[:8]}[/dim]"
        )
