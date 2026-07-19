"""Small typed-friendly multi-subscriber event bus for runtime/UI boundaries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from threading import RLock
from typing import Any


class RuntimeEventBus:
    """Publish runtime events to any number of independently removable subscribers."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._listeners: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def subscribe(self, event_type: str, listener: Callable[..., Any]) -> Callable[[], None]:
        with self._lock:
            if listener not in self._listeners[event_type]:
                self._listeners[event_type].append(listener)

        def unsubscribe() -> None:
            with self._lock:
                listeners = self._listeners.get(event_type, [])
                if listener in listeners:
                    listeners.remove(listener)
                if not listeners:
                    self._listeners.pop(event_type, None)

        return unsubscribe

    def publish(self, event_type: str, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            listeners = tuple(self._listeners.get(event_type, ()))
        for listener in listeners:
            with suppress(Exception):
                listener(*args, **kwargs)

    def clear(self) -> None:
        with self._lock:
            self._listeners.clear()

    def listener_count(self, event_type: str) -> int:
        with self._lock:
            return len(self._listeners.get(event_type, ()))

    def latest(self, event_type: str) -> Callable[..., Any] | None:
        """Return the newest listener for APIs that still require one callback."""
        with self._lock:
            listeners = self._listeners.get(event_type, ())
            return listeners[-1] if listeners else None
