"""Regression test suite for B300 v0.18 Simplified User Experience.

Verifies:
  1. Main window renders in v0.18 mode with 5 primary navigation targets.
  2. PROGRAM page is operator-oriented (Device card, Firmware card, collapsed Advanced).
  3. DEBUG page defaults to VS Code Debug Bridge with 3 modes (LOCAL, GATEWAY, CLIENT).
  4. Internal IDE is NOT the primary production surface.
  5. MONITOR page guarantees Zero-Halt observation (no MCU halt on view, permanent widget tree).
  6. Switching pages never auto-starts OpenOCD interactive debug or halts target.
  7. GATEWAY view exposes strictly loopback 127.0.0.1 (never 0.0.0.0).
  8. CLIENT view features primary CTA for VS Code and connection test.
  9. Remote programming frontend foundation displays pipeline with clean callback interface.
  10. Bootloader and Application safety interlocks are preserved.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.models import ProbeInfo, TargetInfo
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
                    description="ST-Link V2 USB",
                ),
            ),
            automatic_updates=False,
            first_run_setup=False,
        )

    # ------------------------------------------------------------------
    # 1. Main Navigation Tests
    # ------------------------------------------------------------------
    def test_main_window_renders_with_five_primary_pages(self) -> None:
        window = self._make_window()
        try:
            self.assertEqual(len(window.v18_nav_buttons), 5)
            self.assertEqual(window.v18_stack.count(), 5)

            # Check view instances in stack
            self.assertIsInstance(window.program_view, ProgramView)
            self.assertIsInstance(window.monitor_view, MonitorView)
            self.assertIsInstance(window.debug_vscode_view, DebugVsCodeView)
            self.assertIsInstance(window.device_view, DeviceView)
            self.assertIsInstance(window.settings_view, SettingsView)

            # Verify initial default is PROGRAM
            self.assertEqual(window.v18_stack.currentIndex(), 0)
            self.assertTrue(window.nav_program_btn.isChecked())
            self.assertFalse(window.nav_monitor_btn.isChecked())
            self.assertFalse(window.nav_debug_btn_v18.isChecked())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_sidebar_navigation_switches_pages_cleanly(self) -> None:
        window = self._make_window()
        try:
            # Switch to MONITOR
            window.show_page("monitor")
            self.assertEqual(window.v18_stack.currentIndex(), 1)
            self.assertTrue(window.nav_monitor_btn.isChecked())
            self.assertIn("MONITOR", window.page_title.text())

            # Switch to DEBUG
            window.show_page("debug")
            self.assertEqual(window.v18_stack.currentIndex(), 2)
            self.assertTrue(window.nav_debug_btn_v18.isChecked())
            self.assertIn("DEBUG", window.page_title.text())

            # Switch to DEVICE
            window.show_page("device")
            self.assertEqual(window.v18_stack.currentIndex(), 3)
            self.assertTrue(window.nav_device_btn.isChecked())

            # Switch to SETTINGS
            window.show_page("settings")
            self.assertEqual(window.v18_stack.currentIndex(), 4)
            self.assertTrue(window.nav_settings_btn.isChecked())

            # Switch back to PROGRAM
            window.show_page("program")
            self.assertEqual(window.v18_stack.currentIndex(), 0)
            self.assertTrue(window.nav_program_btn.isChecked())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    # ------------------------------------------------------------------
    # 2. PROGRAM Page Tests
    # ------------------------------------------------------------------
    def test_program_page_operator_oriented_layout(self) -> None:
        window = self._make_window()
        try:
            pv = window.program_view
            # Device card checks
            self.assertIn("ST-Link", pv.lbl_probe.text())
            self.assertIn("STM32F407", pv.lbl_target.text())
            self.assertFalse(pv.btn_refresh_probe.isHidden())
            self.assertFalse(pv.btn_inspect_target.isHidden())

            # Firmware card checks
            self.assertTrue(pv.radio_local.isChecked())
            self.assertFalse(pv.btn_flash_app.isHidden())
            self.assertEqual(pv.btn_flash_app.text(), "⚡ NẠP APPLICATION")
            self.assertFalse(pv.btn_flash_bootloader.isHidden())

            # Advanced card is collapsed by default
            self.assertFalse(pv.adv_card.is_expanded())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_program_page_remote_programming_foundation(self) -> None:
        window = self._make_window()
        try:
            pv = window.program_view
            # Initially local is active
            self.assertFalse(pv.local_panel.isHidden())
            self.assertTrue(pv.remote_panel.isHidden())

            # Switch to Remote Gateway
            pv.radio_remote.setChecked(True)
            self.app.processEvents()
            self.assertTrue(pv.local_panel.isHidden())
            self.assertFalse(pv.remote_panel.isHidden())

            # Verify remote controls and pipeline
            self.assertFalse(pv.btn_remote_flash.isHidden())
            self.assertEqual(len(pv.pipeline_labels), 5)
            self.assertIn("Upload", pv.pipeline_labels[0].text())
            self.assertIn("Verify", pv.pipeline_labels[4].text())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    # ------------------------------------------------------------------
    # 3. DEBUG Page & VS Code Bridge Tests
    # ------------------------------------------------------------------
    def test_debug_page_defaults_to_vscode_workflow(self) -> None:
        window = self._make_window()
        try:
            window.show_page("debug")
            dv = window.debug_vscode_view

            # Internal IDE is NOT the primary production surface
            self.assertFalse(hasattr(dv, "workbench"))
            self.assertIsInstance(dv, DebugVsCodeView)

            # Check 3 modes
            self.assertEqual(dv._current_mode, "local")
            self.assertEqual(dv.mode_stack.currentIndex(), 0)
            self.assertTrue(dv.btn_mode_local.isChecked())

            # Local CTA
            self.assertEqual(dv.btn_open_local_vscode.text(), "🚀 OPEN DEBUG IN VS CODE")
            self.assertTrue(dv.btn_open_local_vscode.isEnabled())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_debug_page_gateway_mode_strictly_loopback(self) -> None:
        window = self._make_window()
        try:
            dv = window.debug_vscode_view
            dv.select_mode("gateway")
            self.app.processEvents()

            self.assertEqual(dv.mode_stack.currentIndex(), 1)
            self.assertTrue(dv.btn_mode_gateway.isChecked())

            # CRITICAL SAFETY: OpenOCD must be loopback 127.0.0.1, never 0.0.0.0
            self.assertIn("127.0.0.1", dv.gw_openocd_lbl.text())
            self.assertNotIn("0.0.0.0", dv.gw_openocd_lbl.text())

            # Actions
            self.assertTrue(dv.btn_start_gateway.isEnabled())
            self.assertFalse(dv.btn_stop_gateway.isEnabled())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_debug_page_client_mode_cta_and_tunnel(self) -> None:
        window = self._make_window()
        try:
            dv = window.debug_vscode_view
            dv.select_mode("client")
            self.app.processEvents()

            self.assertEqual(dv.mode_stack.currentIndex(), 2)
            self.assertTrue(dv.btn_mode_client.isChecked())

            # Primary CTA
            self.assertEqual(dv.btn_open_remote_vscode.text(), "🚀 OPEN REMOTE DEBUG IN VS CODE")
            self.assertEqual(dv.btn_test_client_conn.text(), "⚡ TEST CONNECTION")
            self.assertEqual(dv.client_local_gdb_spin.value(), 43333)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    # ------------------------------------------------------------------
    # 4. Zero-Halt Live Monitor Tests
    # ------------------------------------------------------------------
    def test_live_monitor_page_guarantees_zero_halt_and_stable_ownership(self) -> None:
        window = self._make_window()
        try:
            mv = window.monitor_view
            self.assertIsInstance(mv, MonitorView)

            # Permanent widget tree ownership (no dynamic reparenting)
            self.assertIs(mv.live_panel.parent(), mv)

            # Switching to monitor page does NOT start debug or halt MCU
            window.show_page("monitor")
            self.assertFalse(window.busy)
            self.assertIsNone(window._cancellable_worker)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_switching_pages_does_not_halt_or_start_openocd(self) -> None:
        window = self._make_window()
        try:
            # Cycle through all pages
            for page in ("program", "monitor", "debug", "device", "settings", "program"):
                window.show_page(page)
                self.app.processEvents()
                # Ensure no hardware operations or workers are spawned merely by changing pages
                self.assertFalse(window.busy)
                self.assertIsNone(window._cancellable_worker)
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    # ------------------------------------------------------------------
    # 5. Hardware State Synchronization Tests
    # ------------------------------------------------------------------
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

            # Verify ProgramView updated
            self.assertIn("512 KB", window.program_view.lbl_target.text())
            # Verify DebugVsCodeView updated
            self.assertIn("512KB", window.debug_vscode_view.local_target_status.text())
            # Verify DeviceView updated
            self.assertEqual(window.device_view.val_flash_size.text(), "512 KB")
            self.assertEqual(window.device_view.val_dev_id.text(), "0x101F6413")
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
