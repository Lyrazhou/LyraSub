"""Qt worker primitives for long-running local transcription and LLM requests."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


class TaskCancelledError(RuntimeError):
    """Raised when the user requests cancellation of a background task."""

    marker = "[TASK_CANCELLED]"

    def __init__(self, message: str = "任务已停止；未确认的结果不会写入项目。") -> None:
        super().__init__(f"{self.marker} {message}")


class MainThreadTaskBridge(QObject):
    """Receive worker signals in the GUI thread and re-emit them safely to UI callbacks."""

    progress = Signal(str, object)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    @Slot(str, object)
    def deliver_progress(self, message: str, value: object) -> None:
        self.progress.emit(message, value)

    @Slot(object)
    def deliver_success(self, result: object) -> None:
        self.succeeded.emit(result)

    @Slot(str)
    def deliver_failure(self, message: str) -> None:
        self.failed.emit(message)

    @Slot()
    def deliver_finished(self) -> None:
        self.finished.emit()


class TaskWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str, object)
    finished = Signal()

    def __init__(
        self, operation: Callable[[Callable[[str, int | None], None], Event], Any]
    ) -> None:
        super().__init__()
        self.operation = operation
        self.cancel_event = Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(
                lambda message, value=None: self.progress.emit(message, value), self.cancel_event
            )
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
