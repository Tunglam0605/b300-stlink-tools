"""Unit tests for B300 GUI Modern Frontend Redesign."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QScrollArea

from b300_core.models import ImageInfo, ProbeInfo, TargetInfo
from b300_gui.theme import DARK_PALETTE, LIGHT_PALETTE, ThemeManager, generate_stylesheet
from b300_gui.widgets.header_bar import HeaderBar
from b300_gui.widgets.compact_sidebar import CompactSidebar
from b300_gui.widgets.pipeline_stepper import PipelineStepper
from b300_gui.widgets.pass_fail_banner import PassFailBanner
from b300_gui.widgets.memory_map_widget import MemoryMapWidget
from b300_gui.widgets.stats_row import StatCard, StatsRow
from b300_gui.views.operator_view import OperatorView
from b300_gui.views.rnd_flash_view import RndFlashView
from b300_gui.main_window import MainWindow


class GuiRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_theme_manager_palette_and_toggle(self) -> None:
        mgr = ThemeManager.instance()
        mgr.set_theme("dark")
        self.assertEqual(mgr.current_mode, "dark")
        self.assertTrue(mgr.is_dark)
        self.assertEqual(mgr.palette, DARK_PALETTE)

        qss_dark = generate_stylesheet(DARK_PALETTE)
        self.assertIn(DARK_PALETTE.canvas, qss_dark)
        self.assertIn(DARK_PALETTE.primary, qss_dark)
        self.assertIn("QPushButton:hover", qss_dark)
        self.assertIn("QPushButton:pressed", qss_dark)

        # Toggle to light
        new_mode = mgr.toggle_theme()
        self.assertEqual(new_mode, "light")
        self.assertFalse(mgr.is_dark)
        self.assertEqual(mgr.palette, LIGHT_PALETTE)

        qss_light = generate_stylesheet(LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.canvas, qss_light)

        # Revert to dark
        mgr.set_theme("dark")
        self.assertEqual(mgr.current_mode, "dark")

    def test_dark_theme_uses_reference_cyan_palette_and_visible_focus(self) -> None:
        self.assertEqual(DARK_PALETTE.canvas, "#101920")
        self.assertEqual(DARK_PALETTE.text, "#F8FAFC")
        self.assertEqual(DARK_PALETTE.text_secondary, "#AEC2D7")
        self.assertNotEqual(DARK_PALETTE.primary, DARK_PALETTE.text)
        self.assertNotEqual(DARK_PALETTE.accent_cyan, DARK_PALETTE.text)

        qss = generate_stylesheet(DARK_PALETTE)
        self.assertIn("QPushButton:focus", qss)
        self.assertIn("QPushButton#primaryButton,", qss)
        self.assertIn("QPushButton#operatorFlashBtn:hover", qss)
        self.assertIn("QPushButton#primaryButton:disabled", qss)
        self.assertIn("color: #AEC2D7;", qss)

    def test_cockpit_stats_row_keeps_four_operational_cards(self) -> None:
        stats = StatsRow()
        self.assertEqual(stats.layout().count(), 4)
        self.assertEqual(stats.probe_card.title_label.text(), "MẠCH NẠP ST-LINK")
        self.assertEqual(stats.target_card.title_label.text(), "CHIP ĐÍCH (MCU)")
        self.assertEqual(stats.flash_card.title_label.text(), "BỘ NHỚ FLASH")
        self.assertEqual(stats.status_card.title_label.text(), "TRẠNG THÁI HỆ THỐNG")
        stats.update_probe("ST-Link V3", "SN: ABC123")
        stats.update_status("Sẵn sàng", "OpenOCD Loopback OK")
        self.assertEqual(stats.probe_card.value_label.text(), "ST-Link V3")
        self.assertEqual(stats.status_card.subtitle_label.text(), "OpenOCD Loopback OK")

    def test_stats_row_uses_two_columns_at_280_pixels_per_card(self) -> None:
        ThemeManager.instance().set_theme("dark")
        ThemeManager.instance().apply()
        stats = StatsRow()
        two_column_width = 2 * 280 + stats.layout().horizontalSpacing()

        stats.resize(two_column_width - 1, 240)
        stats.show()
        self.app.processEvents()

        self.assertEqual(stats._columns, 1)
        stats.resize(two_column_width, 240)
        self.app.processEvents()

        self.assertEqual(stats._columns, 2)
        stats.close()

    def test_stats_row_uses_four_columns_at_320_pixels_per_card(self) -> None:
        ThemeManager.instance().set_theme("dark")
        ThemeManager.instance().apply()
        stats = StatsRow()
        four_column_width = 4 * 320 + 3 * stats.layout().horizontalSpacing()

        stats.resize(four_column_width - 1, 240)
        stats.show()
        self.app.processEvents()

        self.assertEqual(stats._columns, 2)
        stats.resize(four_column_width, 240)
        self.app.processEvents()

        self.assertEqual(stats._columns, 4)
        stats.close()

    def test_stats_row_breakpoints_ignore_platform_card_size_hints(self) -> None:
        ThemeManager.instance().set_theme("dark")
        ThemeManager.instance().apply()
        stats = StatsRow()
        self.addCleanup(stats.close)

        with patch.object(
            StatCard,
            "minimumSizeHint",
            return_value=QSize(400, 58),
        ):
            stats.resize(568, 240)
            stats.show()
            self.app.processEvents()
            self.assertEqual(stats._columns, 2)

            stats.resize(1304, 240)
            self.app.processEvents()
            self.assertEqual(stats._columns, 4)

    def test_header_bar_mode_switching(self) -> None:
        header = HeaderBar()
        self.assertEqual(header.brand_subtitle.text(), "STM32 • Nạp • Gỡ lỗi • Giám sát")
        self.assertEqual(header.lbl_mcu_title.text(), "MCU đích")
        self.assertIn("ST-Link đang kết nối", header.probe_combo.toolTip())
        self.assertEqual(header.current_mode, "rnd")
        received_modes = []
        header.mode_changed.connect(received_modes.append)

        header.set_mode("operator")
        self.assertEqual(header.current_mode, "operator")
        self.assertEqual(received_modes, ["operator"])

        header.set_mode("rnd")
        self.assertEqual(header.current_mode, "rnd")
        self.assertEqual(received_modes, ["operator", "rnd"])

    def test_header_bar_probes(self) -> None:
        header = HeaderBar()
        probes = [
            ProbeInfo(serial="066EFF545053717867204928", name="ST-Link V2", source="usb"),
            ProbeInfo(serial="002E001B4D31500220383734", name="ST-Link V3", source="usb"),
        ]
        header.set_probes(probes, selected_serial="002E001B4D31500220383734")
        self.assertEqual(header.probe_combo.count(), 2)
        self.assertEqual(header.probe_combo.currentIndex(), 1)

    def test_compact_sidebar_mode_filtering_and_collapse(self) -> None:
        sidebar = CompactSidebar()
        sidebar.show()
        sidebar.set_mode("operator")
        self.assertFalse(sidebar._buttons["op_flash"].isHidden())
        self.assertTrue(sidebar._buttons["rnd_flash"].isHidden())

        sidebar.set_mode("rnd")
        self.assertTrue(sidebar._buttons["op_flash"].isHidden())
        self.assertFalse(sidebar._buttons["rnd_flash"].isHidden())
        self.assertFalse(sidebar._buttons["rnd_memory"].isHidden())

        # Test collapse / expand toggle
        self.assertEqual(sidebar.width(), 64)
        sidebar.toggle_collapse()
        self.assertEqual(sidebar.width(), 200)
        sidebar.toggle_collapse()
        self.assertEqual(sidebar.width(), 64)

    def test_pipeline_stepper_states(self) -> None:
        stepper = PipelineStepper()
        stepper.set_step_state(0, "active", "Scanning USB...")
        self.assertEqual(stepper._step_widgets[0]._state, "active")

        stepper.map_phase("program", message="Writing 0x08010000...")
        # Previous steps should be success
        self.assertEqual(stepper._step_widgets[0]._state, "success")
        self.assertEqual(stepper._step_widgets[1]._state, "success")
        self.assertEqual(stepper._step_widgets[2]._state, "success")
        self.assertEqual(stepper._step_widgets[3]._state, "active")

        stepper.reset_steps()
        self.assertEqual(stepper._step_widgets[0]._state, "idle")

    def test_pass_fail_banner(self) -> None:
        banner = PassFailBanner()
        self.assertFalse(banner.isVisible())

        banner.show_pass("FLASH OK", "STLM Verified", duration_sec=3.2)
        self.assertTrue(banner.isVisible())
        self.assertIn("FLASH OK", banner.title_label.text())
        self.assertIn("3.2s", banner.detail_label.text())

        banner.show_fail("FLASH ERROR", "Verify failed", next_action="Check power")
        self.assertTrue(banner.isVisible())
        self.assertIn("FLASH ERROR", banner.title_label.text())
        self.assertIn("Check power", banner.detail_label.text())

    def test_memory_map_widget(self) -> None:
        widget = MemoryMapWidget()
        self.assertIsNotNone(widget.canvas)
        widget.set_image_span(0x08010000, 128 * 1024)
        self.assertEqual(widget.canvas._image_span, (0x08010000, 128 * 1024))
        # Trigger paint without crash
        widget.canvas.repaint()

    def test_operator_view_probe_and_action_state(self) -> None:
        op_view = OperatorView()
        self.assertFalse(op_view.flash_btn.isEnabled())

        probes = [ProbeInfo(serial="V2SERIAL", name="ST-Link V2", source="usb")]
        op_view.set_probes(probes)
        self.assertIn("SẴN SÀNG", op_view.probe_pill.text())
        # Still disabled until valid image is selected
        self.assertFalse(op_view.flash_btn.isEnabled())

    def test_operator_view_never_shows_a_horizontal_scrollbar(self) -> None:
        op_view = OperatorView()
        scroll = op_view.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        self.assertEqual(
            scroll.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )

    def test_operator_view_uses_core_target_fields_and_requires_safe_plan(self) -> None:
        op_view = OperatorView()
        op_view.set_probes([
            ProbeInfo(serial="V2SERIAL", name="ST-Link V2", source="usb"),
        ])
        op_view._current_image = MagicMock()
        target = TargetInfo(
            0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True,
        )

        op_view.set_target_info(target)
        op_view.set_flash_ready(False)
        self.assertFalse(op_view.flash_btn.isEnabled())
        self.assertFalse(op_view.dry_run_btn.isEnabled())

        op_view.set_flash_ready(True)
        self.assertTrue(op_view.flash_btn.isEnabled())
        self.assertTrue(op_view.dry_run_btn.isEnabled())
        self.assertIn("512 KiB", op_view.probe_status_sub.text())
        self.assertIn("S0-S2 protected", op_view.probe_status_sub.text())

    def test_operator_flash_uses_existing_confirmation_gate(self) -> None:
        mock_service = MagicMock()
        mock_service.doctor.return_value = (True, "openocd")
        window = MainWindow(
            service=mock_service,
            probe_loader=lambda: (),
            automatic_updates=False,
        )
        window.image_info = SimpleNamespace(path=Path("C:/firmware.hex"))
        window.target_ready = True
        window.flash_plan = object()

        with patch.object(window, "confirm_flash") as confirm:
            window._on_operator_flash_requested(Path("C:/firmware.hex"), False)

        confirm.assert_called_once_with()
        window.close()

    def test_main_window_integration(self) -> None:
        mock_service = MagicMock()
        mock_service.doctor.return_value = (True, "openocd")

        window = MainWindow(
            service=mock_service,
            probe_loader=lambda: (),
            automatic_updates=False,
        )
        self.assertEqual(window.header_bar.current_mode, "rnd")
        self.assertEqual(window.main_stack.currentWidget(), window.rnd_workspace)

        # Switch to Operator mode
        window.header_bar.set_mode("operator")
        self.assertEqual(window.header_bar.current_mode, "operator")
        self.assertEqual(window.main_stack.currentWidget(), window.operator_view)

        # Switch back to R&D mode
        # Test theme toggle from MainWindow
        ThemeManager.instance().set_theme("dark")
        self.assertEqual(ThemeManager.instance().current_mode, "dark")
        self.assertEqual(window.header_bar.theme_btn.text(), "Giao diện: Tối")

        # Toggle to light
        window._on_toggle_theme()
        self.assertEqual(ThemeManager.instance().current_mode, "light")
        self.assertEqual(window.header_bar.theme_btn.text(), "Giao diện: Sáng")

        # Toggle back to dark
        window._on_toggle_theme()
        self.assertEqual(ThemeManager.instance().current_mode, "dark")
        self.assertEqual(window.header_bar.theme_btn.text(), "Giao diện: Tối")

        window.close()


if __name__ == "__main__":
    unittest.main()
