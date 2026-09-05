"""One context strip shared by the engineering work pages."""
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from .engineering import engineering_icon


class SharedContextBar(QFrame):
    manage_projects_requested = Signal()
    manage_connections_requested = Signal()
    refresh_probes_requested = Signal()

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.setObjectName('sharedContextBar')
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(10)
        self.project_combo = QComboBox()
        self.connection_combo = QComboBox()
        self.probe_combo = QComboBox()
        for name, combo in (('Dự án',self.project_combo),('Kết nối',self.connection_combo),('ST-Link',self.probe_combo)):
            column = QVBoxLayout()
            column.setSpacing(3)
            label = QLabel(name)
            label.setObjectName('contextFieldLabel')
            column.addWidget(label)
            combo.setMinimumWidth(125)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(9)
            column.addWidget(combo)
            root.addLayout(column, 1)
        status = QVBoxLayout()
        self.target_label = QLabel()
        self.target_label.setObjectName('contextTarget')
        self.connection_status = QLabel()
        self.connection_status.setObjectName('contextConnectionStatus')
        status.addWidget(self.target_label)
        status.addWidget(self.connection_status)
        root.addLayout(status)
        self.refresh_probes_button = QPushButton('')
        self.refresh_probes_button.setIcon(engineering_icon('refresh', 18))
        self.refresh_probes_button.setFixedSize(38, 38)
        self.refresh_probes_button.setToolTip('Quét lại ST-Link')
        self.manage_projects_button = QPushButton('Dự án…')
        self.manage_connections_button = QPushButton('Kết nối…')
        for button in (self.refresh_probes_button,self.manage_projects_button,self.manage_connections_button):
            button.setObjectName('contextAction')
            root.addWidget(button)
        self.refresh_probes_button.clicked.connect(self.refresh_probes_requested)
        self.manage_projects_button.clicked.connect(self.manage_projects_requested)
        self.manage_connections_button.clicked.connect(self.manage_connections_requested)
        self.project_combo.currentIndexChanged.connect(lambda _: context.select_project(self.project_combo.currentData()))
        self.connection_combo.currentIndexChanged.connect(lambda _: context.select_connection(self.connection_combo.currentData()))
        self.probe_combo.currentIndexChanged.connect(lambda _: context.select_probe(self.probe_combo.currentData()))
        context.changed.connect(self.render)
        self.render()

    @staticmethod
    def _populate(combo, entries, selected, placeholder):
        blocker = QSignalBlocker(combo)
        combo.clear()
        if entries and not any(value == selected for _, value, _ in entries):
            combo.addItem("Chọn ST-Link…", None)
        for label, value, tooltip in entries:
            combo.addItem(label, value)
            combo.setItemData(combo.count()-1, tooltip, Qt.ItemDataRole.ToolTipRole)
        if not entries:
            combo.addItem(placeholder, None)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.setToolTip(combo.currentData(Qt.ItemDataRole.ToolTipRole) or placeholder)
        del blocker

    def render(self):
        context = self.context
        self._populate(self.project_combo, [(p.name,p.project_id,'\n'.join((str(p.workspace),str(p.symbols),str(p.application_hex or 'Chưa có tệp HEX ứng dụng')))) for p in context.project_profiles], context.selected_project.project_id if context.selected_project else None, 'Chưa chọn dự án')
        self._populate(self.connection_combo, [(c.name,c.connection_id,c.gateway.display_endpoint if c.gateway else 'ST-Link cục bộ') for c in context.connections], context.selected_connection.connection_id, 'ST-Link cục bộ')
        self._populate(self.probe_combo, [(p.name,p.serial,p.serial or 'Chưa có số sê-ri') for p in context.probes], context.selected_probe, 'Chưa phát hiện ST-Link')
        target = context.target_info
        self.target_label.setText('MCU đích  0x%03X · %d KiB' % (target.device_id,target.flash_kib) if target else 'MCU đích  Chưa kiểm tra')
        self.target_label.setToolTip(self.target_label.text())
        connection = context.selected_connection
        connected = bool(connection.gateway and context.gateway_sessions and context.gateway_sessions.connected(connection.gateway.endpoint))
        text = ('Đã phát hiện ST-Link' if context.probes else 'Chưa phát hiện ST-Link') if connection.is_local else ('SSH đã kết nối' if connected else 'SSH chưa kết nối')
        self.connection_status.setText(text)
        self.connection_status.setToolTip('Trạng thái kết nối SSH; việc kiểm tra MCU đích được thực hiện riêng.' if not connection.is_local else 'Trạng thái phát hiện ST-Link qua USB; việc kiểm tra MCU đích được thực hiện riêng.')
        self.connection_status.setProperty('state', 'success' if connected else 'neutral')
        for widget in (self.project_combo,self.connection_combo,self.probe_combo,self.manage_projects_button,self.manage_connections_button,self.refresh_probes_button):
            widget.setEnabled(not context.hardware_busy)
        self.refresh_probes_button.setEnabled(not context.hardware_busy and connection.is_local)
        self.refresh_probes_button.setToolTip('Quét ST-Link cục bộ' if connection.is_local else 'Chưa hỗ trợ quét ST-Link từ xa; hãy kiểm tra SSH tại GỠ LỖI VS CODE.')
