"""Shared presentation primitives for the production engineering pages."""
from collections import deque
from pathlib import Path
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QComboBox, QFileDialog)


def engineering_icon(name, size=26, color=None):
    """Consistent scalable line icons, tinted by the active theme."""
    from b300_gui.theme import ThemeManager
    paths = {
        'program': '<path d="M13 2 3 14h8l-1 8 11-13h-8z"/>',
        'monitor': '<rect x="2.5" y="3.5" width="19" height="14" rx="1.5"/><path d="M6 12.5 9.5 9l3 2.5 5.5-5M8 21h8M12 17.5V21"/>',
        'debug': '<rect x="7" y="7" width="10" height="13" rx="4"/><path d="M9 7V4h6v3M4 9.5h3M17 9.5h3M3 14h4M17 14h4M5 20l3-3M19 20l-3-3M12 8v11"/>',
        'device': '<rect x="6.5" y="6.5" width="11" height="11" rx="1"/><path d="M9 2v4.5M15 2v4.5M9 17.5V22M15 17.5V22M2 9h4.5M2 15h4.5M17.5 9H22M17.5 15H22"/><rect x="9.5" y="9.5" width="5" height="5"/>',
        'settings': '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1z"/>',
        'file': '<path d="M6 2h8l5 5v15H6zM14 2v6h5M9 12h7M9 16h7"/>',
        'shield': '<path d="m12 2 9 4v6c0 5-5 9-9 10-4-1-9-5-9-10V6zM7 12l4 4 6-7"/>',
        'folder': '<path d="M2 6h8l2 3h10l-3 12H2zM2 6V3h8l2 3h9v3"/>',
        'chart': '<path d="M3 3v18h19M6 16l4-6 5 3 6-8"/>',
        'database': '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 4 18 4 18 0V5M3 12c0 4 18 4 18 0"/>',
        'connection': '<rect x="8" y="2" width="8" height="6" rx="1"/><rect x="1" y="17" width="8" height="6" rx="1"/><rect x="15" y="17" width="8" height="6" rx="1"/><path d="M12 8v5H5v4M12 13h7v4"/>',
        'history': '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 3"/>',
        'refresh': '<path d="M20 7v5h-5M4 17v-5h5M6 8a7 7 0 0 1 12-2l2 6M18 16a7 7 0 0 1-12 2l-2-6"/>',
        'play': '<path d="m8 5 11 7-11 7z"/>',
        'stop': '<rect x="6" y="6" width="12" height="12" rx="1"/>',
        'wrench': '<path d="M14 7a5 5 0 0 0-7-4l3 3-4 4-3-3a5 5 0 0 0 6 7l7 7 5-5-7-7"/>',
    }
    color = color or ThemeManager.instance().palette.accent_cyan
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="%s" stroke-width="2" stroke-linecap="square" stroke-linejoin="miter">%s</g></svg>' % (color, paths.get(name, paths['device']))
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class SectionCard(QFrame):
    def __init__(self, title, subtitle='', parent=None, *, icon=None):
        super().__init__(parent)
        self.setObjectName('engineeringCard')
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(12)
        self.header_layout = QHBoxLayout()
        self.header_layout.setSpacing(12)
        if icon:
            self.icon_tile = QLabel()
            self.icon_tile.setObjectName('iconTile')
            self.icon_tile.setFixedSize(34, 34)
            self.icon_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.icon_tile.setPixmap(engineering_icon(icon, 24).pixmap(24, 24))
            self.header_layout.addWidget(self.icon_tile)
        self.header_text_layout = QVBoxLayout()
        self.header_text_layout.setSpacing(3)
        self.title_label = QLabel(title)
        self.title_label.setObjectName('sectionTitle')
        self.header_text_layout.addWidget(self.title_label)
        if subtitle:
            label = QLabel(subtitle)
            label.setObjectName('sectionSubtitle')
            label.setWordWrap(True)
            self.header_text_layout.addWidget(label)
        self.header_layout.addLayout(self.header_text_layout, 1)
        self.body.addLayout(self.header_layout)


class ActivityLogPanel(SectionCard):
    """Bounded actual messages, filtered without inventing log severity."""
    def __init__(self, title='Nhật ký hoạt động', parent=None):
        super().__init__(title, parent=parent, icon='file')
        self._messages = deque(maxlen=1000)
        actions = QHBoxLayout()
        self.clear_button = QPushButton('Xóa nhật ký')
        self.save_button = QPushButton('Lưu nhật ký…')
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(['Tất cả', 'ERROR', 'WARNING', 'INFO'])
        actions.addStretch(1)
        for widget in (self.clear_button, self.save_button, self.filter_combo):
            actions.addWidget(widget)
        self.header_layout.addLayout(actions)
        self.terminal = QPlainTextEdit()
        self.terminal.setObjectName('terminalView')
        self.terminal.setReadOnly(True)
        self.terminal.document().setMaximumBlockCount(1000)
        self.terminal.setMinimumHeight(60)
        self.terminal.setMaximumHeight(110)
        self.body.addWidget(self.terminal)
        self.clear_button.clicked.connect(self.clear)
        self.save_button.clicked.connect(self.save)
        self.filter_combo.currentTextChanged.connect(self._render)

    def append(self, text):
        self._messages.extend(str(text).splitlines())
        self._render()

    def _render(self, *_):
        level = self.filter_combo.currentText()
        messages = list(self._messages)
        if level != 'Tất cả':
            messages = [m for m in messages if level in m.upper()]
        self.terminal.setPlainText('\n'.join(messages))
        self.terminal.verticalScrollBar().setValue(self.terminal.verticalScrollBar().maximum())

    def clear(self):
        self._messages.clear()
        self._render()

    def save(self):
        filename, _ = QFileDialog.getSaveFileName(self, 'Lưu nhật ký', 'b300-log.txt', 'Tệp văn bản (*.txt)')
        if filename:
            Path(filename).write_text('\n'.join(self._messages), encoding='utf-8')


def engineering_stylesheet(p):
    return f'''
    QMainWindow, QWidget#centralWidget, QWidget#programViewContainer, QStackedWidget#v18MainStack {{
        background: {p.canvas};
        color: {p.text};
    }}
    QScrollArea {{ background: transparent; border: none; }}

    QFrame#headerBar {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {p.surface_sunken}, stop:1 {p.canvas});
        border: none;
        border-bottom: 1px solid {p.border};
    }}
    QFrame#sidebarPanel {{
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {p.surface_sunken}, stop:1 {p.canvas});
        border: none;
        border-right: 1px solid {p.border};
        min-width: 206px; max-width: 206px;
    }}
    QPushButton#navButton {{
        min-height: 48px;
        text-align: left;
        padding: 0 15px;
        margin: 3px 10px;
        border: 1px solid transparent;
        border-radius: 9px;
        color: {p.text_secondary};
        background: transparent;
        font-size: 12.5px;
        font-weight: 650;
    }}
    QPushButton#navButton:hover {{
        color: {p.text};
        background: {p.surface};
        border-color: {p.border};
    }}
    QPushButton#navButton:checked {{
        color: {p.text};
        background: {p.primary_light};
        border: 1px solid {p.primary};
        font-weight: 750;
    }}

    QLabel#engineeringPageTitle {{
        font-size: 21px; font-weight: 800; color: {p.text};
        letter-spacing: .2px;
    }}
    QLabel#headerBrandTitle {{ font-size: 15px; font-weight: 800; color: {p.text}; }}
    QFrame#pageContextHeader {{ background: transparent; border: none; }}

    QFrame#engineeringCard {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 10px;
    }}
    QFrame#engineeringCard:hover {{ border-color: {p.border_strong}; }}
    QLabel#sectionTitle {{ font-size: 13px; font-weight: 750; color: {p.text}; border: none; }}
    QLabel#sectionSubtitle {{ font-size: 11px; color: {p.text_secondary}; border: none; }}
    QLabel#iconTile {{
        background: {p.primary_light};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 4px;
    }}
    QFrame#resourceTile, QFrame#toolTile {{
        background: {p.surface_raised};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 7px;
    }}
    QFrame#resourceTile:hover, QFrame#toolTile:hover {{ border-color: {p.primary}; }}

    QFrame#sharedContextBar {{ background: transparent; border: none; }}
    QLabel#contextFieldLabel {{
        color: {p.text_muted}; border: none;
        font-size: 9.5px; font-weight: 800; letter-spacing: .7px;
    }}
    QLabel#contextTarget {{ color: {p.text}; font-weight: 700; font-size: 11.5px; border: none; }}
    QLabel#contextConnectionStatus {{ color: {p.text_secondary}; font-size: 10.5px; border: none; }}
    QComboBox {{
        min-height: 32px;
        padding: 0 10px;
        border: 1px solid {p.border};
        border-radius: 7px;
        background: {p.surface};
        color: {p.text};
    }}
    QComboBox:hover, QComboBox:focus {{ border-color: {p.primary}; }}
    QPushButton#contextAction {{
        min-height: 32px;
        padding: 0 11px;
        border: 1px solid {p.border};
        border-radius: 7px;
        background: {p.surface};
        color: {p.text_secondary};
        font-weight: 650;
    }}
    QPushButton#contextAction:hover {{ color: {p.text}; border-color: {p.primary}; background: {p.surface_raised}; }}

    QLabel#engineeringPathField {{
        background: {p.surface_sunken};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 8px 10px;
        color: {p.text};
        font-family: "Cascadia Code", "JetBrains Mono", Consolas, monospace;
        font-size: 10.5px;
    }}
    QFrame#memorySegment {{ background: {p.surface_raised}; border: 1px solid {p.border}; border-radius: 7px; }}
    QFrame#memorySegment[region="app"] {{ background: {p.primary_light}; border-color: {p.primary}; }}
    QFrame#memorySegment[region="boot"] {{ background: {p.surface_raised}; border-color: {p.border_strong}; }}
    QFrame#memorySegment[region="meta"] {{ background: {p.surface_sunken}; border-color: {p.border}; }}

    QPushButton#primaryActionButton {{
        min-height: 40px;
        padding: 0 18px;
        border-radius: 8px;
        border: 1px solid {p.accent_cyan};
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {p.primary_hover}, stop:1 {p.primary});
        color: {p.text_on_accent};
        font-size: 12.5px; font-weight: 800;
    }}
    QPushButton#primaryActionButton:hover {{ border-color: {p.primary_hover}; }}
    QPushButton#primaryActionButton:disabled {{
        background: {p.surface_raised}; color: {p.text_muted}; border-color: {p.border};
    }}
    QPushButton#ghostButton {{
        min-height: 36px; padding: 0 13px;
        border: 1px solid {p.border}; border-radius: 8px;
        background: {p.surface_raised}; color: {p.text_secondary};
        font-weight: 650;
    }}
    QPushButton#ghostButton:hover {{ color: {p.text}; border-color: {p.border_strong}; background: {p.surface}; }}
    QPushButton#dangerButton {{
        min-height: 38px; padding: 0 14px;
        border: 1px solid {p.danger}; border-radius: 8px;
        background: transparent; color: {p.danger}; font-weight: 750;
    }}

    QPlainTextEdit#terminalView {{
        background: {p.terminal_bg}; color: {p.text_secondary};
        border: 1px solid {p.border}; border-radius: 8px; padding: 8px;
        font-family: "Cascadia Code", "JetBrains Mono", Consolas, monospace;
        font-size: 10.5px;
    }}
    QLabel[state="neutral"] {{ color: {p.text_secondary}; }}
    QLabel[state="success"] {{ color: {p.success}; font-weight: 700; }}
    QLabel[state="warning"] {{ color: {p.warning}; font-weight: 700; }}
    QLabel[state="failure"] {{ color: {p.danger}; font-weight: 700; }}
    '''
