"""Task-first production Pulse surface; controllers own all real operations."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .pulse_style import pulse_stylesheet
from .pulse_widgets import ElidedLabel, PulseBrand, PulseTitle


class PulseView(QWidget):
    setup_requested = Signal(); login_requested = Signal(); options_requested = Signal(); project_manager_requested = Signal()
    project_selected = Signal(str); connection_selected = Signal(str); file_requested = Signal(); action_requested = Signal(str); stop_requested = Signal(); mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent); self.setObjectName("PulseView"); self.setFont(QFont("Segoe UI", 10)); self.setMinimumSize(720, 480); self._filling = False; self._mode = "debug"
        root = QVBoxLayout(self); root.setContentsMargins(28, 24, 28, 20); root.setSpacing(18)
        toolbar = QFrame(); toolbar.setObjectName("PulseToolbar"); top = QHBoxLayout(toolbar); top.setContentsMargins(14, 10, 14, 10)
        self.setup_button = self._quiet("Thiết lập máy", self.setup_requested.emit); self.login_button = self._quiet("Đăng nhập SSH", self.login_requested.emit); self.options_button = self._quiet("Tùy chọn", self.options_requested.emit)
        top.addWidget(self.setup_button); top.addWidget(self.login_button); top.addStretch(1); top.addWidget(self.options_button); root.addWidget(toolbar)
        root.addStretch(1)
        workspace = QFrame(); workspace.setObjectName("PulseWorkspace"); workspace.setMinimumHeight(380); workspace.setMaximumHeight(440); work = QHBoxLayout(workspace); work.setContentsMargins(32, 28, 32, 28); work.setSpacing(32)
        self.stack = QStackedWidget(); self.stack.setObjectName("PulseStack"); self.debug_page = self._debug_page(); self.program_page = self._program_page()
        for page in (self.debug_page, self.program_page): self.stack.addWidget(page)
        # Native font metrics differ across platforms; the workspace bounds the
        # panel while its layout keeps the full title and controls visible.
        self.stack.setMaximumWidth(500); work.addWidget(self.stack, 1, Qt.AlignmentFlag.AlignVCenter)
        self.illustration = PulseBrand(); work.addWidget(self.illustration, 0, Qt.AlignmentFlag.AlignVCenter); root.addWidget(workspace)
        root.addStretch(1)
        dock = QFrame(); dock.setObjectName("PulseDock"); dl = QHBoxLayout(dock); dl.setContentsMargins(12, 10, 12, 10); dl.setSpacing(8)
        self.nav = {mode: self._nav(text, mode) for mode, text in (("debug", "Debug"), ("program", "Nạp code"))}
        for button in self.nav.values(): dl.addWidget(button)
        dl.addStretch(1); self.connection_status = self._status("Chưa đăng nhập SSH"); dl.addWidget(self.connection_status); self.stop_button = self._quiet("Dừng", self.stop_requested.emit); self.stop_button.hide(); dl.addWidget(self.stop_button); root.addWidget(dock)
        self.setStyleSheet(pulse_stylesheet(False)); self.set_mode("debug")

    def _quiet(self, text, callback):
        item = QPushButton(text); item.setObjectName("PulseQuiet"); item.clicked.connect(callback); return item
    def _primary(self, text, action):
        item = QPushButton(text); item.setObjectName("PulsePrimary"); item.clicked.connect(lambda: self.action_requested.emit(action)); return item
    def _nav(self, text, mode):
        item = QPushButton(text); item.setObjectName("PulseNav"); item.setCheckable(True); item.clicked.connect(lambda: self.set_mode(mode)); return item
    def _page(self, title):
        page = QWidget(); page.setObjectName("PulseTaskPage"); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0); layout.setSpacing(16); layout.addStretch(1); title_widget = PulseTitle(title); layout.addWidget(title_widget); self._titles = getattr(self, "_titles", []) + [title_widget]; return page, layout
    def _combo(self):
        combo = QComboBox(); combo.setMinimumWidth(300); return combo
    def _status(self, text):
        item = QLabel(text); item.setObjectName("PulseStatus"); return item
    def _debug_page(self):
        page, l = self._page("Debug"); l.addWidget(QLabel("Dự án")); self.debug_project = self._combo(); self.debug_project.activated.connect(self._project_activated)
        project_row = QHBoxLayout(); project_row.setContentsMargins(0, 0, 0, 0); project_row.setSpacing(8); project_row.addWidget(self.debug_project, 1)
        self.debug_add_project = self._quiet("Thêm dự án", self.project_manager_requested.emit); self.debug_add_project.hide(); project_row.addWidget(self.debug_add_project); l.addLayout(project_row)
        l.addWidget(QLabel("Kết nối")); self.debug_connection = self._combo(); self.debug_connection.activated.connect(self._connection_activated); l.addWidget(self.debug_connection)
        self.status_label = self._status("Sẵn sàng"); self.status = self.status_label; l.addWidget(self.status_label); self.debug_start = self._primary("Mở debug", "debug"); l.addWidget(self.debug_start); l.addStretch(1); return page
    def _program_page(self):
        page, l = self._page("Nạp code"); self.program_file = ElidedLabel("Chưa chọn tệp"); self.program_file.setObjectName("PulseCaption"); l.addWidget(self.program_file); self.file_button = self._quiet("Chọn tệp", self.file_requested.emit); l.addWidget(self.file_button); l.addWidget(QLabel("Nạp bằng ST-Link cắm vào máy này."))
        self.program_progress = QProgressBar(); self.program_progress.hide(); l.addWidget(self.program_progress); self.program_status = self._status("Sẵn sàng chọn tệp"); l.addWidget(self.program_status); self.program_start = self._primary("Nạp code", "program"); l.addWidget(self.program_start); l.addStretch(1); return page
    def _project_activated(self, index):
        if not self._filling:
            sender = self.sender(); value = sender.itemData(index) if sender else None
            if value: self.project_selected.emit(str(value))
    def _connection_activated(self, index):
        if not self._filling:
            value = self.debug_connection.itemData(index)
            if value is not None: self.connection_selected.emit(str(value))
    @staticmethod
    def _items(context, name):
        return list(context.get(name, ()) if isinstance(context, dict) else getattr(context, name, ()))
    @staticmethod
    def _value(context, name):
        return context.get(name) if isinstance(context, dict) else getattr(context, name, None)
    def render(self, context, *, busy=False, debug_active=False, ssh_connected=False):
        projects = self._items(context, "project_profiles") or self._items(context, "projects")
        selected_project_item = self._value(context, "selected_project")
        selected_project = getattr(selected_project_item, "project_id", None) or self._value(context, "project_id")
        connections = self._items(context, "connections")
        gateways = self._items(context, "gateways")
        selected_connection_item = self._value(context, "selected_connection")
        selected_connection = getattr(selected_connection_item, "connection_id", None) or self._value(context, "gateway_id")
        if not connections:
            connections = [("local", "Máy này", None)] + [(item.profile_id, item.name, item) for item in gateways]
        else:
            connections = [(item.connection_id, item.name if item.gateway else "Máy này", item.gateway) for item in connections]
        self._filling=True
        for combo in (self.debug_project,):
            combo.clear()
            for project in projects: combo.addItem(project.name, project.project_id)
            combo.setEnabled(bool(projects) and not busy and not debug_active)
            idx=combo.findData(selected_project); combo.setCurrentIndex(idx if idx>=0 else 0)
        self.debug_connection.clear()
        for connection_id, name, _gateway in connections: self.debug_connection.addItem(name, connection_id)
        index=self.debug_connection.findData(selected_connection); self.debug_connection.setCurrentIndex(index if index>=0 else 0); self.debug_connection.setEnabled(not busy and not debug_active)
        self._filling=False
        empty=not projects; self.debug_add_project.setVisible(empty)
        blocked = bool(busy or debug_active)
        for item in (self.setup_button, self.login_button, self.debug_add_project, self.file_button, *self.nav.values()): item.setEnabled(not blocked)
        self.options_button.setEnabled(True)
        selected_gateway = next((gateway for connection_id, _name, gateway in connections if connection_id == selected_connection), None)
        self.debug_start.setEnabled(bool(projects) and not blocked); self.program_start.setEnabled(not blocked)
        self.stop_button.setVisible(bool(debug_active)); self.stop_button.setEnabled(not busy)
        self.connection_status.setText(
            ("Đã kết nối SSH · %s" % selected_gateway.name if ssh_connected else "Chưa đăng nhập SSH")
            if selected_gateway else "Kết nối · Máy này")
    def set_status(self, text, error=False):
        for item in (self.status_label, self.program_status):
            item.setText(str(text)); item.setProperty("error", bool(error)); item.style().unpolish(item); item.style().polish(item)
    def set_file(self, path):
        self.program_file.setText(Path(path).name if path else "Chưa chọn tệp")
        if path:
            self.program_file.setToolTip(str(path))
    def set_dark(self, dark):
        self.setStyleSheet(pulse_stylesheet(bool(dark)))
        for title in self._titles: title.set_dark(bool(dark))
        self.illustration.set_dark(bool(dark))
    def set_mode(self, mode):
        mode = mode if mode in {"debug", "program"} else "debug"
        index={"debug":0,"program":1}[mode]; self.stack.setCurrentIndex(index)
        for name, button in self.nav.items(): button.setChecked(name==mode)
        if mode != self._mode:
            self._mode = mode
            self.mode_changed.emit(mode)

    @property
    def mode(self):
        return self._mode

__all__ = ["PulseView"]
