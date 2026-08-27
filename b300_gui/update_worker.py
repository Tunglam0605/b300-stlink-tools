"""Qt bridges for the UI-independent update client."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal


class UpdateCheckWorker(QThread):
    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, client, current_version: str, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.current_version = current_version

    def run(self) -> None:
        try:
            result = self.client.check(self.current_version)
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)


class UpdateDownloadWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, client, asset, destination: Path, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.asset = asset
        self.destination = Path(destination)
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            result = self.client.download(
                self.asset, self.destination, self.progress.emit, self.cancel_event
            )
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)
