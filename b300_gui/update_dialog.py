"""User-facing update notification, download progress, and install gate."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QTextBrowser, QVBoxLayout, QWidget,
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
        self.setMinimumSize(520, 300)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        # Header Title with Badge Icon
        title = QLabel("✨ Có bản cập nhật mới")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #0284C7;")
        root.addWidget(title)

        # Cockpit-style Version Summary Card
        version_card = QWidget()
        version_card.setObjectName("versionCard")
        version_card.setStyleSheet(
            "QWidget#versionCard { background-color: #F0F9FF; border: 1px solid #BAE6FD; "
            "border-radius: 8px; padding: 10px 14px; }"
        )
        card_layout = QVBoxLayout(version_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        self.new_version_value = QLabel(str(release.version))
        self.new_version_value.setStyleSheet(
            "font-size: 16px; font-weight: 800; color: #0369A1; font-family: 'Cascadia Code', 'Consolas', monospace;"
        )
        card_layout.addWidget(self.new_version_value)

        self.current_version_value = QLabel(current_version)
        self.current_version_value.setVisible(False)  # Preserved for API/tests

        subtitle = QLabel("Phiên bản hiện tại v%s · Đã sẵn sàng tải về và nâng cấp an toàn." % current_version)
        subtitle.setStyleSheet("color: #475569; font-size: 12px;")
        card_layout.addWidget(subtitle)
        root.addWidget(version_card)

        # Release details stay hidden until the user asks for them.
        self.details_button = QPushButton("Xem thay đổi")
        self.details_button.setObjectName("updateDetailsButton")
        root.addWidget(self.details_button)

        notes_label = QLabel("Thay đổi trong bản mới")
        notes_label.setStyleSheet("font-weight: 700; color: #334155; font-size: 13px; margin-top: 4px;")
        notes_label.setVisible(False)
        root.addWidget(notes_label)
        self.notes_label = notes_label

        self.notes_view = QTextBrowser()
        self.notes_view.setOpenExternalLinks(True)
        self.notes_view.setMarkdown(release.notes)
        self.notes_view.setVisible(False)
        self.notes_view.setMinimumHeight(180)
        root.addWidget(self.notes_view, 1)
        self.details_button.clicked.connect(self._toggle_details)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.install_reason = QLabel("")
        self.install_reason.setWordWrap(True)
        self.install_reason.setStyleSheet("color: #B45309; font-size: 12px;")
        root.addWidget(self.install_reason)

        # Cockpit-style Action Footer
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.later_button = QPushButton("Để sau")
        self.later_button.clicked.connect(self.close)
        self.release_button = QPushButton("Trang phát hành")
        self.release_button.clicked.connect(
            lambda: self.release_requested.emit(self.release.release_page)
        )
        self.action_button = QPushButton("Tải bản cập nhật")
        self.action_button.setObjectName("updateActionButton")
        self.action_button.clicked.connect(self._request_action)
        actions.addWidget(self.later_button)
        self.release_button.setVisible(False)
        actions.addWidget(self.release_button)
        actions.addStretch(1)
        actions.addWidget(self.action_button)
        root.addLayout(actions)


    def _toggle_details(self) -> None:
        visible = not self.notes_view.isVisible()
        self.notes_label.setVisible(visible)
        self.notes_view.setVisible(visible)
        self.release_button.setVisible(visible)
        self.details_button.setText("Ẩn thay đổi" if visible else "Xem thay đổi")
        self.adjustSize()

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
