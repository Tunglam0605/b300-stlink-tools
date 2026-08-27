"""User-facing update notification, download progress, and install gate."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QVBoxLayout,
)

from b300_core.release_manifest import LatestRelease, ReleaseAsset


class UpdateDialog(QDialog):
    download_requested = Signal()
    install_requested = Signal()
    release_requested = Signal(str)

    def __init__(
            self, current_version: str, release: LatestRelease,
            asset: ReleaseAsset, parent=None) -> None:
        super().__init__(parent)
        self.release = release
        self.asset = asset
        self.ready_package = None
        self.setWindowTitle("Cập nhật B300 ST-Link Tools")
        self.setMinimumSize(520, 390)
        root = QVBoxLayout(self)
        title = QLabel("Có phiên bản mới")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #0369A1;")
        root.addWidget(title)
        form = QFormLayout()
        self.current_version_value = QLabel(current_version)
        self.new_version_value = QLabel(str(release.version))
        form.addRow("Hiện tại:", self.current_version_value)
        form.addRow("Mới:", self.new_version_value)
        root.addLayout(form)
        self.notes_view = QPlainTextEdit()
        self.notes_view.setReadOnly(True)
        self.notes_view.setPlainText(release.notes)
        root.addWidget(self.notes_view, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)
        self.install_reason = QLabel("")
        self.install_reason.setWordWrap(True)
        self.install_reason.setStyleSheet("color: #B45309;")
        root.addWidget(self.install_reason)
        actions = QHBoxLayout()
        self.later_button = QPushButton("Để sau")
        self.later_button.clicked.connect(self.close)
        self.release_button = QPushButton("Xem Release")
        self.release_button.clicked.connect(
            lambda: self.release_requested.emit(self.release.release_page)
        )
        self.action_button = QPushButton("Tải bản cập nhật")
        self.action_button.clicked.connect(self._request_action)
        actions.addWidget(self.later_button)
        actions.addWidget(self.release_button)
        actions.addStretch(1)
        actions.addWidget(self.action_button)
        root.addLayout(actions)

    def _request_action(self) -> None:
        if self.ready_package is None:
            self.download_requested.emit()
        else:
            self.install_requested.emit()

    def set_downloading(self) -> None:
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.action_button.setText("Đang tải…")
        self.action_button.setEnabled(False)
        self.install_reason.setText("")

    def set_download_progress(self, done: int, total: int) -> None:
        percent = int(done * 100 / total) if total else 0
        self.progress.setValue(max(0, min(100, percent)))

    def set_ready(self, package: Path) -> None:
        self.ready_package = Path(package)
        self.progress.setVisible(True)
        self.progress.setValue(100)
        self.action_button.setText("Cài đặt ngay")

    def set_install_allowed(self, allowed: bool, reason: str = "") -> None:
        if self.ready_package is None:
            return
        self.action_button.setEnabled(allowed)
        self.install_reason.setText("" if allowed else reason)
