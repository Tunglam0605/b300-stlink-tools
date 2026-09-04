"""Settings / workstation environment view for B300 v0.18."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from b300_core import __version__ as CORE_VERSION
from b300_version import __version__


class SettingsView(QWidget):
    machine_setup_requested = Signal()
    toggle_theme_requested = Signal()
    check_updates_requested = Signal()
    export_support_bundle_requested = Signal()
    about_requested = Signal()
    release_notes_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsViewContainer")
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.layout = QVBoxLayout(content)
        self.layout.setContentsMargins(16, 12, 16, 14)
        self.layout.setSpacing(12)
        self._build_ui()
        scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _build_ui(self) -> None:
        header = QFrame()
        header.setObjectName("headerRibbon")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        title = QLabel("SETTINGS · WORKSTATION & B300 RUNTIME")
        title.setObjectName("sectionTitle")
        header_layout.addWidget(title)
        desc = QLabel(
            "B300 tự quản lý OpenOCD và ARM GDB runtime; VS Code/Cortex-Debug chỉ là giao diện interactive debug bên ngoài."
        )
        desc.setWordWrap(True)
        desc.setObjectName("pageSubtitle")
        header_layout.addWidget(desc)
        self.layout.addWidget(header)

        setup = QFrame()
        setup.setObjectName("cardSurface")
        setup_layout = QVBoxLayout(setup)
        setup_layout.setContentsMargins(14, 12, 14, 12)
        setup_layout.addWidget(self._eyebrow("1. THIẾT LẬP MÁY"))
        setup_desc = QLabel(
            "Setup Wizard kiểm tra/cài các prerequisite do B300 hỗ trợ theo hệ điều hành, ví dụ ST-Link driver trên Windows, "
            "udev trên Linux và SSH khi dùng Gateway/Client. OpenOCD được bundle; ARM GDB được B300 managed trong gói v0.18."
        )
        setup_desc.setWordWrap(True)
        setup_layout.addWidget(setup_desc)
        self.btn_run_setup = QPushButton("⚙ MỞ SETUP WIZARD")
        self.btn_run_setup.setObjectName("primaryActionButton")
        self.btn_run_setup.clicked.connect(self.machine_setup_requested.emit)
        setup_layout.addWidget(self.btn_run_setup)
        self.layout.addWidget(setup)

        debug = QFrame()
        debug.setObjectName("cardSurface")
        debug_layout = QVBoxLayout(debug)
        debug_layout.setContentsMargins(14, 12, 14, 12)
        debug_layout.addWidget(self._eyebrow("2. DEBUG TOOLCHAIN"))
        grid = QGridLayout()
        grid.addWidget(QLabel("OpenOCD"), 0, 0)
        self.lbl_openocd = QLabel("B300 managed · 0.12.0-7")
        grid.addWidget(self.lbl_openocd, 0, 1)
        grid.addWidget(QLabel("ARM GDB"), 0, 2)
        self.lbl_gdb = QLabel("B300 managed · GNU Arm GDB 15.2.1-1.1")
        grid.addWidget(self.lbl_gdb, 0, 3)
        grid.addWidget(QLabel("VS Code"), 1, 0)
        self.lbl_vscode = QLabel("Cài bên ngoài · B300 tự phát hiện")
        grid.addWidget(self.lbl_vscode, 1, 1)
        grid.addWidget(QLabel("Cortex-Debug"), 1, 2)
        self.lbl_cortex = QLabel("marus25.cortex-debug · required cho VS Code debug")
        grid.addWidget(self.lbl_cortex, 1, 3)
        debug_layout.addLayout(grid)
        note = QLabel(
            "C/C++ extension của VS Code được khuyến nghị cho IntelliSense/code navigation nhưng không phải thành phần của debug transport. "
            "B300 không yêu cầu người dùng tự cài OpenOCD hay thêm arm-none-eabi-gdb vào PATH."
        )
        note.setWordWrap(True)
        debug_layout.addWidget(note)
        self.layout.addWidget(debug)

        preference = QFrame()
        preference.setObjectName("cardSurface")
        preference_layout = QVBoxLayout(preference)
        preference_layout.setContentsMargins(14, 12, 14, 12)
        preference_layout.addWidget(self._eyebrow("3. GIAO DIỆN"))
        pref_row = QHBoxLayout()
        pref_row.addWidget(QLabel("Theme"))
        self.btn_toggle_theme = QPushButton("🌓 Sáng / Tối (Ctrl+T)")
        self.btn_toggle_theme.clicked.connect(self.toggle_theme_requested.emit)
        pref_row.addWidget(self.btn_toggle_theme)
        pref_row.addStretch(1)
        preference_layout.addLayout(pref_row)
        self.layout.addWidget(preference)

        version = QFrame()
        version.setObjectName("cardSurface")
        version_layout = QVBoxLayout(version)
        version_layout.setContentsMargins(14, 12, 14, 12)
        version_layout.addWidget(self._eyebrow("4. PHIÊN BẢN / HỖ TRỢ"))
        version_grid = QGridLayout()
        version_grid.addWidget(QLabel("GUI"), 0, 0)
        self.lbl_gui_version = QLabel("v%s" % __version__)
        version_grid.addWidget(self.lbl_gui_version, 0, 1)
        version_grid.addWidget(QLabel("Core"), 0, 2)
        self.lbl_core_version = QLabel("v%s" % CORE_VERSION)
        version_grid.addWidget(self.lbl_core_version, 0, 3)
        version_layout.addLayout(version_grid)
        actions = QHBoxLayout()
        self.btn_check_updates = QPushButton("↻ Kiểm tra cập nhật")
        self.btn_check_updates.clicked.connect(self.check_updates_requested.emit)
        self.btn_release_notes = QPushButton("📋 Release notes")
        self.btn_release_notes.clicked.connect(self.release_notes_requested.emit)
        self.btn_export_support = QPushButton("📦 Support bundle")
        self.btn_export_support.clicked.connect(self.export_support_bundle_requested.emit)
        self.btn_about = QPushButton("ℹ Giới thiệu")
        self.btn_about.clicked.connect(self.about_requested.emit)
        for button in (
            self.btn_check_updates, self.btn_release_notes,
            self.btn_export_support, self.btn_about,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        version_layout.addLayout(actions)
        self.layout.addWidget(version)
        self.layout.addStretch(1)

    @staticmethod
    def _eyebrow(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("eyebrowLabel")
        return label


__all__ = ["SettingsView"]
