"""Regression tests for the B300 v0.18 simplified production surface."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from b300_core.models import ImageInfo, ProbeInfo, TargetInfo
from b300_gui.main_window_v18 import MainWindowV18
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

    def test_program_remote_programming_is_visible_but_fail_closed(self) -> None:
        window = self._make_window()
        try:
            view = window.program_view
            view.radio_remote.setChecked(True)
            self.app.processEvents()
            self.assertTrue(view.local_panel.isHidden())
            self.assertFalse(view.remote_panel.isHidden())
            self.assertFalse(view.btn_remote_flash.isHidden())
            self.assertFalse(view.btn_remote_flash.isEnabled())
            self.assertIn("CHƯA BẬT", view.btn_remote_flash.text())
            self.assertEqual(len(view.pipeline_labels), 5)
            self.assertIn("Upload", view.pipeline_labels[0].text())
            self.assertIn("Verify", view.pipeline_labels[4].text())
            self.assertFalse(view.btn_remote_bootloader.isEnabled())
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
            self.assertEqual(view.client_local_gdb_spin.value(), 43333)
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


if __name__ == "__main__":
    unittest.main()
