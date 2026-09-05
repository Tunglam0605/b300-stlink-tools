"""Production MainWindow for B300 ST-Link Tools v0.19 Shared-Workspace UX.

B300 v0.19 intentionally is not an IDE.  The production surface is:
PROGRAM / MONITOR / DEBUG / DEVICE / SETTINGS, with interactive debugging
outsourced to VS Code + Cortex-Debug while B300 retains ST-Link/OpenOCD/SSH and
run-state safety ownership.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDateTime, QTimer, QUrl, QSize, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout

from b300_core.gateway_profiles import GatewayProfile, GatewayProfileStore
from b300_core.gateway_sessions import GatewaySessionManager
from b300_core.models import ProbeInfo, ProbeRef, TargetInfo
from b300_core.policy import validate_target_for_provisioning, validate_bootloader_write_protection
from b300_core.project_profiles import ProjectProfileStore
from b300_core.vscode_bridge import BridgeState
from b300_core.remote_profile import RemoteGatewayProfile
from b300_core.remote_session import RemoteSession
from .main_window import MainWindow
from .confirm_dialog import ConfirmFlashDialog
from .operation_state import OperationState
from .gateway_login_dialog import GatewayLoginDialog
from .gateway_manager_dialog import GatewayManagerDialog
from .project_manager_dialog import ProjectManagerDialog
from .vscode_debug_controller import VsCodeDebugController
from .views.debug_vscode_view import DebugVsCodeView
from .views.device_view import DeviceView
from .views.monitor_view import MonitorView
from .views.program_view import ProgramView
from .views.settings_view import SettingsView
from .theme import ThemeManager
from .reference_style import apply_reference_palette
from .engineering_context_controller import EngineeringContextController
from .widgets.engineering import engineering_stylesheet, ActivityLogPanel, SectionCard, engineering_icon
from .branding import asset_path


class MainWindowV18(MainWindow):
    """v0.19 shared-resource window and explicit VS Code debug orchestration."""

    def __init__(self, *args, **kwargs) -> None:
        gateway_store = kwargs.pop("gateway_store", None)
        project_store = kwargs.pop("project_store", None)
        gateway_sessions = kwargs.pop("gateway_sessions", None)
        kwargs.setdefault("legacy_workbenches", False)
        super().__init__(*args, **kwargs)
        # Reuse the base DebugService so HardwareSession arbitration remains
        # authoritative across flash, monitor and interactive debug.
        self._vscode_controller = VsCodeDebugController(debug_service=self.debug_service)
        self._gateway_store = gateway_store or GatewayProfileStore()
        self._project_store = project_store or ProjectProfileStore()
        self._gateway_sessions = gateway_sessions or GatewaySessionManager()
        self._vscode_remote_session: Optional[RemoteSession] = None
        self._remote_login_dialog: Optional[GatewayLoginDialog] = None
        self._gateway_manager_dialog: Optional[GatewayManagerDialog] = None
        self._project_manager_dialog: Optional[ProjectManagerDialog] = None
        self._pending_remote_request: Optional[dict] = None
        self._context_controller = EngineeringContextController(self)
        self.app_context = self._context_controller.context
        self.shared_context_bar = self._context_controller.bar
        self._configure_v18_navigation()
        self._configure_v18_views()
        self._configure_engineering_shell()
        self._context_controller.bind()
        self._refresh_shared_profiles()
        self.show_page("program")
        ThemeManager.instance().theme_changed.connect(self._refresh_reference_palette)
        self._refresh_reference_palette()

    def _refresh_reference_palette(self, *args) -> None:
        apply_reference_palette(self, ThemeManager.instance().palette)
        self.setStyleSheet(engineering_stylesheet(ThemeManager.instance().palette))
        self._apply_density()

    def _configure_engineering_shell(self):
        header = self.header_bar
        header.setFixedHeight(84)
        self.header_logo = QLabel()
        self.header_logo.setPixmap(QPixmap(str(asset_path('b300-industrial-mark.svg'))).scaled(42, 42, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.header_logo.setAccessibleName('B300 ST-Link Tools')
        header.layout().insertWidget(0, self.header_logo)
        header.brand_title.setStyleSheet('')
        header.brand_subtitle.setStyleSheet('font-size: 12px;')
        header.brand_container.setMinimumWidth(215)
        self.brand_logo.parentWidget().hide()
        for label in self.sidebar.findChildren(QLabel):
            if label.objectName() == 'navSectionTitle':
                label.hide()
        self.sidebar.layout().setContentsMargins(0, 16, 0, 12)
        navigation_labels = {
            'program': 'NẠP PHẦN MỀM',
            'monitor': 'GIÁM SÁT',
            'debug': 'GỠ LỖI VS CODE',
            'device': 'THIẾT BỊ',
            'settings': 'CÀI ĐẶT',
        }
        for page, button in zip(('program','monitor','debug','device','settings'),self.v18_nav_buttons):
            button.setText('  ' + navigation_labels[page])
            button.setIcon(engineering_icon(page, 22, ThemeManager.instance().palette.text_secondary))
            button.setIconSize(QSize(22,22))
            button.setMinimumHeight(58)
        self.shared_context_bar.parentWidget().layout().removeWidget(self.shared_context_bar)
        header.layout().insertWidget(2, self.shared_context_bar, 1)
        self.shared_context_bar.manage_projects_button.hide()
        self.shared_context_bar.manage_connections_button.hide()
        header.btn_open_project.setText('Dự án')
        header.btn_open_project.setIcon(engineering_icon('folder',20))
        header.btn_history.setText('Phiên bản')
        header.btn_history.setIcon(engineering_icon('history',20))
        for button in (header.btn_open_project,header.btn_history):
            button.setFixedHeight(38)
        self.page_title.setObjectName('engineeringPageTitle')
        self.page_icon = QLabel()
        self.page_icon.setFixedSize(38,38)
        self.page_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_header = self.page_title.parentWidget()
        page_header.layout().insertWidget(0,self.page_icon)
        page_header.setMinimumHeight(60)
        self.status_banner.setMaximumWidth(260)

    def _apply_density(self, value=None):
        if value is not None:
            self.settings.setValue('engineering_density', value)
        compact = self.settings.value('engineering_density', 'compact') == 'compact'
        for card in self.findChildren(SectionCard):
            card.body.setSpacing(7 if compact else 12)
            margin = 12 if compact else 18
            card.body.setContentsMargins(margin, margin, margin, margin)

    def _open_activity_log(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('B300 · Nhật ký hoạt động')
        layout = QVBoxLayout(dialog)
        panel = ActivityLogPanel(parent=dialog)
        panel.terminal.setMaximumHeight(16777215)
        panel.append(self.log_view.toPlainText())
        layout.addWidget(panel)
        dialog.resize(900, 520)
        dialog.exec()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "device_info_panel"):
            # Keep the task area usable on laptop screens. The DEVICE page
            # remains available for the same information at compact widths.
            self.device_info_panel.setVisible(self.width() >= 1480 and getattr(self, "v18_stack", None) is not None and self.v18_stack.currentIndex() == 0)

    # ------------------------------------------------------------------
    # Main navigation
    # ------------------------------------------------------------------
    def _configure_v18_navigation(self) -> None:
        # Keep global utilities in one predictable place. Page-local duplicates
        # remain as compatibility attributes but are not part of production UX.
        self.header_bar.segmented_control.hide()
        self.header_bar.conn_mode_control.hide()
        self.header_bar.probe_container.hide()
        self.header_bar.target_mcu_badge.hide()
        self.header_bar.probe_refresh_btn.setObjectName("refreshProbeAction")
        self.header_bar.theme_btn.hide()
        self.header_bar.machine_setup_btn.hide()
        self.header_bar.help_btn.hide()
        self.machine_setup_button.hide()
        self.update_channel_label.hide()
        for btn_name in ("nav_flash_btn", "nav_memory_btn", "nav_debug_btn", "nav_gateway_btn"):
            button = getattr(self, btn_name, None)
            if button is not None:
                button.setVisible(False)

        sidebar_layout = self.sidebar.layout()
        if sidebar_layout is None:
            return
        self.v18_nav_buttons = []
        specs = [
            ("program", "NẠP PHẦN MỀM", "Nạp ứng dụng và bộ nạp khởi động an toàn"),
            ("monitor", "GIÁM SÁT", "Quan sát biến và luồng thực thi theo thời gian thực"),
            ("debug", "GỠ LỖI VS CODE", "Chuẩn bị gỡ lỗi trong VS Code"),
            ("device", "THIẾT BỊ", "Thông tin MCU đích, ST-Link và byte tùy chọn"),
            ("settings", "CÀI ĐẶT", "Thiết lập môi trường và cấu hình hệ thống"),
        ]
        attrs = [
            "nav_program_btn", "nav_monitor_btn", "nav_debug_btn_v18",
            "nav_device_btn", "nav_settings_btn",
        ]
        insert_idx = 3
        for index, (page, text, tooltip) in enumerate(specs):
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda _checked=False, selected=page: self.show_page(selected))
            sidebar_layout.insertWidget(insert_idx + index, button)
            self.v18_nav_buttons.append(button)
            setattr(self, attrs[index], button)
        self.nav_buttons = self.v18_nav_buttons

    def _configure_v18_views(self) -> None:
        self.tabs.setVisible(False)
        self.v18_stack = QStackedWidget()
        self.v18_stack.setObjectName("v18MainStack")

        self.program_view = ProgramView(self)
        self.program_view.flash_application_requested.connect(self._on_v18_flash_application)
        self.program_view.flash_bootloader_requested.connect(self._on_v18_flash_bootloader)
        self.program_view.file_selected.connect(self._on_v18_file_selected)
        self.program_view.file_invalidated.connect(self._on_v18_image_invalidated)
        self.program_view.probe_refresh_requested.connect(self.refresh_probes)
        self.program_view.target_inspect_requested.connect(self.inspect_target)
        self.program_view.btn_refresh_probe.hide()
        self.program_view.btn_inspect_target.hide()
        if hasattr(self.program_view, "device_card"):
            self.program_view.device_card.hide()
        self.v18_stack.addWidget(self.program_view)

        self.monitor_view = MonitorView(
            self,
            selected_probe=self._selected_probe,
            remote_session_provider=self._monitor_remote_session,
            hardware_busy=lambda: self._operation_state().is_hardware_busy,
            openocd_executable=str(self.debug_service.executable),
            context=self.app_context,
        )
        self.monitor_view.log.connect(self.append_log)
        self.monitor_view.operation_state_changed.connect(self._hardware_activity_changed)
        self.monitor_view.manage_gateways_requested.connect(self._open_gateway_manager)
        self.monitor_view.manage_projects_requested.connect(self._open_project_manager)
        self.v18_stack.addWidget(self.monitor_view)

        self.debug_vscode_view = DebugVsCodeView(self, context=self.app_context)
        self.debug_vscode_view.open_local_vscode_requested.connect(self._on_v18_open_local_vscode)
        self.debug_vscode_view.open_remote_vscode_requested.connect(self._on_v18_open_remote_vscode)
        self.debug_vscode_view.test_client_connection_requested.connect(self._on_v18_test_client_connection)
        self.debug_vscode_view.stop_bridge_requested.connect(self._on_v18_stop_bridge)
        self.debug_vscode_view.refresh_environment_requested.connect(self._refresh_vscode_environment)
        self.debug_vscode_view.manage_gateways_requested.connect(self._open_gateway_manager)
        self.debug_vscode_view.manage_projects_requested.connect(self._open_project_manager)
        self.v18_stack.addWidget(self.debug_vscode_view)

        self.device_view = DeviceView(self)
        self.device_view.refresh_requested.connect(self.refresh_probes)
        self.device_view.doctor_requested.connect(self.inspect_target)
        self.device_view.btn_refresh.hide()
        self.v18_stack.addWidget(self.device_view)

        self.settings_view = SettingsView(self)
        self.settings_view.machine_setup_requested.connect(self.show_machine_setup)
        self.settings_view.toggle_theme_requested.connect(self._on_toggle_theme)
        self.settings_view.check_updates_requested.connect(lambda: self.check_for_updates(manual=True))
        self.settings_view.export_support_bundle_requested.connect(self.export_support_bundle)
        self.settings_view.about_requested.connect(self.show_about)
        self.settings_view.release_notes_requested.connect(self.show_release_notes)
        self.settings_view.manage_gateways_requested.connect(self._open_gateway_manager)
        self.settings_view.manage_projects_requested.connect(self._open_project_manager)
        self.settings_view.start_gateway_requested.connect(self._on_v18_start_gateway)
        self.settings_view.stop_gateway_requested.connect(self._on_v18_stop_gateway)
        self.settings_view.refresh_environment_requested.connect(self._refresh_vscode_environment)
        self.settings_view.density_changed.connect(self._apply_density)
        self.settings_view.set_density(str(self.settings.value('engineering_density', 'compact')))
        self.settings_view.open_logs_requested.connect(self._open_activity_log)
        documentation = Path(__file__).resolve().parents[1] / 'docs' / '00_START_HERE.md'
        self.settings_view.btn_documentation.setEnabled(documentation.is_file())
        self.settings_view.btn_documentation.setToolTip(str(documentation) if documentation.is_file() else 'Tài liệu chưa có trong gói này.')
        self.settings_view.documentation_requested.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(documentation))))
        self.settings_view.btn_run_setup.setObjectName("machineSetupAction")
        self.settings_view.btn_check_updates.setObjectName("checkUpdateAction")
        self.v18_stack.addWidget(self.settings_view)

        content_area = self.tabs.parentWidget()
        if content_area is not None and content_area.layout() is not None:
            content_area.layout().addWidget(self.shared_context_bar)
            content_area.layout().addWidget(self.v18_stack)

        if hasattr(self, "stats_row"):
            self.stats_row.hide()

        # Pinned right sidebar: "Thông tin thiết bị" matching the mockup
        from .widgets.device_info_panel import DeviceInfoPanel
        self.device_info_panel = DeviceInfoPanel(self, summary_only=True)
        self.device_info_panel.setVisible(self.width() >= 1480 and getattr(self, "v18_stack", None) is not None and self.v18_stack.currentIndex() == 0)
        if content_area is not None and content_area.parentWidget() is not None:
            parent_layout = content_area.parentWidget().layout()
            if parent_layout is not None:
                parent_layout.addWidget(self.device_info_panel)

        # Quick action buttons on HeaderBar
        if hasattr(self.header_bar, "btn_open_project"):
            self.header_bar.btn_open_project.clicked.connect(self._open_project_manager)
        if hasattr(self.header_bar, "btn_history"):
            self.header_bar.btn_history.setText("Phát hành")
            self.header_bar.btn_history.setToolTip("Xem ghi chú phát hành")
            self.header_bar.btn_history.clicked.connect(self.show_release_notes)

        # Realtime status bar clock matching mockup
        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 600; padding-right: 12px;")
        self.statusBar().addPermanentWidget(self.clock_label)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._update_clock()

        self.program_view.set_probes(self._probes)
        self.debug_vscode_view.set_probes(self._probes)
        self.device_view.set_probes(self._probes)
        self.device_info_panel.set_probes(self._probes)
        self.header_bar.set_probes(self._probes, self.probe_combo.currentData())
        if self.target_info is not None:
            self.program_view.set_target_info(self.target_info)
            self.debug_vscode_view.set_target_info(self.target_info)
            self.device_view.set_target_info(self.target_info)
            self.device_info_panel.set_target_info(self.target_info)
            self.header_bar.set_target_info(self.target_info)
        self._render_bridge_state()
        self._render_program_readiness()

    def show_page(self, page_name: str) -> None:
        page_map = {
            "program": (0, self.nav_program_btn, "NẠP PHẦN MỀM"),
            "monitor": (1, self.nav_monitor_btn, "THEO DÕI BIẾN"),
            "debug": (2, getattr(self, "nav_debug_btn_v18", None), "GỠ LỖI VS CODE"),
            "device": (3, self.nav_device_btn, "THÔNG SỐ PHẦN CỨNG"),
            "settings": (4, self.nav_settings_btn, "CÀI ĐẶT HỆ THỐNG"),
        }
        entry = page_map.get(str(page_name).lower())
        if entry is None:
            return
        index, active, title = entry
        for button in self.v18_nav_buttons:
            button.setChecked(button is active)
        palette = ThemeManager.instance().palette
        for name, button in zip(("program", "monitor", "debug", "device", "settings"), self.v18_nav_buttons):
            button.setIcon(engineering_icon(name, 22, palette.accent_cyan if button is active else palette.text_secondary))
        self.v18_stack.setCurrentIndex(index)
        self.shared_context_bar.setVisible(index != 4)
        if hasattr(self, 'device_info_panel'):
            self.device_info_panel.setVisible(index == 0 and self.width() >= 1480)
        self.page_title.setText(title)
        if hasattr(self, 'page_icon'):
            self.page_icon.setPixmap(engineering_icon(str(page_name),32).pixmap(32,32))
        self.page_subtitle.setText("")
        self.page_subtitle.hide()
        if hasattr(self, "sep_header"):
            self.sep_header.hide()

    # ------------------------------------------------------------------
    # Shared hardware state
    # ------------------------------------------------------------------
    def _operation_state(self) -> OperationState:
        base = super()._operation_state()
        monitor = getattr(self, "monitor_view", None)
        monitor_busy = bool(
            monitor is not None and monitor.controller.active
        )
        controller = getattr(self, "_vscode_controller", None)
        bridge_busy = bool(controller is not None and controller.state.state == BridgeState.READY)
        manager = getattr(self.service, "session_manager", None)
        lease_busy = bool(manager is not None and manager.snapshot().busy)
        return OperationState(
            main_hardware_busy=base.main_hardware_busy or lease_busy,
            memory_hardware_busy=base.memory_hardware_busy,
            debug_hardware_busy=base.debug_hardware_busy or monitor_busy or bridge_busy,
        )

    def _update_controls(self) -> None:
        super()._update_controls()
        hardware_busy = self._operation_state().is_hardware_busy
        local_connection = not hasattr(self, 'app_context') or self.app_context.selected_connection.is_local
        if hasattr(self, 'app_context'):
            self.app_context.set_hardware_busy(hardware_busy)
        self.header_bar.machine_setup_btn.setEnabled(not hardware_busy)
        self.header_bar.probe_refresh_btn.setEnabled(not hardware_busy)
        self.header_bar.conn_mode_control.setEnabled(not hardware_busy)
        if hasattr(self, "monitor_view"):
            self.monitor_view.set_hardware_busy(hardware_busy)
            if not self.monitor_view.controller.active and self.app_context.selected_project is None:
                self.monitor_view.live_panel.start_button.setEnabled(False)
        if hasattr(self, "program_view"):
            self.program_view.set_busy(hardware_busy)
            self.program_view.set_probes(self.app_context.probes, self.app_context.selected_probe)
            self.program_view.btn_flash_app.setEnabled(
                local_connection and not hardware_busy and self.openocd_ready and bool(self._probes)
                and not (self._probe_selection_required and self.probe_combo.currentData() is None)
                and self.program_view._selected_file is not None
            )
            if not local_connection:
                self.program_view.btn_dry_run_action.setEnabled(False)
                self.program_view.btn_flash_bootloader.setEnabled(False)
        if hasattr(self, "device_view"):
            self.device_view.set_busy(hardware_busy or not local_connection or not self.openocd_ready or not self._probes)
        if hasattr(self, "debug_vscode_view"):
            self.debug_vscode_view.set_hardware_busy(hardware_busy)
        if hasattr(self, "settings_view"):
            self.settings_view.set_hardware_busy(hardware_busy)
            self.settings_view.set_openocd_status(self.openocd_ready, str(self.debug_service.executable))
            self.settings_view.btn_run_setup.setEnabled(not hardware_busy)
            self.settings_view.btn_manage_gateways.setEnabled(not hardware_busy)
            self.settings_view.btn_manage_projects.setEnabled(not hardware_busy)
            self.header_bar.btn_open_project.setEnabled(not hardware_busy)
        if hasattr(self, "device_info_panel"):
            self.device_info_panel.set_probes(self.app_context.probes, self.app_context.selected_probe)
            self._refresh_reference_palette()

    def _update_clock(self) -> None:
        if hasattr(self, "clock_label"):
            self.clock_label.setText(QDateTime.currentDateTime().toString("HH:mm:ss  |  dd/MM/yyyy"))

    def refresh_probes(self) -> None:
        if hasattr(self, 'app_context') and not self.app_context.selected_connection.is_local:
            self._context_controller.refresh_probes()
            return
        super().refresh_probes()
        if hasattr(self, 'app_context'):
            self.app_context.set_probes(self._probes, self.probe_combo.currentData())
        if hasattr(self, "program_view"):
            self.program_view.set_probes(self._probes, self.probe_combo.currentData())
        if hasattr(self, "debug_vscode_view"):
            self.debug_vscode_view.set_probes(self._probes)
        if hasattr(self, "device_view"):
            self.device_view.set_probes(self._probes)
        if hasattr(self, "device_info_panel"):
            self.device_info_panel.set_probes(self._probes, self.probe_combo.currentData())
        if hasattr(self, "header_bar"):
            self.header_bar.set_probes(self._probes, self.probe_combo.currentData())

    def apply_target_info(self, info: TargetInfo) -> None:
        super().apply_target_info(info)
        if hasattr(self, 'app_context'):
            self.app_context.set_target_info(info)
        if hasattr(self, "program_view"):
            self.program_view.set_target_info(info)
        if hasattr(self, "debug_vscode_view"):
            self.debug_vscode_view.set_target_info(info)
        if hasattr(self, "device_view"):
            self.device_view.set_target_info(info)
        if hasattr(self, "device_info_panel"):
            self.device_info_panel.set_target_info(info)
        if hasattr(self, "header_bar"):
            self.header_bar.set_target_info(info)

    def _clear_target_display(self) -> None:
        self._target_revision = getattr(self, "_target_revision", 0) + 1
        if hasattr(self, 'app_context'):
            self.app_context.set_target_info(None)
        super()._clear_target_display()
        if hasattr(self, "stats_row"):
            self.stats_row.target_card.set_value("Chưa đọc MCU đích", "Tự kiểm tra khi nạp · hoặc trang THIẾT BỊ")
            self.stats_row.flash_card.set_value("Chưa đọc bộ nhớ flash", "Tự kiểm tra khi nạp · hoặc trang THIẾT BỊ")
        if hasattr(self, "device_info_panel"):
            self.device_info_panel.set_target_info(None)
        if hasattr(self, "header_bar"):
            self.header_bar.set_target_info(None)
        for name in ("program_view", "device_view", "debug_vscode_view"):
            view = getattr(self, name, None)
            if view is not None:
                view.set_target_info(None)

    def _invalidate_target(self) -> None:
        self.target_info = None
        self.target_ready = False
        self.flash_plan = None
        self._clear_target_display()

    def _selected_probe(self) -> ProbeRef:
        probe = super()._selected_probe()
        if probe.serial is None and len(self._probes) == 1:
            return ProbeRef(self._probes[0].serial)
        return probe

    def _rebuild_plan(self) -> None:
        self.flash_plan = None
        error = None
        if self.image_info is not None and self.target_info is not None:
            try:
                self.flash_plan = self.service.plan(self.image_info, self._selected_probe(), self.target_info)
            except Exception as failure:
                error = failure
                self.append_log(str(failure))
        self._update_controls()
        self._render_program_readiness()
        if error is not None and hasattr(self, "program_view"):
            self.program_view.banner.show_fail("Kiểm tra an toàn không đạt", str(error), self._preflight_next_action())

    def _render_program_readiness(self) -> None:
        if not hasattr(self, "program_view"):
            return
        banner = self.program_view.banner
        if self.target_info is None:
            detail = "Bấm NẠP ỨNG DỤNG để tự kiểm tra trước khi xác nhận."
            if not self.openocd_ready:
                detail = "Mở CÀI ĐẶT để thiết lập môi trường OpenOCD."
            elif not self._probes:
                detail = "Cắm ST-Link và bấm Quét lại ở thanh trên."
            elif self._probe_selection_required and self.probe_combo.currentData() is None:
                detail = "Chọn ST-Link theo số sê-ri ở thanh trên."
            banner.show_info("Chưa kiểm tra MCU đích", detail)
            return
        try:
            validate_target_for_provisioning(self.target_info)
            validate_bootloader_write_protection(self.target_info)
        except ValueError as error:
            banner.show_fail("Kiểm tra MCU đích không đạt", str(error), self._preflight_next_action())
            return
        banner.show_info(
            "MCU đích đã được kiểm tra" if self.flash_plan is not None else "Chọn tệp HEX ứng dụng hợp lệ",
            "Mỗi lần nạp sẽ kiểm tra lại MCU đích và firmware trước khi xác nhận."
        )

    def _preflight_next_action(self) -> str:
        info = self.target_info
        if info is not None:
            if info.device_id & 0xFFF != 0x413 or info.flash_kib != 512:
                return "Chọn đúng ST-Link và board B300 STM32F407 512 KiB."
            if info.readout_protected:
                return "Dùng quy trình recovery đã được phê duyệt; không đổi RDP."
            if not info.protection_reported:
                return "Kiểm tra nguồn, SWD và nhật ký OpenOCD để đọc được bằng chứng WRP."
            if not {0, 1, 2}.issubset(info.protected_sectors):
                return "Dùng quy trình xuất xưởng được ủy quyền để bảo vệ bộ nạp khởi động S0–S2."
        return "Kiểm tra tệp HEX, ST-Link, nguồn và kết nối; xem nhật ký rồi bắt đầu lại thủ công."

    def inspect_target(self) -> None:
        self._begin_target_inspection()

    def _begin_target_inspection(self, continuation=None) -> None:
        if not self.app_context.selected_connection.is_local:
            self.append_log('THIẾT BỊ / NẠP PHẦN MỀM: chọn ST-Link cục bộ để kiểm tra phần cứng trực tiếp.')
            return
        if self._operation_state().is_hardware_busy or not self.openocd_ready or not self._probes:
            return
        try:
            probe = self._selected_probe()
        except ValueError as error:
            self.program_view.banner.show_info("Chọn ST-Link", str(error))
            return
        self._invalidate_target()
        revision = self._target_revision
        self.busy = True
        self._set_status("Đang kiểm tra MCU đích, WRP và RDP…", "busy", notify=False)
        self.program_view.banner.show_info("Đang kiểm tra an toàn", "Đọc MCU đích, bộ nhớ flash, WRP và RDP trước khi xác nhận.")
        self._update_controls()

        def completed(info):
            self.busy = False
            if revision != self._target_revision:
                return
            self.apply_target_info(info)
            if continuation is not None:
                # Continue only after QThread.finished releases GUI ownership.
                self.busy = True
                self._program_continuation = (revision, continuation)

        def failed(failure):
            self.busy = False
            if revision != self._target_revision:
                return
            self._operation_failed(failure)
            self.program_view.banner.show_fail(
                "Kiểm tra an toàn không đạt", failure.message, failure.next_action
            )

        self._start_worker(
            lambda log, phase, cancel: self.service.inspect_target(probe, event_sink=log, cancel_event=cancel),
            completed, cancellable=True, on_failed=failed,
        )

    def _worker_finished(self) -> None:
        super()._worker_finished()
        pending = getattr(self, "_program_continuation", None)
        if pending is not None and not self._threads:
            self._program_continuation = None
            self.busy = False
            self._update_controls()
            revision, continuation = pending
            if revision == self._target_revision and not self._operation_state().is_hardware_busy:
                continuation()
            self._finish_pending_close()

    def cancel_operation(self) -> None:
        super().cancel_operation()
        if self._cancellable_worker is not None:
            self._invalidate_target()

    def append_log(self, text: str) -> None:
        super().append_log(text)
        if hasattr(self, "program_view") and hasattr(self.program_view, "append_log"):
            self.program_view.append_log(text)
        if hasattr(self, 'debug_vscode_view'):
            self.debug_vscode_view.append_log(text)
        if hasattr(self, 'device_view'):
            self.device_view.activity_log.append(text)

    # ------------------------------------------------------------------
    # PROGRAM handlers: reuse existing proven safe flash/factory paths
    # ------------------------------------------------------------------
    def _on_v18_file_selected(self, path: Path) -> None:
        self.load_image_path(path, quiet=True)

    def _on_v18_image_invalidated(self) -> None:
        self.image_info = None
        self.flash_plan = None
        self._update_controls()
        self._render_program_readiness()

    def _on_v18_flash_application(self, path: Path, is_dry_run: bool) -> None:
        if self._operation_state().is_hardware_busy:
            return
        self.program_view.set_file_path(path)
        if self.image_info is None:
            self.program_view.banner.show_fail(
                "Tệp HEX ứng dụng không hợp lệ", self.program_view.app_meta_label.text(), "Chọn lại tệp HEX ứng dụng hợp lệ."
            )
            return
        image = self.image_info

        def continue_program():
            if self.image_info != image:
                self.program_view.banner.show_info("Phần mềm đã thay đổi", "Bấm nạp lại để kiểm tra tệp mới.")
                return
            if not self.target_ready or self.flash_plan is None:
                return
            self.show_dry_run() if is_dry_run else self.confirm_flash()

        self._begin_target_inspection(continue_program)

    def confirm_flash(self) -> None:
        plan = self.flash_plan
        revision = self._target_revision
        if plan is None or self._operation_state().is_hardware_busy:
            return
        if ConfirmFlashDialog(plan, self).exec() != QDialog.DialogCode.Accepted:
            return
        if (self._operation_state().is_hardware_busy or self.flash_plan is not plan
                or revision != self._target_revision):
            self.program_view.banner.show_info("Điều kiện nạp đã thay đổi", "Bấm nạp lại để kiểm tra trước khi xác nhận.")
            return
        self._start_flash()

    def _start_flash(self) -> None:
        self.program_view.banner.show_info("Đang nạp ứng dụng", "Không rút ST-Link hoặc ngắt nguồn.")
        super()._start_flash()

    def _flash_finished(self, result) -> None:
        super()._flash_finished(result)
        self.device_info_panel.set_latest_result(result.succeeded,
            "Ứng dụng đã được xác minh" if result.succeeded else result.reason,
            QDateTime.currentDateTime().toString("HH:mm:ss · dd/MM/yyyy"))
        # Programming/reset makes inspection a historical snapshot, not current evidence.
        self._invalidate_target()
        if result.succeeded:
            self.program_view.banner.show_pass("Nạp ứng dụng thành công", "Ứng dụng và STLM CONFIRMED đã được xác minh.")
        else:
            self.program_view.banner.show_fail("Nạp ứng dụng thất bại", result.reason, result.next_action)
        self._update_controls()

    def _operation_failed(self, failure) -> None:
        super()._operation_failed(failure)
        if hasattr(self, "program_view"):
            self._invalidate_target()
            self.program_view.banner.show_fail(
                "Thao tác không đạt", getattr(failure, "message", str(failure)),
                getattr(failure, "next_action", "Xem nhật ký và khắc phục trước khi thử lại thủ công."),
            )

    def _hardware_activity_changed(self, _busy: bool = False) -> None:
        if _busy:
            self._invalidate_target()
        super()._hardware_activity_changed(_busy)

    def _on_v18_flash_bootloader(self, confirmed: bool) -> None:
        if confirmed and self.app_context.selected_connection.is_local:
            self.start_factory_provision()

    def start_factory_provision(self) -> None:
        if hasattr(self, 'app_context') and not self.app_context.selected_connection.is_local:
            return
        if self._operation_state().is_hardware_busy:
            return
        super().start_factory_provision()

    def _factory_preflight_finished(self, probe, info) -> None:
        super()._factory_preflight_finished(probe, info)
        if self.busy:
            self._invalidate_target()
            self.program_view.banner.show_info("Đang nạp bộ nạp khởi động", "Không rút ST-Link hoặc ngắt nguồn.")

    def _factory_finished(self, result) -> None:
        super()._factory_finished(result)
        self.device_info_panel.set_latest_result(result.succeeded,
            "Bộ nạp khởi động và WRP đã được xác minh" if result.succeeded else result.reason,
            QDateTime.currentDateTime().toString("HH:mm:ss · dd/MM/yyyy"))
        self._invalidate_target()
        if result.succeeded:
            self.program_view.banner.show_pass("Quy trình xuất xưởng hoàn tất", "Bộ nạp khởi động và WRP S0–S2 đã được xác minh.")
        else:
            self.program_view.banner.show_fail("Quy trình xuất xưởng thất bại", result.reason, result.next_action)
        self._update_controls()

    def _factory_operation_failed(self, failure) -> None:
        super()._factory_operation_failed(failure)
        self._invalidate_target()
        self.program_view.banner.show_fail(
            "Quy trình xuất xưởng thất bại", getattr(failure, "message", str(failure)),
            getattr(failure, "next_action", "Xác minh WRP trước thao tác tiếp theo."),
        )

    # ------------------------------------------------------------------
    # VS Code environment / bridge
    # ------------------------------------------------------------------
    def _refresh_vscode_environment(self) -> None:
        try:
            status = self._vscode_controller.inspect_environment()
        except Exception as error:
            self._show_debug_error("Không thể kiểm tra môi trường gỡ lỗi", error)
            return
        self.debug_vscode_view.set_environment_status(status)
        self.settings_view.set_environment_status(status)
        if status.ready:
            self.append_log("Môi trường gỡ lỗi VS Code SẴN SÀNG.")
        else:
            self.append_log("Môi trường gỡ lỗi VS Code chưa sẵn sàng: %s" % status.reason)

    def _render_bridge_state(self) -> None:
        state = self._vscode_controller.state
        if state.state == BridgeState.READY:
            self._invalidate_target()
        if hasattr(self, "debug_vscode_view"):
            role = state.role.value if state.role is not None else None
            self.debug_vscode_view.set_bridge_state(
                role, state.state.value, state.detail, state.gdb_target
            )
        if hasattr(self, "settings_view"):
            host_role = state.role is not None and state.role.value.lower() == 'gateway'
            self.settings_view.set_gateway_status(state.state.value if host_role else 'STOPPED', state.detail if host_role else '')
        if hasattr(self, "shared_context_bar"):
            self.shared_context_bar.render()
        self._update_controls()

    def _selected_debug_probe(self):
        probe = self._selected_probe()
        if not self._probes or probe is None:
            raise ValueError('Không có ST-Link hợp lệ được chọn.')
        return probe

    def _selected_factory_probe(self):
        # Production has one probe selection; retain the canonical ambiguity guard.
        return self._selected_debug_probe()

    def _confirm_launch_overwrite(self, workspace: Path) -> bool:
        launch = Path(workspace) / '.vscode' / 'launch.json'
        answer = QMessageBox.question(self, 'B300 · launch.json đã tồn tại',
            'Thư mục làm việc đã có .vscode/launch.json:\n%s\n\nGhi đè bằng cấu hình chỉ kết nối của B300?' % launch,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _on_v18_open_local_vscode(self, workspace: Path, elf: Path) -> None:
        if self._operation_state().is_hardware_busy or not self.app_context.selected_connection.is_local:
            return
        force = False
        while True:
            try:
                result = self._vscode_controller.start_local(
                    probe=self._selected_debug_probe(), workspace=workspace,
                    symbols=elf, force_launch_json=force,
                )
                self._render_bridge_state()
                self.append_log(
                    "Gỡ lỗi VS Code cục bộ SẴN SÀNG · %s · tệp ký hiệu=%s" %
                    (result.state.gdb_target, result.symbols)
                )
                return
            except FileExistsError:
                if force or not self._confirm_launch_overwrite(workspace):
                    return
                force = True
            except Exception as error:
                self._render_bridge_state()
                self._show_debug_error("Không thể mở gỡ lỗi VS Code cục bộ", error)
                return

    def _on_v18_start_gateway(self) -> None:
        if self._operation_state().is_hardware_busy:
            return
        try:
            state = self._vscode_controller.start_gateway(probe=self._selected_debug_probe())
        except Exception as error:
            self._render_bridge_state()
            self._show_debug_error("Không thể khởi động Gateway", error)
            return
        self._render_bridge_state()
        self.append_log(
            "Gateway SẴN SÀNG · %s · OpenOCD chỉ liên kết cục bộ; TCL không được chuyển tiếp." %
            (state.gdb_target or "địa chỉ chưa được báo cáo")
        )

    def _on_v18_stop_gateway(self) -> None:
        self._on_v18_stop_bridge()

    def _on_v18_stop_bridge(self) -> None:
        try:
            state = self._vscode_controller.stop()
        except Exception as error:
            self._show_debug_error("Lỗi khi dừng cầu nối gỡ lỗi", error)
            return
        self._render_bridge_state()
        if state.detail:
            self.append_log(state.detail)
        else:
            self.append_log("Cầu nối gỡ lỗi ĐÃ DỪNG.")

    # ------------------------------------------------------------------
    # Shared Gateway / Project resources
    # ------------------------------------------------------------------
    def _refresh_shared_profiles(self) -> None:
        try:
            gateways = self._gateway_store.list()
            gateway_default = self._gateway_store.default_id()
        except Exception as error:
            self.append_log("Cảnh báo hồ sơ Gateway: %s" % error)
            gateways, gateway_default = (), None
        try:
            projects = self._project_store.list()
            project_default = self._project_store.default_id()
        except Exception as error:
            self.append_log("Cảnh báo dự án gỡ lỗi: %s" % error)
            projects, project_default = (), None
        self.app_context.set_profiles(projects, gateways, project_default, gateway_default)
        self.shared_context_bar.render()

    def _open_gateway_manager(self) -> None:
        if self._operation_state().is_hardware_busy:
            return
        dialog = GatewayManagerDialog(self._gateway_store, self._gateway_sessions, self)
        self._gateway_manager_dialog = dialog
        dialog.profiles_changed.connect(self._refresh_shared_profiles)
        dialog.exec()
        self._gateway_manager_dialog = None
        self._refresh_shared_profiles()

    def _open_project_manager(self) -> None:
        if self._operation_state().is_hardware_busy:
            return
        dialog = ProjectManagerDialog(self._project_store, self)
        self._project_manager_dialog = dialog
        dialog.profiles_changed.connect(self._refresh_shared_profiles)
        dialog.exec()
        self._project_manager_dialog = None
        self._refresh_shared_profiles()

    # Compatibility alias retained for older tests/callers.
    def _restore_v18_remote_profile(self) -> None:
        self._refresh_shared_profiles()

    @staticmethod
    def _profile_from_request(request: dict) -> RemoteGatewayProfile:
        return RemoteGatewayProfile(
            host=str(request.get("host", "")).strip(),
            user=str(request.get("user", "")).strip(),
            port=int(request.get("ssh_port", 22)),
        ).validate()

    def _gateway_profile_for_endpoint(self, endpoint: RemoteGatewayProfile) -> GatewayProfile:
        selected = endpoint.validate()
        for profile in self._gateway_store.list():
            if profile.endpoint == selected:
                return profile
        return GatewayProfile.create(selected.host, selected.host, selected.user, selected.port)

    def _session_matches(self, profile: RemoteGatewayProfile) -> bool:
        return self._gateway_sessions.connected(profile)

    def _get_or_create_remote_session(self, profile: RemoteGatewayProfile) -> RemoteSession:
        current = self._gateway_sessions.session(profile)
        self._vscode_remote_session = current
        return current

    def _monitor_remote_session(self, request) -> RemoteSession:
        profile = RemoteGatewayProfile(request.host, request.user, request.ssh_port).validate()
        if not self._session_matches(profile):
            self._show_remote_login(
                {"host": profile.host, "user": profile.user, "ssh_port": profile.port},
                launch_after=False,
            )
        if not self._session_matches(profile):
            raise RuntimeError("Đã hủy đăng nhập giám sát từ xa; hãy chọn hoặc kết nối lại Gateway.")
        return self._get_or_create_remote_session(profile)

    def _show_remote_login(self, request: dict, *, launch_after: bool) -> None:
        try:
            endpoint = self._profile_from_request(request)
            saved_profile = self._gateway_profile_for_endpoint(endpoint)
        except Exception as error:
            self._show_debug_error("Thông tin Gateway không hợp lệ", error)
            return

        session = self._get_or_create_remote_session(endpoint)
        if session.connected:
            self.debug_vscode_view.set_client_connection_status(True, session.endpoint)
            if launch_after:
                self._launch_remote_debug(request, session)
            return

        if self._gateway_sessions.has_cached_password(endpoint):
            try:
                self._gateway_sessions.connect(endpoint, timeout_seconds=12.0)
            except Exception:
                pass
            else:
                self._vscode_remote_session = self._gateway_sessions.session(endpoint)
                self.debug_vscode_view.set_client_connection_status(True, self._vscode_remote_session.endpoint)
                if launch_after:
                    self._launch_remote_debug(request, self._vscode_remote_session)
                return

        dialog = GatewayLoginDialog(saved_profile, self)
        self._remote_login_dialog = dialog
        self._pending_remote_request = dict(request)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._remote_login_dialog = None
            self._pending_remote_request = None
            return
        secret = dialog.password()
        try:
            self._gateway_sessions.connect(endpoint, secret, timeout_seconds=12.0)
            session = self._gateway_sessions.session(endpoint)
            self._vscode_remote_session = session
            persisted = self._gateway_store.get(saved_profile.profile_id)
            if persisted is not None:
                self._gateway_store.set_default(saved_profile.profile_id)
        except Exception as error:
            dialog.password_input.clear()
            self._remote_login_dialog = None
            self._pending_remote_request = None
            self.debug_vscode_view.set_client_connection_status(False, str(error))
            self._show_debug_error("Kết nối SSH thất bại", error)
            return
        finally:
            dialog.password_input.clear()

        self.debug_vscode_view.set_client_connection_status(True, session.endpoint)
        self.append_log("Máy khách SSH ĐÃ KẾT NỐI · %s" % session.endpoint)
        self._remote_login_dialog = None
        self._pending_remote_request = None
        self._refresh_shared_profiles()
        if launch_after:
            self._launch_remote_debug(request, session)

    def _on_v18_test_client_connection(self, request: dict) -> None:
        self._show_remote_login(request, launch_after=False)

    def _on_v18_open_remote_vscode(self, request: dict) -> None:
        self._show_remote_login(request, launch_after=True)

    def _launch_remote_debug(self, request: dict, session: RemoteSession) -> None:
        workspace = Path(request.get("workspace", ""))
        elf = Path(request.get("elf", ""))
        local_port = int(request.get("local_gdb_port", 0))
        force = False
        while True:
            try:
                result = self._vscode_controller.start_client(
                    session=session, workspace=workspace, symbols=elf,
                    local_gdb_port=local_port, force_launch_json=force,
                )
                self._render_bridge_state()
                self.debug_vscode_view.set_client_connection_status(
                    True, "SSH đã xác thực; GDB %s" % result.state.gdb_target
                )
                self.append_log(
                    "Gỡ lỗi VS Code từ xa SẴN SÀNG · %s · chỉ GDB được chuyển tiếp qua SSH." %
                    result.state.gdb_target
                )
                return
            except FileExistsError:
                if force or not self._confirm_launch_overwrite(workspace):
                    return
                force = True
            except Exception as error:
                self._render_bridge_state()
                self._show_debug_error("Không thể mở gỡ lỗi VS Code từ xa", error)
                return

    def _show_debug_error(self, title: str, error: BaseException) -> None:
        message = str(error).strip() or error.__class__.__name__
        self.append_log("%s: %s" % (title, message))
        QMessageBox.warning(self, title, message)

    # ------------------------------------------------------------------
    # Explicit legacy diagnostics only
    # ------------------------------------------------------------------
    def _on_v18_open_legacy_ide(self) -> None:
        QMessageBox.information(
            self,
            "Debug trong VS Code",
            "B300 v0.19 dùng VS Code + Cortex-Debug để gỡ lỗi tương tác. "
            "Công cụ chẩn đoán của B300 vẫn có trong NẠP PHẦN MỀM và THIẾT BỊ.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.monitor_view.controller.prepare_shutdown():
            event.ignore()
            return
        # Restore target state before the base window tears down shared services.
        try:
            self._vscode_controller.stop()
        except Exception as error:
            self.append_log("Cảnh báo khi đóng cầu nối gỡ lỗi: %s" % error)
        try:
            self._gateway_sessions.disconnect_all()
        except Exception as error:
            self.append_log("Cảnh báo khi ngắt SSH: %s" % error)
        self._vscode_remote_session = None
        super().closeEvent(event)


__all__ = ["MainWindowV18"]
