"""Reusable operator-first dialogs for B300 ST-Link Tools."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget
from b300_gui.theme import ThemeManager


class TechnicalDetailsDialog(QDialog):
    """Non-destructive details window with an embeddable body."""
    def __init__(self, title: str, heading: str, description: str = "", parent: Optional[QWidget] = None, *, minimum_size: tuple[int, int] = (640, 440)) -> None:
        super().__init__(parent)
        self.setObjectName("technicalDetailsDialog")
        self.setWindowTitle(title)
        self.setMinimumSize(*minimum_size)
        self.setModal(False)
        p = ThemeManager.instance().palette
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title_label = QLabel(heading)
        title_label.setObjectName("detailsDialogTitle")
        title_label.setStyleSheet(f"font-size: 16px; font-weight: 800; color: {p.text};")
        root.addWidget(title_label)
        self.description_label = QLabel(description)
        self.description_label.setObjectName("detailsDialogDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setStyleSheet(f"font-size: 12px; color: {p.text_secondary};")
        self.description_label.setVisible(bool(description))
        root.addWidget(self.description_label)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(10)
        root.addWidget(self.body, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        close_button = QPushButton("Đóng")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.close)
        actions.addWidget(close_button)
        root.addLayout(actions)

    def open_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


class SafetyActionDialog(QDialog):
    """Focused warning/confirmation dialog with optional hidden details."""
    def __init__(self, title: str, heading: str, message: str, *, details: str = "", confirm_text: str = "Tiếp tục", cancel_text: str = "Hủy", severity: str = "warning", required_text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("safetyActionDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(520)
        p = ThemeManager.instance().palette
        key = severity if severity in {"warning", "danger", "info"} else "warning"
        color_map = {
            "warning": ("⚠", p.warning_light, p.warning, p.warning),
            "danger": ("!", p.danger_light, p.danger, p.danger),
            "info": ("i", p.primary_light, p.primary, p.accent_cyan),
        }
        icon, bg, border, fg = color_map[key]
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(12)
        badge = QLabel(icon)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(44, 44)
        badge.setStyleSheet(f"font-size: 20px; font-weight: 800; background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 10px;")
        header.addWidget(badge)
        stack = QVBoxLayout()
        stack.setSpacing(3)
        h = QLabel(heading)
        h.setObjectName("safetyDialogHeading")
        h.setWordWrap(True)
        h.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {p.text};")
        stack.addWidget(h)
        m = QLabel(message)
        m.setObjectName("safetyDialogMessage")
        m.setWordWrap(True)
        m.setStyleSheet(f"font-size: 12px; color: {p.text_secondary};")
        stack.addWidget(m)
        header.addLayout(stack, 1)
        root.addLayout(header)
        self.details_button = QPushButton("Xem chi tiết")
        self.details_button.setObjectName("ghostButton")
        self.details_button.setVisible(bool(details))
        root.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.details_view = QTextBrowser()
        self.details_view.setObjectName("whatsNewNotes")
        self.details_view.setPlainText(details)
        self.details_view.setMinimumHeight(140)
        self.details_view.setVisible(False)
        root.addWidget(self.details_view)
        self.details_button.clicked.connect(self._toggle_details)
        self.confirm_input = QLineEdit()
        self.confirm_input.setObjectName("safetyConfirmationInput")
        self.confirm_input.setPlaceholderText(required_text)
        self.confirm_input.setAccessibleName("Xác nhận thao tác nguy hiểm")
        self.confirm_input.setVisible(bool(required_text))
        if required_text:
            root.addWidget(QLabel("Nhập chính xác: %s" % required_text))
            root.addWidget(self.confirm_input)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton(cancel_text)
        self.cancel_button.setObjectName("ghostButton")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.confirm_button = QPushButton(confirm_text)
        if key == "danger":
            self.confirm_button.setObjectName("dangerButton")
        else:
            self.confirm_button.setObjectName("primaryButton")
        self.confirm_button.setDefault(True)
        if required_text:
            self.confirm_button.setEnabled(False)
            self.confirm_input.textChanged.connect(
                lambda value: self.confirm_button.setEnabled(value == required_text)
            )
        self.confirm_button.clicked.connect(self.accept)
        actions.addWidget(self.confirm_button)
        root.addLayout(actions)

    def _toggle_details(self) -> None:
        visible = not self.details_view.isVisible()
        self.details_view.setVisible(visible)
        self.details_button.setText("Ẩn chi tiết" if visible else "Xem chi tiết")
        self.adjustSize()

    @classmethod
    def confirm(cls, parent: QWidget, title: str, heading: str, message: str, *, details: str = "", confirm_text: str = "Tiếp tục", severity: str = "warning", required_text: str = "") -> bool:
        d = cls(title, heading, message, details=details, confirm_text=confirm_text, severity=severity, required_text=required_text, parent=parent)
        return d.exec() == QDialog.DialogCode.Accepted
