"""One-time release notes shown after a successful product upgrade."""

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)


class WhatsNewDialog(QDialog):
    def __init__(self, version: str, notes: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Có gì mới trong B300 ST-Link Tools %s" % version)
        self.setMinimumSize(540, 400)
        layout = QVBoxLayout(self)
        title = QLabel("Đã cập nhật lên phiên bản %s" % version)
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #0369A1;")
        layout.addWidget(title)
        self.notes_view = QPlainTextEdit()
        self.notes_view.setReadOnly(True)
        self.notes_view.setPlainText(notes)
        layout.addWidget(self.notes_view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
