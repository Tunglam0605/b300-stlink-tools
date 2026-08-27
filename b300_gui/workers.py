"""Qt workers that keep OpenOCD operations off the UI thread."""

from __future__ import annotations

import traceback
import threading
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QThread, Signal


@dataclass(frozen=True)
class WorkerFailure:
    phase: str
    message: str
    next_action: str
    traceback: str


class FunctionWorker(QThread):
    log = Signal(str)
    phase = Signal(object)
    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, operation: Callable[[Callable[[str], None]], object],
                 parent=None) -> None:
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            result = self.operation(
                self.log.emit,
                self.phase.emit,
                self.cancel_event,
            )
        except Exception as error:  # UI boundary must surface hardware errors.
            self.failed.emit(WorkerFailure(
                phase=getattr(error, "phase", "operation"),
                message=getattr(error, "reason", str(error)),
                next_action=getattr(
                    error,
                    "next_action",
                    "Review the log, correct the cause, and start a new operation manually.",
                ),
                traceback=traceback.format_exc(),
            ))
        else:
            self.completed.emit(result)
