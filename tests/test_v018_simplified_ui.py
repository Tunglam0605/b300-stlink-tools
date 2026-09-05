"""Regression tests for the B300 v0.18 simplified production surface."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget

from b300_core.models import ImageInfo, ProbeInfo, TargetInfo
from b300_gui.main_window_v18 import MainWindowV18
from b300_gui.update_dialog import UpdateDialog
from b300_core.vscode_bridge import BridgeState, DebugRole, VsCodeBridgeState
from b300_gui.views.debug_vscode_view import DebugVsCodeView
from b300_gui.views.device_view import DeviceView
from b300_gui.views.monitor_view import MonitorView
from b300_gui.views.program_view import ProgramView
from b300_gui.views.settings_view import SettingsView


class V018SimplifiedUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_window(self) -> MainWindowV18:
        return MainWindowV18(
            probe_loader=lambda: (
                ProbeInfo(
                    name="ST-LINK/V2",
                    serial="066EFF535052877067142436",
                    source="usb",
                    usb_identity="ST-Link V2 USB",
                ),
            ),
            automatic_updates=False,
            first_run_setup=False,
        )

    def _close(self, window: MainWindowV18) -> None:
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_ready_bridge_roles_lock_hardware_and_update_install_until_stopped(self) -> None:
        window = self._make_window()
        try:
            window.program_view._selected_file = Path("application.hex")
            window.update_dialog = UpdateDialog(
                "0.18.0", SimpleNamespace(version="0.18.1", notes="", release_page=""),
                SimpleNamespace(), window,
            )
            window.update_dialog.set_ready(Path("update.zip"))
            locked_controls = (
                window.monitor_view.live_panel.start_button,
                window.monitor_view.role_selector,
                window.monitor_view.symbol_button,
                window.program_view.btn_flash_app,
                window.program_view.btn_flash_bootloader,
                window.program_view.btn_inspect_target,
                window.device_view.btn_doctor,
                window.header_bar.machine_setup_btn,
                window.header_bar.probe_refresh_btn,
                window.machine_setup_button,
                window.update_dialog.action_button,
                window.debug_vscode_view.btn_open_local_vscode,
                window.debug_vscode_view.btn_start_gateway,
                window.debug_vscode_view.btn_open_remote_vscode,
            )
            for role in DebugRole:
                with self.subTest(role=role):
                    with mock.patch.object(type(window._vscode_controller), "state",
                                           new_callable=mock.PropertyMock) as state:
                        state.return_value = VsCodeBridgeState(role, BridgeState.READY, "127.0.0.1:3333")
                        window._render_bridge_state()
                        self.assertTrue(window._operation_state().is_hardware_busy)
                        for control in locked_controls:
                            self.assertFalse(control.isEnabled(), str(control))
                        self.assertTrue(window.debug_vscode_view.btn_stop_bridge.isEnabled())
                        self.assertEqual(window.debug_vscode_view.btn_stop_gateway.isEnabled(),
                                         role == DebugRole.GATEWAY)
                        state.return_value = VsCodeBridgeState(None, BridgeState.STOPPED, None)
                        window._render_bridge_state()
                        self.assertFalse(window._operation_state().is_hardware_busy)
                        for control in locked_controls:
                            self.assertTrue(control.isEnabled(), str(control))
                        self.assertFalse(window.debug_vscode_view.btn_stop_bridge.isEnabled())
        finally:
            self._close(window)

    def test_client_monitor_handler_refuses_while_vscode_client_ready(self) -> None:
        from b300_gui.live_monitor_controller import LiveMonitorRequest
        window = self._make_window()
        try:
            created = []
            window.monitor_view.controller._session_factory = lambda **kwargs: created.append(kwargs)
            window.monitor_view.controller._remote_session_provider = lambda request: self.fail("busy Monitor attempted login")
            with mock.patch.object(type(window._vscode_controller), "state",
                                   new_callable=mock.PropertyMock) as state:
                state.return_value = VsCodeBridgeState(DebugRole.CLIENT, BridgeState.READY, "127.0.0.1:43333")
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    window.monitor_view.controller.start(LiveMonitorRequest.client(
                        None, host="gateway.local", user="operator", symbol_roots=(Path.cwd(),)))
            self.assertEqual(created, [])
            self.assertFalse(window.monitor_view.controller.active)
        finally:
            self._close(window)

    def test_monitor_client_reuses_authenticated_gui_session(self) -> None:
        from b300_core.remote_profile import RemoteGatewayProfile
        from tests.test_live_monitor_controller import _InlineWorker, _Session
        from b300_gui.live_monitor_controller import LiveMonitorRequest
        window = self._make_window()
        try:
            profile = RemoteGatewayProfile("gateway.local", "operator", 22)
            authenticated = SimpleNamespace(profile=profile, connected=True, disconnect=lambda: None)
            window._vscode_remote_session = authenticated
            received = []
            class ClientSession(_Session):
                def start_client(self, config, remote_session=None):
                    received.append(remote_session)
                    return self.start_local(config)
            controller = window.monitor_view.controller
            controller._worker_factory = _InlineWorker
            controller._session_factory = lambda **kwargs: ClientSession(())
            controller.start(LiveMonitorRequest.client(
                None, host="gateway.local", user="operator", symbol_roots=(Path.cwd(),)))
            self.assertEqual(received, [authenticated])
            self.assertFalse(controller.active)
        finally:
            self._close(window)

    def test_monitor_client_login_cancel_keeps_transport_stopped(self) -> None:
        from b300_gui.live_monitor_controller import LiveMonitorRequest
        window = self._make_window()
        try:
            created = []
            window.monitor_view.controller._session_factory = lambda **kwargs: created.append(kwargs)
            with mock.patch("b300_gui.main_window_v18.RemoteLoginDialog") as dialog:
                dialog.return_value.exec.return_value = 0
                with self.assertRaisesRegex(RuntimeError, "cancelled"):
                    window.monitor_view.controller.start(LiveMonitorRequest.client(
                        None, host="gateway.local", user="operator", symbol_roots=(Path.cwd(),)))
            self.assertEqual(created, [])
            self.assertFalse(window.monitor_view.controller.active)
        finally:
            self._close(window)

    def test_main_window_renders_with_five_primary_pages(self) -> None:
        window = self._make_window()
        try:
            self.assertEqual(len(window.v18_nav_buttons), 5)
            self.assertEqual(window.v18_stack.count(), 5)
            self.assertIsInstance(window.program_view, ProgramView)
            self.assertIsInstance(window.monitor_view, MonitorView)
            self.assertIsInstance(window.debug_vscode_view, DebugVsCodeView)
            self.assertIsInstance(window.device_view, DeviceView)
            self.assertIsInstance(window.settings_view, SettingsView)
            self.assertEqual(window.v18_stack.currentIndex(), 0)
            self.assertTrue(window.nav_program_btn.isChecked())
            self.assertNotIsInstance(window.tabs, QTabWidget)
            self.assertEqual(
                [tabs for tabs in window.findChildren(QTabWidget)
                 if tabs.objectName() != "debugLiveViewTabs"],
                [],
            )
        finally:
            self._close(window)

    def test_production_window_does_not_construct_hidden_legacy_workbenches(self) -> None:
        window = self._make_window()
        try:
            self.assertFalse(hasattr(window, "debug_tab"))
            self.assertFalse(hasattr(window, "gateway_tab"))
            self.assertFalse(hasattr(window, "memory_tab"))
            self.assertFalse(hasattr(window, "operator_view"))
            self.assertIs(window.monitor_view.live_panel.parent(), window.monitor_view)
            self.assertIs(window.monitor_view.controller.panel, window.monitor_view.live_panel)
        finally:
            self._close(window)

    def test_global_actions_are_unique_and_page_tools_are_not_duplicated(self) -> None:
        window = self._make_window()
        try:
            self.assertEqual(
                len(window.findChildren(QPushButton, "refreshProbeAction")), 1
            )
            self.assertEqual(
                len(window.findChildren(QPushButton, "machineSetupAction")), 1
            )
            self.assertEqual(
                len(window.findChildren(QPushButton, "checkUpdateAction")), 1
            )
            self.assertTrue(window.program_view.btn_refresh_probe.isHidden())
            self.assertTrue(window.device_view.btn_refresh.isHidden())
            self.assertTrue(window.settings_view.btn_run_setup.isHidden())
            self.assertTrue(window.settings_view.btn_toggle_theme.isHidden())
            self.assertTrue(window.machine_setup_button.isHidden())
            self.assertTrue(window.update_channel_label.isHidden())
            self.assertTrue(window.header_bar.segmented_control.isHidden())
        finally:
            self._close(window)

    def test_bootloader_action_routes_to_the_guarded_factory_workflow(self) -> None:
        window = self._make_window()
        try:
            with mock.patch.object(window, "start_factory_provision") as start:
                window._on_v18_flash_bootloader(True)
            start.assert_called_once_with()
        finally:
            self._close(window)

    def test_sidebar_navigation_switches_pages_without_hardware_side_effects(self) -> None:
        window = self._make_window()
        try:
            for name, index in (
                ("monitor", 1), ("debug", 2), ("device", 3),
                ("settings", 4), ("program", 0),
            ):
                window.show_page(name)
                self.app.processEvents()
                self.assertEqual(window.v18_stack.currentIndex(), index)
                self.assertFalse(window.busy)
                self.assertIsNone(window._cancellable_worker)
            self.assertIsNone(window._vscode_controller.state.role)
        finally:
            self._close(window)

    def test_program_page_operator_oriented_layout(self) -> None:
        window = self._make_window()
        try:
            view = window.program_view
            self.assertIn("ST-LINK", view.lbl_probe.text().upper())
            self.assertIn("STM32F407", view.lbl_target.text())
            self.assertTrue(view.radio_local.isChecked())
            self.assertEqual(view.btn_flash_app.text(), "⚡ NẠP APPLICATION")
            self.assertFalse(view.btn_flash_app.isEnabled())
            self.assertFalse(view.adv_card.is_expanded())
        finally:
            self._close(window)

    def test_program_rejects_non_hex_and_uses_real_imageinfo_fields(self) -> None:
        view = ProgramView()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                elf = root / "application.elf"
                elf.write_bytes(b"elf")
                with mock.patch("b300_gui.views.program_view.inspect_image") as inspect:
                    view.set_file_path(elf)
                inspect.assert_not_called()
                self.assertFalse(view.btn_flash_app.isEnabled())
                self.assertIn("Intel HEX", view.app_meta_label.text())

                image_path = root / "application.hex"
                image_path.write_text(":00000001FF\n", encoding="ascii")
                image = ImageInfo(
                    path=image_path,
                    sha256="A" * 64,
                    start_address=0x08010000,
                    end_address=0x08010FFF,
                    size=4096,
                    data_record_count=10,
                    reset_vector=0x08010101,
                    flash_span_size=4096,
                    flash_crc32=0x12345678,
                )
                with mock.patch("b300_gui.views.program_view.inspect_image", return_value=image):
                    view.set_file_path(image_path)
                self.assertTrue(view.btn_flash_app.isEnabled())
                self.assertIn("4096 B", view.app_meta_label.text())
                self.assertIn("0x12345678", view.app_meta_label.text())
                self.assertIn("0x08010101", view.app_meta_label.text())
        finally:
            view.deleteLater()
            self.app.processEvents()

    def test_program_hides_unavailable_remote_tools_and_keeps_factory_advanced(self) -> None:
        window = self._make_window()
        try:
            view = window.program_view
            self.assertTrue(view.radio_local.isHidden())
            self.assertTrue(view.radio_remote.isHidden())
            self.assertTrue(view.remote_panel.isHidden())
            self.assertIs(view.bootloader_card.parent(), view.adv_card.content_widget)
            self.assertFalse(view.adv_card.is_expanded())
        finally:
            self._close(window)

    def test_debug_page_defaults_to_vscode_workflow(self) -> None:
        window = self._make_window()
        try:
            window.show_page("debug")
            view = window.debug_vscode_view
            self.assertFalse(hasattr(view, "workbench"))
            self.assertEqual(view._current_mode, "local")
            self.assertEqual(view.mode_stack.currentIndex(), 0)
            self.assertTrue(view.btn_mode_local.isChecked())
            self.assertEqual(view.btn_open_local_vscode.text(), "🚀 OPEN DEBUG IN VS CODE")
            self.assertTrue(view.btn_open_local_vscode.isEnabled())
            self.assertFalse(hasattr(view, "debug_service"))
            self.assertFalse(hasattr(view, "remote_session"))
        finally:
            self._close(window)

    def test_debug_page_gateway_mode_strictly_loopback(self) -> None:
        window = self._make_window()
        try:
            view = window.debug_vscode_view
            view.select_mode("gateway")
            self.app.processEvents()
            self.assertEqual(view.mode_stack.currentIndex(), 1)
            self.assertIn("127.0.0.1", view.gw_openocd_lbl.text())
            self.assertNotIn("0.0.0.0", view.gw_openocd_lbl.text())
            self.assertFalse(view.gateway_details_card.is_expanded())
            self.assertTrue(view.btn_start_gateway.isEnabled())
            self.assertFalse(view.btn_stop_gateway.isEnabled())
        finally:
            self._close(window)

    def test_debug_page_client_mode_cta_and_tunnel(self) -> None:
        window = self._make_window()
        try:
            view = window.debug_vscode_view
            view.select_mode("client")
            self.app.processEvents()
            self.assertEqual(view.mode_stack.currentIndex(), 2)
            self.assertEqual(view.btn_open_remote_vscode.text(), "🚀 OPEN REMOTE DEBUG IN VS CODE")
            self.assertEqual(view.btn_test_client_conn.text(), "⚡ TEST CONNECTION")
            self.assertEqual(view.client_local_gdb_spin.value(), 0)
            self.assertFalse(view.client_details_card.is_expanded())
        finally:
            self._close(window)

    def test_live_monitor_owns_a_production_controller_and_panel(self) -> None:
        window = self._make_window()
        try:
            controller = window.monitor_view.controller
            self.assertIs(controller.panel, window.monitor_view.live_panel)
            self.assertFalse(hasattr(window, "debug_tab"))
            self.assertIs(window.monitor_view.live_panel.parent(), window.monitor_view)
            self.assertIsNotNone(controller._selected_probe)
            window.show_page("monitor")
            self.assertFalse(window.busy)
            self.assertIsNone(window._cancellable_worker)
            self.assertFalse(controller.active)
        finally:
            self._close(window)

    def test_device_defaults_are_not_optimistically_healthy(self) -> None:
        view = DeviceView()
        try:
            self.assertEqual(view.val_dev_id.text(), "Chưa kiểm tra")
            self.assertEqual(view.val_flash_size.text(), "Chưa kiểm tra")
            self.assertEqual(view.val_voltage.text(), "Chưa kiểm tra")
            self.assertEqual(view.val_wrp.text(), "Chưa kiểm tra")
            self.assertEqual(view.val_rdp.text(), "Chưa kiểm tra")
        finally:
            view.deleteLater()
            self.app.processEvents()

    def test_target_info_syncs_across_views(self) -> None:
        window = self._make_window()
        try:
            info = TargetInfo(
                device_id=0x101F6413,
                flash_kib=512,
                target_voltage=3.28,
                protection_summary="S0-S2 protected",
                protected_sectors=(0, 1, 2),
                protection_reported=True,
                readout_protected=False,
            )
            window.apply_target_info(info)
            self.app.processEvents()
            self.assertIn("512 KB", window.program_view.lbl_target.text())
            self.assertIn("512KB", window.debug_vscode_view.local_target_status.text())
            self.assertEqual(window.device_view.val_flash_size.text(), "512 KB")
            self.assertEqual(window.device_view.val_dev_id.text(), "0x101F6413")
            self.assertIn("PROTECTED", window.device_view.val_wrp.text())
        finally:
            self._close(window)

    def test_window_close_is_refused_until_monitor_cleanup_finishes(self) -> None:
        window = self._make_window()
        original = window.monitor_view.controller.prepare_shutdown
        try:
            window.monitor_view.controller.prepare_shutdown = lambda: False
            event = QCloseEvent()
            window.closeEvent(event)
            self.assertFalse(event.isAccepted())
        finally:
            window.monitor_view.controller.prepare_shutdown = original
            self._close(window)

    def test_monitor_activity_participates_in_global_hardware_interlock(self) -> None:
        window = self._make_window()
        try:
            window.monitor_view.controller._active = True
            state = window._operation_state()
            self.assertTrue(state.debug_hardware_busy)
            window._hardware_activity_changed(True)
            self.assertFalse(window.program_view.btn_inspect_target.isEnabled())
            self.assertFalse(window.device_view.btn_refresh.isEnabled())
            self.assertFalse(window.device_view.btn_doctor.isEnabled())
            self.assertFalse(window.debug_vscode_view.btn_open_local_vscode.isEnabled())
            self.assertFalse(window.debug_vscode_view.btn_start_gateway.isEnabled())
            self.assertFalse(window.debug_vscode_view.btn_test_client_conn.isEnabled())
            self.assertFalse(window.debug_vscode_view.btn_open_remote_vscode.isEnabled())
        finally:
            window.monitor_view.controller._active = False
            self._close(window)

    def test_device_page_is_read_only_and_does_not_duplicate_global_actions(self) -> None:
        window = self._make_window()
        try:
            self.assertTrue(window.device_view.btn_refresh.isHidden())
            self.assertTrue(window.device_view.btn_doctor.isHidden())
            self.assertFalse(window.debug_vscode_view.environment_details_card.is_expanded())
        finally:
            self._close(window)


if __name__ == "__main__":
    unittest.main()
