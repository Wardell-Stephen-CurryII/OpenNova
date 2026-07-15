"""Runtime-owned cancellation primitives for runs and tool resources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any


class CancellationToken:
    """Idempotent cancellation signal shared by one runtime run."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason = "Run cancelled"
        self._callbacks: list[Callable[[str], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "Run cancelled") -> bool:
        """Set the signal once and notify registered resource callbacks."""
        if self.cancelled:
            return False
        self._reason = reason
        self._event.set()
        callbacks = tuple(self._callbacks)
        self._callbacks.clear()
        for callback in callbacks:
            with suppress(Exception):
                callback(reason)
        return True

    def add_callback(self, callback: Callable[[str], None]) -> Callable[[], None]:
        """Register a callback and return an idempotent unsubscriber."""
        if self.cancelled:
            callback(self.reason)
            return lambda: None
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._callbacks.remove(callback)

        return unsubscribe

    async def wait(self) -> str:
        await self._event.wait()
        return self.reason

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError(self.reason)


@dataclass
class RunHandle:
    """Own one active runtime task and its shared cancellation token."""

    run_id: str
    token: CancellationToken = field(default_factory=CancellationToken)
    task: asyncio.Task[Any] | None = None

    @property
    def done(self) -> bool:
        return self.task is None or self.task.done()

    def cancel(self, reason: str = "Run cancelled") -> bool:
        changed = self.token.cancel(reason)
        if self.task is not None and not self.task.done():
            self.task.cancel(reason)
            return True
        return changed

    async def wait(self) -> Any:
        if self.task is None:
            return None
        return await self.task
