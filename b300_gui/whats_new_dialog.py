"""One-time release notes shown after a successful product upgrade."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout,
)


class WhatsNewDialog(QDialog):
    def __init__(self, version: str, notes: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Có gì mới trong B300 ST-Link Tools %s" % version)
        self.setMinimumSize(600, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Đã cập nhật lên phiên bản %s" % version)
        title.setObjectName("pageContextTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #38BDF8;")
        layout.addWidget(title)

        self.notes_view = QTextBrowser()
        self.notes_view.setObjectName("whatsNewNotes")
        self.notes_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.notes_view.setOpenExternalLinks(True)
        self.notes_view.setMarkdown(notes)
        layout.addWidget(self.notes_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText("Đóng")
            close_button.setObjectName("primaryButton")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
