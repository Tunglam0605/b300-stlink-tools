"""Qt workers that keep OpenOCD operations off the UI thread."""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, Signal, Slot


class FunctionWorker(QObject):
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[Callable[[str], None]], object]) -> None:
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(self.log.emit)
        except Exception as error:  # UI boundary must surface hardware errors.
            self.failed.emit("%s\n%s" % (error, traceback.format_exc()))
        else:
            self.finished.emit(result)
