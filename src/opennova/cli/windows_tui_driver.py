"""Windows Textual driver helpers with better IME character handling.

Textual's stock Windows driver enables virtual-terminal input and drops some
``VK == 0`` key events when modifier state is present. Some Windows IMEs commit
Chinese/Japanese/Korean characters through exactly that path: the committed
Unicode character is present, but the virtual key code is zero. OpenNova keeps
the Textual TUI on Windows by using a small driver shim that preserves those
printable Unicode characters.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any


def should_queue_console_key(
    *,
    key: str,
    key_down: bool,
    control_key_state: int,
    virtual_key_code: int,
) -> bool:
    """Return whether a Windows console key record should be parsed as text."""
    if not key_down or not key or key == "\x00":
        return False

    if control_key_state and virtual_key_code == 0:
        return key.isprintable()

    return True


def get_ime_friendly_windows_driver_class() -> type[Any]:
    """Return the Windows-only Textual driver class used by OpenNova TUI."""
    if sys.platform != "win32":
        raise RuntimeError("The IME-friendly Textual driver is only available on Windows")

    return _build_ime_friendly_windows_driver_class()


def _build_ime_friendly_windows_driver_class() -> type[Any]:
    import asyncio
    import threading
    from asyncio import AbstractEventLoop, run_coroutine_threadsafe

    from textual import constants, events
    from textual._xterm_parser import XTermParser
    from textual.drivers import win32
    from textual.drivers._writer_thread import WriterThread
    from textual.drivers.windows_driver import WindowsDriver
    from textual.geometry import Size

    key_event_type = 0x0001
    window_buffer_size_event_type = 0x0004

    left_alt_pressed = 0x0002
    right_alt_pressed = 0x0001
    shift_pressed = 0x0010
    left_ctrl_pressed = 0x0008
    right_ctrl_pressed = 0x0004

    special_keys = {
        8: "backspace",
        9: "tab",
        13: "enter",
        27: "escape",
        33: "pageup",
        34: "pagedown",
        35: "end",
        36: "home",
        37: "left",
        38: "up",
        39: "right",
        40: "down",
        45: "insert",
        46: "delete",
        **{code: f"f{code - 111}" for code in range(112, 124)},
    }

    def enable_ime_application_mode(mouse: bool) -> Callable[[], None]:
        terminal_in = sys.__stdin__
        terminal_out = sys.__stdout__

        current_console_mode_in = win32.get_console_mode(terminal_in)
        current_console_mode_out = win32.get_console_mode(terminal_out)

        def restore() -> None:
            win32.set_console_mode(terminal_in, current_console_mode_in)
            win32.set_console_mode(terminal_out, current_console_mode_out)

        win32.set_console_mode(
            terminal_out,
            current_console_mode_out | win32.ENABLE_VIRTUAL_TERMINAL_PROCESSING,
        )

        input_mode = current_console_mode_in
        input_mode &= ~(
            win32.ENABLE_ECHO_INPUT | win32.ENABLE_LINE_INPUT | win32.ENABLE_PROCESSED_INPUT
        )
        input_mode |= win32.ENABLE_WINDOW_INPUT
        if mouse:
            input_mode |= win32.ENABLE_MOUSE_INPUT | win32.ENABLE_EXTENDED_FLAGS
            input_mode &= ~win32.ENABLE_QUICK_EDIT_MODE

        win32.set_console_mode(terminal_in, input_mode)
        return restore

    def format_special_key(virtual_key_code: int, control_key_state: int) -> str | None:
        key = special_keys.get(virtual_key_code)
        if key is None:
            return None

        if key == "tab" and control_key_state & shift_pressed:
            return "shift+tab"

        modifiers: list[str] = []
        if control_key_state & (left_ctrl_pressed | right_ctrl_pressed):
            modifiers.append("ctrl")
        if control_key_state & shift_pressed:
            modifiers.append("shift")
        if control_key_state & (left_alt_pressed | right_alt_pressed):
            modifiers.append("alt")

        return "+".join([*modifiers, key]) if modifiers else key

    class IMEFriendlyWindowsEventMonitor(threading.Thread):
        """Thread that sends Windows console input events to Textual."""

        def __init__(
            self,
            loop: AbstractEventLoop,
            app: Any,
            exit_event: threading.Event,
            process_event: Callable[[events.Event], None],
        ) -> None:
            self.loop = loop
            self.app = app
            self.exit_event = exit_event
            self.process_event = process_event
            super().__init__(name="opennova-textual-input")

        def run(self) -> None:
            exit_requested = self.exit_event.is_set
            parser = XTermParser(debug=constants.DEBUG)

            try:
                read_count = win32.wintypes.DWORD(0)
                input_handle = win32.GetStdHandle(win32.STD_INPUT_HANDLE)

                max_events = 1024
                arrtype = win32.INPUT_RECORD * max_events
                input_records = arrtype()
                read_console_input = win32.KERNEL32.ReadConsoleInputW
                queued_keys: list[str] = []

                def flush_queued_keys() -> None:
                    if not queued_keys:
                        return
                    text = "".join(queued_keys).encode("utf-16", "surrogatepass").decode("utf-16")
                    queued_keys.clear()
                    for parsed_event in parser.feed(text):
                        self.process_event(parsed_event)

                while not exit_requested():
                    for parsed_event in parser.tick():
                        self.process_event(parsed_event)

                    if win32.wait_for_handles([input_handle], 100) is None:
                        continue

                    read_console_input(
                        input_handle,
                        win32.byref(input_records),
                        max_events,
                        win32.byref(read_count),
                    )

                    new_size: tuple[int, int] | None = None
                    for input_record in input_records[: read_count.value]:
                        event_type = input_record.EventType

                        if event_type == key_event_type:
                            key_event = input_record.Event.KeyEvent
                            key = key_event.uChar.UnicodeChar
                            control_state = key_event.dwControlKeyState
                            virtual_key_code = key_event.wVirtualKeyCode

                            if should_queue_console_key(
                                key=key,
                                key_down=bool(key_event.bKeyDown),
                                control_key_state=control_state,
                                virtual_key_code=virtual_key_code,
                            ):
                                queued_keys.append(key)
                                continue

                            if key_event.bKeyDown:
                                special_key = format_special_key(
                                    virtual_key_code,
                                    control_state,
                                )
                                if special_key is not None:
                                    flush_queued_keys()
                                    self.process_event(events.Key(special_key, None))

                        elif event_type == window_buffer_size_event_type:
                            size = input_record.Event.WindowBufferSizeEvent.dwSize
                            new_size = (size.X, size.Y)

                    flush_queued_keys()
                    if new_size is not None:
                        self.on_size_change(*new_size)

            except Exception as error:
                self.app.log.error("EVENT MONITOR ERROR", error)

        def on_size_change(self, width: int, height: int) -> None:
            size = Size(width, height)
            event = events.Resize(size, size)
            run_coroutine_threadsafe(self.app._post_message(event), loop=self.loop)

    class IMEFriendlyWindowsDriver(WindowsDriver):
        """Windows Textual driver that preserves IME-committed Unicode text."""

        def start_application_mode(self) -> None:
            loop = asyncio.get_running_loop()

            self._restore_console = enable_ime_application_mode(mouse=self._mouse)

            self._writer_thread = WriterThread(self._file)
            self._writer_thread.start()

            self.write("\x1b[?1049h")  # Enable alt screen
            self._enable_mouse_support()
            self.write("\x1b[?25l")  # Hide cursor
            self.write("\033[?1004h")  # Enable FocusIn/FocusOut.
            self.write("\x1b[>1u")  # Kitty keyboard protocol.
            self.flush()
            self._enable_bracketed_paste()

            self._event_thread = IMEFriendlyWindowsEventMonitor(
                loop,
                self._app,
                self.exit_event,
                self.process_message,
            )
            self._event_thread.start()

    return IMEFriendlyWindowsDriver
