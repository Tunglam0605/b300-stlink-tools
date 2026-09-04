"""Production MainWindow for B300 ST-Link Tools v0.18 Simplified UX.

B300 v0.18 intentionally is not an IDE.  The production surface is:
PROGRAM / MONITOR / DEBUG / DEVICE / SETTINGS, with interactive debugging
outsourced to VS Code + Cortex-Debug while B300 retains ST-Link/OpenOCD/SSH and
run-state safety ownership.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QMessageBox, QPushButton, QStackedWidget

from b300_core.models import ProbeInfo, TargetInfo
from b300_core.remote_profile import RemoteGatewayProfile, load_remote_profile, save_remote_profile
from b300_core.remote_session import RemoteSession
from .main_window import MainWindow
from .operation_state import OperationState
from .remote_login_dialog import RemoteLoginDialog
from .vscode_debug_controller import VsCodeDebugController
from .views.debug_vscode_view import DebugVsCodeView
from .views.device_view import DeviceView
from .views.monitor_view import MonitorView
from .views.program_view import ProgramView
from .views.settings_view import SettingsView


class MainWindowV18(MainWindow):
    """v0.18 simplified window and explicit VS Code debug orchestration."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("legacy_workbenches", False)
        super().__init__(*args, **kwargs)
        # Reuse the base DebugService so HardwareSession arbitration remains
        # authoritative across flash, monitor and interactive debug.
        self._vscode_controller = VsCodeDebugController(debug_service=self.debug_service)
        self._vscode_remote_session: Optional[RemoteSession] = None
        self._remote_login_dialog: Optional[RemoteLoginDialog] = None
        self._pending_remote_request: Optional[dict] = None
        self._configure_v18_navigation()
        self._configure_v18_views()
        self._restore_v18_remote_profile()
        self.show_page("program")

    # ------------------------------------------------------------------
    # Main navigation
    # ------------------------------------------------------------------
    def _configure_v18_navigation(self) -> None:
        # Keep global utilities in one predictable place. Page-local duplicates
        # remain as compatibility attributes but are not part of production UX.
        self.header_bar.segmented_control.hide()
        self.header_bar.probe_refresh_btn.setObjectName("refreshProbeAction")
        self.header_bar.machine_setup_btn.setObjectName("machineSetupAction")
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
            ("program", "⚡  PROGRAM", "Nạp Application & Bootloader an toàn"),
            ("monitor", "📈  MONITOR", "Zero-Halt realtime observation"),
            ("debug", "🐞  DEBUG", "VS Code + Cortex-Debug: LOCAL / GATEWAY / CLIENT"),
            ("device", "🔍  DEVICE", "Target MCU, ST-Link, Option Bytes / WRP"),
            ("settings", "⚙️  SETTINGS", "Thiết lập máy, cập nhật và diagnostics"),
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
        self.program_view.probe_refresh_requested.connect(self.refresh_probes)
        self.program_view.target_inspect_requested.connect(self.inspect_target)
        self.program_view.btn_refresh_probe.hide()
        self.v18_stack.addWidget(self.program_view)

        self.monitor_view = MonitorView(
            self,
            selected_probe=self._selected_probe,
            openocd_executable=str(self.debug_service.executable),
        )
        self.monitor_view.log.connect(self.append_log)
        self.monitor_view.operation_state_changed.connect(self._hardware_activity_changed)
        self.v18_stack.addWidget(self.monitor_view)

        self.debug_vscode_view = DebugVsCodeView(self)
        self.debug_vscode_view.open_local_vscode_requested.connect(self._on_v18_open_local_vscode)
        self.debug_vscode_view.open_remote_vscode_requested.connect(self._on_v18_open_remote_vscode)
        self.debug_vscode_view.test_client_connection_requested.connect(self._on_v18_test_client_connection)
        self.debug_vscode_view.start_gateway_requested.connect(self._on_v18_start_gateway)
        self.debug_vscode_view.stop_gateway_requested.connect(self._on_v18_stop_gateway)
        self.debug_vscode_view.stop_bridge_requested.connect(self._on_v18_stop_bridge)
        self.debug_vscode_view.refresh_environment_requested.connect(self._refresh_vscode_environment)
        self.debug_vscode_view.legacy_ide_requested.connect(self._on_v18_open_legacy_ide)
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
        self.settings_view.btn_run_setup.hide()
        self.settings_view.btn_toggle_theme.hide()
        self.settings_view.btn_check_updates.setObjectName("checkUpdateAction")
        self.v18_stack.addWidget(self.settings_view)

        content_area = self.tabs.parentWidget()
        if content_area is not None and content_area.layout() is not None:
            content_area.layout().addWidget(self.v18_stack)

        self.program_view.set_probes(self._probes)
        self.debug_vscode_view.set_probes(self._probes)
        self.device_view.set_probes(self._probes)
        if self.target_info is not None:
            self.program_view.set_target_info(self.target_info)
            self.debug_vscode_view.set_target_info(self.target_info)
            self.device_view.set_target_info(self.target_info)
        self._render_bridge_state()

    def show_page(self, page_name: str) -> None:
        page_map = {
            "program": (0, self.nav_program_btn, "PROGRAM · Nạp firmware",
                        "Nạp Application hoặc Bootloader an toàn; Sector 0–2 được bảo vệ."),
            "monitor": (1, self.nav_monitor_btn, "MONITOR · Theo dõi realtime",
                        "Zero-halt observation: quan sát mà không dừng MCU."),
            "debug": (2, self.nav_debug_btn_v18, "DEBUG · VS Code Bridge",
                      "LOCAL, GATEWAY hoặc CLIENT; mở trang không khởi động OpenOCD/GDB."),
            "device": (3, self.nav_device_btn, "DEVICE · Thông số phần cứng",
                      "Target STM32F407, VDD, Option Bytes và WRP Bootloader."),
            "settings": (4, self.nav_settings_btn, "SETTINGS · Thiết lập môi trường",
                         "Driver, B300 runtime, giao diện và cập nhật."),
        }
        entry = page_map.get(str(page_name).lower())
        if entry is None:
            return
        index, active, title, subtitle = entry
        for button in self.v18_nav_buttons:
            button.setChecked(button is active)
        self.v18_stack.setCurrentIndex(index)
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)

    # ------------------------------------------------------------------
    # Shared hardware state
    # ------------------------------------------------------------------
    def _operation_state(self) -> OperationState:
        base = super()._operation_state()
        monitor = getattr(self, "monitor_view", None)
        monitor_busy = bool(
            monitor is not None and monitor.controller.active
        )
        return OperationState(
            main_hardware_busy=base.main_hardware_busy,
            memory_hardware_busy=base.memory_hardware_busy,
            debug_hardware_busy=base.debug_hardware_busy or monitor_busy,
        )

    def _update_controls(self) -> None:
        super()._update_controls()
        hardware_busy = self._operation_state().is_hardware_busy
        if hasattr(self, "program_view"):
            self.program_view.set_busy(hardware_busy)
        if hasattr(self, "device_view"):
            self.device_view.set_busy(hardware_busy)
        if hasattr(self, "debug_vscode_view"):
            self.debug_vscode_view.set_hardware_busy(hardware_busy)

    def refresh_probes(self) -> None:
        super().refresh_probes()
        if hasattr(self, "program_view"):
            self.program_view.set_probes(self._probes)
        if hasattr(self, "debug_vscode_view"):
            self.debug_vscode_view.set_probes(self._probes)
        if hasattr(self, "device_view"):
            self.device_view.set_probes(self._probes)

    def apply_target_info(self, info: TargetInfo) -> None:
        super().apply_target_info(info)
        if hasattr(self, "program_view"):
            self.program_view.set_target_info(info)
        if hasattr(self, "debug_vscode_view"):
            self.debug_vscode_view.set_target_info(info)
        if hasattr(self, "device_view"):
            self.device_view.set_target_info(info)

    def append_log(self, text: str) -> None:
        super().append_log(text)
        if hasattr(self, "program_view") and hasattr(self.program_view, "append_log"):
            self.program_view.append_log(text)

    # ------------------------------------------------------------------
    # PROGRAM handlers: reuse existing proven safe flash/factory paths
    # ------------------------------------------------------------------
    def _on_v18_file_selected(self, path: Path) -> None:
        self.load_image_path(path, quiet=True)

    def _on_v18_flash_application(self, path: Path, is_dry_run: bool) -> None:
        if self.image_info is None or Path(self.image_info.path) != path:
            if not self.load_image_path(path):
                return
        if not self.target_ready or self.flash_plan is None:
            self._set_status(
                "Chưa thể nạp: kiểm tra Target B300, WRP Bootloader và flash plan.", "error"
            )
            self.program_view.banner.show_fail(
                "CHƯA SẴN SÀNG NẠP FIRMWARE",
                "Target hoặc flash plan chưa đạt điều kiện an toàn.",
                next_action="Bấm 'Kiểm tra Target' để xác minh WRP Bootloader trước khi nạp.",
            )
            return
        self.show_dry_run() if is_dry_run else self.confirm_flash()

    def _on_v18_flash_bootloader(self, confirmed: bool) -> None:
        if confirmed:
            self.confirm_factory_provision()

    # ------------------------------------------------------------------
    # VS Code environment / bridge
    # ------------------------------------------------------------------
    def _refresh_vscode_environment(self) -> None:
        try:
            status = self._vscode_controller.inspect_environment()
        except Exception as error:
            self._show_debug_error("Không thể kiểm tra môi trường debug", error)
            return
        self.debug_vscode_view.set_environment_status(status)
        if status.ready:
            self.append_log("VS Code debug environment READY.")
        else:
            self.append_log("VS Code debug environment chưa sẵn sàng: %s" % status.reason)

    def _render_bridge_state(self) -> None:
        if not hasattr(self, "debug_vscode_view"):
            return
        state = self._vscode_controller.state
        role = state.role.value if state.role is not None else None
        self.debug_vscode_view.set_bridge_state(
            role, state.state.value, state.detail, state.gdb_target
        )

    def _selected_debug_probe(self):
        probe = self._selected_probe()
        if probe is None:
            raise RuntimeError("Không có ST-Link hợp lệ được chọn.")
        return probe

    def _confirm_launch_overwrite(self, workspace: Path) -> bool:
        launch = Path(workspace) / ".vscode" / "launch.json"
        answer = QMessageBox.question(
            self,
            "B300 · launch.json đã tồn tại",
            "Workspace đã có .vscode/launch.json:\n%s\n\n"
            "B300 sẽ ghi cấu hình attach-only an toàn cho STM32F407. Ghi đè file này?" % launch,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_v18_open_local_vscode(self, workspace: Path, elf: Path) -> None:
        force = False
        while True:
            try:
                result = self._vscode_controller.start_local(
                    probe=self._selected_debug_probe(), workspace=workspace,
                    symbols=elf, force_launch_json=force,
                )
                self._render_bridge_state()
                self.append_log(
                    "LOCAL VS Code debug READY · %s · symbols=%s" %
                    (result.state.gdb_target, result.symbols)
                )
                return
            except FileExistsError:
                if force or not self._confirm_launch_overwrite(workspace):
                    return
                force = True
            except Exception as error:
                self._render_bridge_state()
                self._show_debug_error("Không thể mở LOCAL debug", error)
                return

    def _on_v18_start_gateway(self) -> None:
        try:
            state = self._vscode_controller.start_gateway(probe=self._selected_debug_probe())
        except Exception as error:
            self._render_bridge_state()
            self._show_debug_error("Không thể khởi động Gateway", error)
            return
        self._render_bridge_state()
        self.append_log(
            "Gateway READY · %s · OpenOCD chỉ bind loopback; TCL không được forward." %
            (state.gdb_target or "127.0.0.1:3333")
        )

    def _on_v18_stop_gateway(self) -> None:
        self._on_v18_stop_bridge()

    def _on_v18_stop_bridge(self) -> None:
        try:
            state = self._vscode_controller.stop()
        except Exception as error:
            self._show_debug_error("Lỗi khi dừng debug bridge", error)
            return
        self._render_bridge_state()
        if state.detail:
            self.append_log(state.detail)
        else:
            self.append_log("Debug bridge STOPPED.")

    # ------------------------------------------------------------------
    # CLIENT SSH + GDB tunnel
    # ------------------------------------------------------------------
    def _restore_v18_remote_profile(self) -> None:
        try:
            profile = load_remote_profile()
        except Exception as error:
            self.append_log("Remote Gateway profile warning: %s" % error)
            return
        if profile is None or not hasattr(self, "debug_vscode_view"):
            return
        self.debug_vscode_view.client_host.setText(profile.host)
        self.debug_vscode_view.client_user.setText(profile.user)
        self.debug_vscode_view.client_ssh_port.setValue(profile.port)

    @staticmethod
    def _profile_from_request(request: dict) -> RemoteGatewayProfile:
        return RemoteGatewayProfile(
            host=str(request.get("host", "")).strip(),
            user=str(request.get("user", "")).strip(),
            port=int(request.get("ssh_port", 22)),
        ).validate()

    def _session_matches(self, profile: RemoteGatewayProfile) -> bool:
        session = self._vscode_remote_session
        return bool(session is not None and session.profile == profile and session.connected)

    def _get_or_create_remote_session(self, profile: RemoteGatewayProfile) -> RemoteSession:
        current = self._vscode_remote_session
        if current is not None and current.profile != profile:
            current.disconnect()
            current = None
        if current is None:
            current = RemoteSession(profile)
            self._vscode_remote_session = current
        return current

    def _show_remote_login(self, request: dict, *, launch_after: bool) -> None:
        try:
            profile = self._profile_from_request(request)
        except Exception as error:
            self._show_debug_error("Thông tin Gateway không hợp lệ", error)
            return
        if self._session_matches(profile):
            self.debug_vscode_view.set_client_connection_status(True, self._vscode_remote_session.endpoint)
            if launch_after:
                self._launch_remote_debug(request, self._vscode_remote_session)
            return

        dialog = RemoteLoginDialog(profile.host, profile.user, profile.port, self)
        self._remote_login_dialog = dialog
        self._pending_remote_request = dict(request)

        # If a remembered credential exists, an empty password asks RemoteSession
        # to load it from the encrypted per-user credential store.
        session = self._get_or_create_remote_session(profile)
        try:
            has_remembered = session.credential_store.load(profile) is not None
        except Exception:
            has_remembered = False
        dialog.set_has_remembered_credential(has_remembered)

        def login(host: str, user: str, password: str, port: int, remember: bool) -> None:
            try:
                selected = RemoteGatewayProfile(host, user, port).validate()
                selected_session = self._get_or_create_remote_session(selected)
                selected_session.ensure_connected(
                    password=password or None, remember=remember, timeout_seconds=12.0
                )
                save_remote_profile(selected)
            except Exception as error:
                dialog.set_login_error(str(error))
                self.debug_vscode_view.set_client_connection_status(False, str(error))
                return
            dialog.set_login_success()
            self.debug_vscode_view.client_host.setText(selected.host)
            self.debug_vscode_view.client_user.setText(selected.user)
            self.debug_vscode_view.client_ssh_port.setValue(selected.port)
            self.debug_vscode_view.set_client_connection_status(True, selected_session.endpoint)
            self.append_log("SSH Client CONNECTED · %s" % selected_session.endpoint)
            if launch_after:
                next_request = dict(request)
                next_request.update(host=selected.host, user=selected.user, ssh_port=selected.port)
                self._launch_remote_debug(next_request, selected_session)

        dialog.login_requested.connect(login)
        dialog.exec()
        self._remote_login_dialog = None
        self._pending_remote_request = None

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
                    True, "SSH authenticated; GDB %s" % result.state.gdb_target
                )
                self.append_log(
                    "CLIENT VS Code debug READY · %s · only GDB forwarded through SSH." %
                    result.state.gdb_target
                )
                return
            except FileExistsError:
                if force or not self._confirm_launch_overwrite(workspace):
                    return
                force = True
            except Exception as error:
                self._render_bridge_state()
                self._show_debug_error("Không thể mở Remote VS Code debug", error)
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
            "B300 v0.18 dùng VS Code + Cortex-Debug cho debug tương tác. "
            "Diagnostics của B300 vẫn có trong PROGRAM và DEVICE.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.monitor_view.controller.prepare_shutdown():
            event.ignore()
            return
        # Restore target state before the base window tears down shared services.
        try:
            self._vscode_controller.stop()
        except Exception as error:
            self.append_log("Debug bridge shutdown warning: %s" % error)
        session = self._vscode_remote_session
        if session is not None:
            try:
                session.disconnect()
            except Exception as error:
                self.append_log("SSH shutdown warning: %s" % error)
        super().closeEvent(event)


__all__ = ["MainWindowV18"]
