"""Serialize blocking Pulse actions and deliver their outcomes on the Qt thread."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from .workers import FunctionWorker


@dataclass(frozen=True)
class _Outcome:
    value: object = None
    error: Exception | None = None


class PulseTasks(QObject):
    busy_changed = Signal(bool)
    completed = Signal(str, object)
    failed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._name = ''
        self._outcome = None

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def start(self, name: str, operation: Callable[[], object]) -> bool:
        if self.busy:
            return False
        def run(log, phase, cancel):
            try:
                return _Outcome(value=operation())
            except Exception as error:
                return _Outcome(error=error)
        worker = FunctionWorker(run, self)
        self._name, self._outcome, self._worker = name, None, worker
        worker.completed.connect(self._receive)
        worker.failed.connect(self._unexpected_failure)
        worker.finished.connect(self._finish)
        self.busy_changed.emit(True)
        worker.start()
        return True

    @Slot(object)
    def _receive(self, outcome):
        self._outcome = outcome

    @Slot(object)
    def _unexpected_failure(self, failure):
        self._outcome = _Outcome(error=RuntimeError(failure.message))

    @Slot()
    def _finish(self, expected_worker=None):
        worker = self._worker
        if worker is None or (expected_worker is not None and worker is not expected_worker):
            return
        # finished() precedes native thread-local cleanup. Keep the ownership
        # gate and wrapper alive until wait() confirms a full join, without
        # blocking Qt's UI thread.
        if not worker.wait(0):
            QTimer.singleShot(1, self, lambda: self._finish(worker))
            return
        worker, name, outcome = self._worker, self._name, self._outcome
        self._worker, self._outcome, self._name = None, None, ''
        worker.operation = None  # Drop closures, including submitted SSH secrets.
        worker.deleteLater()
        self.busy_changed.emit(False)
        if outcome is None:
            self.failed.emit(name, RuntimeError('Thao tác kết thúc nhưng chưa có kết quả.'))
        elif outcome.error is not None:
            self.failed.emit(name, outcome.error)
        else:
            self.completed.emit(name, outcome.value)
