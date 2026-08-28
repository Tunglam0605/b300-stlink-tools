"""Product About dialog with release and runtime identity."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton, QVBoxLayout,
)


class AboutDialog(QDialog):
    check_updates_requested = Signal()

    def __init__(self, version: str, core_version: str, build_commit: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Giới thiệu B300 ST-Link Tools")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        title = QLabel("B300 ST-Link Tools")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)
        form = QFormLayout()
        self.version_value = QLabel(version)
        self.core_value = QLabel(core_version)
        self.build_value = QLabel(build_commit)
        self.openocd_value = QLabel("OpenOCD xPack 0.12.0-7")
        self.target_value = QLabel("STM32F407 · Application 0x08010000")
        form.addRow("Phiên bản:", self.version_value)
        form.addRow("Core:", self.core_value)
        form.addRow("Build commit:", self.build_value)
        form.addRow("Runtime:", self.openocd_value)
        form.addRow("Target:", self.target_value)
        layout.addLayout(form)
        self.check_button = QPushButton("Kiểm tra cập nhật")
        self.check_button.clicked.connect(self.check_updates_requested.emit)
        layout.addWidget(self.check_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText("Đóng")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
