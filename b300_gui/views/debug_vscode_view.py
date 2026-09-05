"""Prepare attach-only VS Code debugging from the shared application context."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QBoxLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from b300_gui.widgets.engineering import ActivityLogPanel, SectionCard, engineering_icon


class DebugVsCodeView(QWidget):
    open_local_vscode_requested = Signal(Path, Path)
    open_remote_vscode_requested = Signal(object)
    test_client_connection_requested = Signal(object)
    stop_bridge_requested = Signal()
    refresh_environment_requested = Signal()
    manage_gateways_requested = Signal()
    manage_projects_requested = Signal()

    def __init__(self, parent=None, context=None):
        super().__init__(parent)
        self.context = None
        self._busy = False
        self._bridge_active = False
        self._environment = None
        self._probes = ()
        self._target_info = None
        self._build_ui()
        self.bind_context(context)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        launch = SectionCard('1. Gỡ lỗi với VS Code', 'Chuẩn bị cấu hình chỉ kết nối và mở thư mục dự án.', icon='debug')
        actions = QHBoxLayout()
        self.btn_open_vscode = QPushButton('MỞ GỠ LỖI TRONG VS CODE')
        self.btn_open_vscode.setIcon(engineering_icon('debug', 18))
        self.btn_open_vscode.setObjectName('primaryActionButton')
        self.btn_open_vscode.setMinimumHeight(42)
        self.btn_open_vscode.clicked.connect(self._open_vscode)
        self.btn_open_workspace = QPushButton('Mở thư mục dự án')
        self.btn_open_workspace.setIcon(engineering_icon('folder', 18))
        self.btn_open_workspace.clicked.connect(self._open_workspace)
        self.btn_test_client_conn = QPushButton('Thử kết nối')
        self.btn_test_client_conn.setIcon(engineering_icon('play', 18))
        self.btn_test_client_conn.clicked.connect(self._emit_client_connection_test)
        actions.addWidget(self.btn_open_vscode, 2)
        actions.addWidget(self.btn_open_workspace)
        actions.addWidget(self.btn_test_client_conn)
        launch.header_layout.addLayout(actions, 2)
        layout.addWidget(launch)
        self.work_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.work_row.setSpacing(10)
        environment = SectionCard('Trạng thái thành phần', icon='shield')
        environment.body.setSpacing(5)
        self.env_openocd = QLabel('OpenOCD · Chưa kiểm tra')
        self.env_gdb = QLabel('ARM GDB · Chưa kiểm tra')
        self.env_vscode = QLabel('VS Code · Chưa kiểm tra')
        self.env_cortex = QLabel('Cortex-Debug · Chưa kiểm tra')
        self.workspace_status = QLabel('Thư mục dự án · Chưa chọn dự án')
        self.symbols_status = QLabel('Ký hiệu ELF/AXF · Chưa chọn dự án')
        for glyph, label in zip(('device', 'settings', 'debug', 'database', 'folder', 'file'), (self.env_openocd,self.env_gdb,self.env_vscode,self.env_cortex,self.workspace_status,self.symbols_status)):
            row = QHBoxLayout()
            tile = QLabel(); tile.setPixmap(engineering_icon(glyph,18).pixmap(18,18)); tile.setObjectName('iconTile'); tile.setFixedSize(20,20); tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName('toolStatus'); label.setWordWrap(True)
            row.addWidget(tile); row.addWidget(label,1)
            environment.body.addLayout(row)
        self.env_detail = QLabel('Kiểm tra môi trường trước khi mở VS Code.')
        self.env_detail.setWordWrap(True); self.env_detail.setObjectName('mutedLabel')
        environment.body.addWidget(self.env_detail)
        self.btn_refresh_environment = QPushButton('↻  Kiểm tra môi trường')
        self.btn_refresh_environment.setText('Kiểm tra môi trường')
        self.btn_refresh_environment.setIcon(engineering_icon('refresh', 18))
        self.btn_refresh_environment.clicked.connect(self.refresh_environment_requested)
        environment.body.addWidget(self.btn_refresh_environment)
        self.work_row.addWidget(environment, 4)
        self.environment_card = environment
        guide = SectionCard('Hướng dẫn gỡ lỗi nhanh', icon='file')
        for number, title, detail in (('1','Chọn dự án & kết nối','Dùng thanh Dự án / Kết nối ở phía trên.'),('2','Mở VS Code','B300 chuẩn bị launch.json và kết nối gỡ lỗi.'),('3','Bắt đầu gỡ lỗi','Nhấn F5 trong VS Code với Cortex-Debug.')):
            row = QHBoxLayout()
            badge = QLabel(number); badge.setObjectName('iconTile'); badge.setFixedSize(30,30); badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            texts = QVBoxLayout(); heading=QLabel(title); heading.setObjectName('sectionTitle')
            note=QLabel(detail); note.setWordWrap(True); note.setObjectName('mutedLabel')
            texts.addWidget(heading); texts.addWidget(note)
            row.addWidget(badge,0,Qt.AlignmentFlag.AlignTop); row.addLayout(texts,1)
            guide.body.addLayout(row)
        self.launch_note = QLabel('Chỉ kết nối. Gỡ lỗi tương tác có thể dừng CPU.')
        self.launch_note.setWordWrap(True); self.launch_note.setObjectName('mutedLabel')
        guide.body.addWidget(self.launch_note)
        guide.body.addStretch(1)
        self.work_row.addWidget(guide,3)
        session = SectionCard('Kết nối & phiên gỡ lỗi', icon='connection')
        self.client_state_label = QLabel('SSH · Chưa chọn')
        self.client_state_label.setWordWrap(True)
        self.bridge_status = QLabel('Cầu gỡ lỗi · Đã dừng')
        self.bridge_status.setObjectName('debugBridgeStatus'); self.bridge_status.setWordWrap(True)
        session.body.addWidget(self.client_state_label)
        session.body.addWidget(self.bridge_status)
        note = QLabel('Địa chỉ kết nối chỉ xuất hiện khi cầu gỡ lỗi báo sẵn sàng. Kiểm tra thiết bị đích riêng tại THIẾT BỊ.')
        note.setObjectName('mutedLabel'); note.setWordWrap(True); session.body.addWidget(note)
        session.body.addStretch(1)
        self.btn_stop_bridge = QPushButton('□  Dừng cầu gỡ lỗi')
        self.btn_stop_bridge.setText('Dừng cầu gỡ lỗi')
        self.btn_stop_bridge.setIcon(engineering_icon('stop', 18))
        self.btn_stop_bridge.clicked.connect(self.stop_bridge_requested)
        session.body.addWidget(self.btn_stop_bridge)
        self.work_row.addWidget(session,3)
        layout.addLayout(self.work_row)
        self.activity_log = ActivityLogPanel("Nhật ký gỡ lỗi")
        self.activity_log.terminal.setMaximumHeight(64)
        layout.addWidget(self.activity_log)
        layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll)

    def bind_context(self, context):
        if self.context is not None:
            self.context.changed.disconnect(self._render_context)
        self.context = context
        if context is not None:
            context.changed.connect(self._render_context)
        self._render_context()

    def _render_context(self):
        context = self.context
        project = context.selected_project if context else None
        connection = context.selected_connection if context else None
        busy = self._busy or bool(context and context.hardware_busy)
        for label, name, path in ((self.workspace_status,'Thư mục dự án',project.workspace if project else None),(self.symbols_status,'Ký hiệu ELF/AXF',project.symbols if project else None)):
            label.setText('%s · %s' % (name,path.name or str(path)) if path else '%s · Chưa chọn dự án' % name)
            label.setToolTip(str(path) if path else 'Chọn dự án trên thanh dùng chung.')
        self.btn_open_vscode.setEnabled(bool(project and connection and not busy and not self._bridge_active))
        self.btn_open_vscode.setToolTip('Chuẩn bị launch.json và mở VS Code với dự án đã chọn.' if project else 'Chọn dự án trên thanh dùng chung.')
        self.btn_open_workspace.setEnabled(bool(project and not busy))
        self.btn_test_client_conn.setEnabled(bool(connection and not connection.is_local and not busy))
        self.btn_test_client_conn.setToolTip('Kiểm tra kết nối SSH đã chọn.' if connection and not connection.is_local else 'Kiểm tra ST-Link cục bộ tại THIẾT BỊ.')
        self.btn_refresh_environment.setEnabled(not busy)
        self.btn_stop_bridge.setEnabled(self._bridge_active)
        if not connection or connection.is_local:
            self.client_state_label.setText('ST-Link cục bộ · kiểm tra thiết bị đích khi mở gỡ lỗi')
        else:
            sessions = context.gateway_sessions
            connected = bool(sessions and sessions.connected(connection.gateway.endpoint))
            self.client_state_label.setText('SSH · Đã kết nối' if connected else 'SSH · Chưa kết nối')
            self.client_state_label.setToolTip(connection.gateway.display_endpoint)

    def _can_launch(self):
        return bool(self.context and self.context.selected_project and not self._busy and not self.context.hardware_busy and not self._bridge_active)

    def _open_vscode(self):
        if not self._can_launch():
            return
        project = self.context.selected_project
        if self.context.selected_connection.is_local:
            self.open_local_vscode_requested.emit(project.workspace, project.symbols)
        else:
            self.open_remote_vscode_requested.emit(self._client_request())

    def _client_request(self):
        if self.context is None or self.context.selected_connection.is_local:
            return {}
        gateway = self.context.selected_connection.gateway
        project = self.context.selected_project
        return {'host':gateway.endpoint.host,'user':gateway.endpoint.user,'ssh_port':gateway.endpoint.port,
                'local_gdb_port':0,'workspace':project.workspace if project else None,
                'elf':project.symbols if project else None,'gateway_id':gateway.profile_id,
                'project_id':project.project_id if project else None}

    def _emit_client_connection_test(self):
        if self.context and not self.context.selected_connection.is_local and not self._busy and not self.context.hardware_busy:
            self.test_client_connection_requested.emit(self._client_request())

    def _open_workspace(self):
        if self.context and self.context.selected_project and not self._busy and not self.context.hardware_busy:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.context.selected_project.workspace)))

    def set_environment_status(self, status):
        self._environment = status
        for label, name, ready in ((self.env_vscode,'VS Code',status.vscode_ready),(self.env_cortex,'Cortex-Debug',status.cortex_debug_ready),(self.env_gdb,'ARM GDB',status.gdb_ready)):
            label.setText('%s · %s' % (name,'Sẵn sàng' if ready else 'Thiếu'))
            label.setProperty('state','success' if ready else 'warning')
        self.env_vscode.setToolTip(status.vscode_path or '')
        self.env_gdb.setToolTip(status.gdb_path or '')
        self.env_detail.setText(status.reason or 'VS Code, Cortex-Debug và ARM GDB đã sẵn sàng.' if status.ready else status.reason or 'Cần kiểm tra lại môi trường.')

    set_environment = set_environment_status

    def set_bridge_state(self, role, state, detail='', gdb_target=None):
        self._bridge_active = str(state).upper() == 'READY'
        state_text = {'READY': 'Sẵn sàng', 'FAILED': 'Lỗi', 'STOPPED': 'Đã dừng', 'STARTING': 'Đang khởi chạy'}.get(str(state).upper(), str(state))
        endpoint = (' · ' + str(gdb_target)) if self._bridge_active and gdb_target else ''
        self.bridge_status.setText('Cầu gỡ lỗi · %s%s' % (state_text, endpoint))
        self.bridge_status.setToolTip(' · '.join(item for item in (detail,gdb_target or '') if item))
        self.bridge_status.setProperty('state','failure' if str(state).upper() == 'FAILED' else 'success' if self._bridge_active else 'neutral')
        self.bridge_status.style().unpolish(self.bridge_status)
        self.bridge_status.style().polish(self.bridge_status)
        if str(role).upper() == 'CLIENT':
            self.env_openocd.setText('OpenOCD từ xa · Chưa xác minh')
        elif self._bridge_active:
            self.env_openocd.setText('OpenOCD · Đang chạy')
        else:
            self.env_openocd.setText('OpenOCD · Đã dừng')
        self._render_context()

    set_bridge_status = set_bridge_state

    def set_client_connection_status(self, connected, detail=''):
        self.client_state_label.setText('SSH · Đã kết nối' if connected else 'SSH · Chưa kết nối')
        self.client_state_label.setToolTip(detail)

    def set_hardware_busy(self, busy):
        self._busy = bool(busy)
        self._render_context()

    set_busy = set_hardware_busy

    def set_probes(self, probes):
        self._probes = tuple(probes)

    def set_target_info(self, info):
        self._target_info = info

    def append_log(self, text):
        self.activity_log.append(text)

    def resizeEvent(self, event):
        self.work_row.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= 900 else QBoxLayout.Direction.TopToBottom)
        super().resizeEvent(event)
