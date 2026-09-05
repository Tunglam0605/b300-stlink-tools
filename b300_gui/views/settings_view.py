"""Configuration owners and workstation readiness; no discovery on render."""
from __future__ import annotations
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from b300_core import __version__ as CORE_VERSION
from b300_version import __version__
from b300_core.vscode_environment import VsCodeEnvironmentStatus
from b300_gui.widgets.engineering import SectionCard, engineering_icon


class SettingsView(QWidget):
    machine_setup_requested = Signal()
    toggle_theme_requested = Signal()
    check_updates_requested = Signal()
    export_support_bundle_requested = Signal()
    about_requested = Signal()
    release_notes_requested = Signal()
    manage_gateways_requested = Signal()
    manage_projects_requested = Signal()
    start_gateway_requested = Signal()
    stop_gateway_requested = Signal()
    density_changed = Signal(str)
    open_logs_requested = Signal()
    documentation_requested = Signal()
    refresh_environment_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsViewContainer")
        self._gateway_state = "STOPPED"
        self._hardware_busy = False
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.cards_grid = QGridLayout()
        self.cards_grid.setSpacing(12)
        self.cards = []
        shared = self._card("Tài nguyên dùng chung", "Dự án và kết nối dùng chung cho mọi trang.")
        self.btn_manage_gateways = self._button("Quản lý kết nối", self.manage_gateways_requested)
        self.btn_manage_projects = self._button("Quản lý dự án", self.manage_projects_requested)
        resources = QHBoxLayout()
        for button, description, vector_icon in ((self.btn_manage_gateways,'SSH · Gateway · Phiên đăng nhập','connection'),(self.btn_manage_projects,'Thư mục làm việc · ELF/AXF · HEX','folder')):
            button.setText(button.text() + '\n' + description)
            button.setIcon(engineering_icon(vector_icon, 20))
            button.setObjectName('resourceTile')
            button.setMinimumHeight(62)
            resources.addWidget(button,1)
        shared.body.addLayout(resources)
        shared.body.addWidget(self._note("Lưu hồ sơ tại đây. Mật khẩu SSH chỉ được giữ trong phiên chạy."))

        runtime = self._card("Công cụ chạy và biên dịch", "Trạng thái từ lần kiểm tra gần nhất.")
        tools_grid = QGridLayout(); tools_grid.setSpacing(6)
        for index, (attribute,name,icon) in enumerate((('lbl_openocd','OpenOCD','connection'),('lbl_gdb','ARM GDB','settings'),('lbl_vscode','VS Code','debug'),('lbl_cortex','Cortex-Debug','database'))):
            tile = QFrame(); tile.setObjectName('toolTile'); row = QHBoxLayout(tile); row.setContentsMargins(6,3,6,3)
            glyph = QLabel(); glyph.setPixmap(engineering_icon(icon,22).pixmap(22,22)); glyph.setObjectName('iconTile'); glyph.setFixedSize(28,28); glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            labels=QVBoxLayout(); labels.setSpacing(2); title=QLabel(name); title.setObjectName('sectionTitle')
            value=QLabel('Chưa kiểm tra'); value.setObjectName('statusBadge'); labels.addWidget(title); labels.addWidget(value)
            row.addWidget(glyph); row.addLayout(labels,1); setattr(self,attribute,value)
            tools_grid.addWidget(tile,index//2,index%2)
        runtime.body.addLayout(tools_grid)
        self.environment_detail = self._note("OpenOCD và ARM GDB do B300 quản lý.")
        runtime.body.addWidget(self.environment_detail)
        self.btn_refresh_environment = self._button("Kiểm tra", self.refresh_environment_requested)
        self.btn_refresh_environment.setIcon(engineering_icon('refresh', 18))
        runtime.header_layout.addWidget(self.btn_refresh_environment)

        host = self._card("Máy này làm Gateway", "Chia sẻ ST-Link trên máy này qua SSH.")
        self.gateway_status = QLabel("ĐÃ DỪNG")
        self.gateway_status.setObjectName("statusBadge")
        host.body.addWidget(self.gateway_status)
        host.body.addWidget(self._note("Máy chủ OpenSSH · ST-Link USB · OpenOCD\nGỡ lỗi chỉ lắng nghe cục bộ qua đường hầm SSH."))
        actions = QHBoxLayout()
        self.btn_start_gateway = self._button("Khởi chạy Gateway", self.start_gateway_requested)
        self.btn_start_gateway.setObjectName("primaryActionButton")
        self.btn_stop_gateway = self._button("Dừng Gateway", self.stop_gateway_requested)
        self.btn_stop_gateway.setEnabled(False)
        actions.addWidget(self.btn_start_gateway)
        actions.addWidget(self.btn_stop_gateway)
        host.body.addLayout(actions)

        machine = self._card("Thiết lập máy", "Trình điều khiển, USB và môi trường theo hệ điều hành.")
        self.btn_run_setup = self._button("Thiết lập trình điều khiển / USB / SSH", self.machine_setup_requested)
        self.btn_run_setup.setIcon(engineering_icon('wrench', 18))
        self.btn_run_setup.setObjectName("primaryActionButton")
        machine.body.addWidget(self.btn_run_setup)
        machine.body.addWidget(self._note("Windows  ·  Trình điều khiển ST-Link\nLinux  ·  udev và quyền USB\nSSH  ·  Kết nối từ xa\nB300 quản lý OpenOCD và ARM GDB."))

        updates = self._card("Cập nhật & phiên bản")
        self.lbl_gui_version = self._value(updates, "Giao diện", "v%s" % __version__)
        self.lbl_core_version = self._value(updates, "Lõi xử lý", "v%s" % CORE_VERSION)
        self.btn_check_updates = self._button("Kiểm tra cập nhật", self.check_updates_requested)
        self.btn_release_notes = self._button("Nhật ký phiên bản", self.release_notes_requested)
        update_actions=QHBoxLayout()
        self.btn_check_updates.setObjectName('primaryActionButton')
        update_actions.addWidget(self.btn_check_updates); update_actions.addWidget(self.btn_release_notes)
        updates.body.addLayout(update_actions)

        support = self._card("Giao diện & hỗ trợ")
        density_row = QHBoxLayout()
        density_row.addWidget(QLabel("Mật độ hiển thị"))
        self.density_selector = QComboBox()
        self.density_selector.addItem("Thu gọn", "compact")
        self.density_selector.addItem("Thoải mái", "comfortable")
        self.density_selector.currentIndexChanged.connect(lambda _index: self.density_changed.emit(self.density_selector.currentData()))
        density_row.addWidget(self.density_selector, 1)
        support.body.addLayout(density_row)
        self.btn_toggle_theme = self._button("Sáng / Tối  ·  Ctrl+T", self.toggle_theme_requested)
        self.btn_export_support = self._button("Xuất gói hỗ trợ", self.export_support_bundle_requested)
        self.btn_about = self._button("Giới thiệu B300", self.about_requested)
        self.btn_open_logs = self._button("Mở nhật ký", self.open_logs_requested)
        self.btn_open_logs.setIcon(engineering_icon('file', 18))
        self.btn_documentation = self._button("Tài liệu", self.documentation_requested)
        self.btn_documentation.setIcon(engineering_icon('file', 18))
        support_actions = QGridLayout()
        for index, button in enumerate((self.btn_toggle_theme,self.btn_export_support,self.btn_open_logs,self.btn_documentation,self.btn_about)):
            support_actions.addWidget(button,index//2,index%2)
        support.body.addLayout(support_actions)
        for card in self.cards:
            card.body.addStretch(1)
        layout.addLayout(self.cards_grid)
        layout.addStretch(1)
        scroll.setWidget(content)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)
        self._arrange_cards(2)

    def _card(self, title: str, subtitle: str = "") -> SectionCard:
        icons=('folder','settings','connection','device','history','shield')
        card = SectionCard(str(len(self.cards)+1)+'. '+title, subtitle, self, icon=icons[len(self.cards)])
        card.body.setSpacing(5)
        card.body.setContentsMargins(12,8,12,8)
        self.cards.append(card)
        return card

    @staticmethod
    def _button(text: str, signal) -> QPushButton:
        button = QPushButton(text)
        button.setMinimumHeight(28)
        button.clicked.connect(signal.emit)
        return button

    @staticmethod
    def _note(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("mutedLabel")
        return label

    @staticmethod
    def _value(card: SectionCard, name: str, value: str = "Chưa kiểm tra") -> QLabel:
        row = QHBoxLayout()
        row.addWidget(QLabel(name))
        label = QLabel(value)
        label.setWordWrap(True)
        label.setObjectName("monoText")
        row.addWidget(label, 1, Qt.AlignmentFlag.AlignRight)
        card.body.addLayout(row)
        return label

    def _arrange_cards(self, columns: int) -> None:
        for card in self.cards:
            self.cards_grid.removeWidget(card)
        for index, card in enumerate(self.cards):
            self.cards_grid.addWidget(card, index // columns, index % columns)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._arrange_cards(2 if self.width() >= 850 else 1)

    def set_environment_status(self, status: Optional[VsCodeEnvironmentStatus]) -> None:
        """Render externally supplied evidence without launching discovery."""
        for label, ready, path in (
            (self.lbl_vscode, status.vscode_ready if status else None, status.vscode_path if status else None),
            (self.lbl_gdb, status.gdb_ready if status else None, status.gdb_path if status else None),
            (self.lbl_cortex, status.cortex_debug_ready if status else None, None),
        ):
            label.setText("Chưa kiểm tra" if ready is None else "Sẵn sàng" if ready else "Thiếu")
            label.setToolTip(path or "")
            label.setProperty("state", "neutral" if ready is None else "success" if ready else "warning")
        self.environment_detail.setText(status.reason if status and status.reason else "OpenOCD và ARM GDB do B300 quản lý.")

    def set_density(self, density: str) -> None:
        """Restore the shared preference without emitting a second user action."""
        index = self.density_selector.findData(density)
        if index < 0:
            return
        was_blocked = self.density_selector.blockSignals(True)
        self.density_selector.setCurrentIndex(index)
        self.density_selector.blockSignals(was_blocked)

    def set_openocd_status(self, ready: Optional[bool], path: str = "") -> None:
        self.lbl_openocd.setText("Chưa kiểm tra" if ready is None else "Sẵn sàng" if ready else "Thiếu")
        self.lbl_openocd.setToolTip(path)

    def set_gateway_status(self, state, message: str = "") -> None:
        self._gateway_state = str(getattr(state, "value", state)).upper()
        self.gateway_status.setText({"STOPPED": "ĐÃ DỪNG", "READY": "SẴN SÀNG", "FAILED": "LỖI", "STARTING": "ĐANG KHỞI CHẠY"}.get(self._gateway_state, "CHƯA KIỂM TRA"))
        self.gateway_status.setToolTip(message)
        self.gateway_status.setProperty('state', 'failure' if self._gateway_state == 'FAILED' else 'success' if self._gateway_state == 'READY' else 'neutral')
        self.gateway_status.style().unpolish(self.gateway_status)
        self.gateway_status.style().polish(self.gateway_status)
        self._render_host_controls()

    def set_hardware_busy(self, busy: bool) -> None:
        self._hardware_busy = bool(busy)
        self._render_host_controls()

    def _render_host_controls(self) -> None:
        self.btn_start_gateway.setEnabled(not self._hardware_busy and self._gateway_state in {"STOPPED", "FAILED"})
        self.btn_stop_gateway.setEnabled(self._gateway_state == "READY")


__all__ = ["SettingsView"]
