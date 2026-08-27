from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from b300_gui.main_window import MainWindow
from b300_core.hex_image import inspect_image
from b300_core.models import FlashPhaseEvent, ProbeInfo, TargetInfo
from b300_gui.workers import FunctionWorker
from b300_core.policy import build_flash_plan
from tests.test_core_hex_policy import write_hex


class FakeService:
    def doctor(self):
        return True, "openocd"

    def inspect_image(self, path):
        return inspect_image(path)

    def plan(self, image, probe, target):
        return build_flash_plan(image, probe, target)

    def flash_command(self, plan):
        return ["openocd", "-c", "flash erase_sector 0 3 7"]

    def marker_command(self, probe):
        return ["openocd", "-c", "mww 0x40002860 0x53544C4B"]

    def reset_command(self, probe):
        return ["openocd", "-c", "reset run"]

    def inspect_target(self, probe, event_sink=None, cancel_event=None):
        if event_sink:
            event_sink("read-only target inspection")
        return TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected")


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.processEvents()
        cls.app.shutdown()
        cls.app = None

    def test_smoke_entry_point_finalizes_qapplication(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from b300_gui.__main__ import main; "
                "from PySide6.QtWidgets import QApplication; "
                "assert main(['--smoke-test']) == 0; "
                "assert QApplication.instance() is None",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_main_window_starts_safe_and_has_no_com_selector(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        window.show()
        self.app.processEvents()
        self.assertFalse(window.windowIcon().isNull())
        brand_logo = window.findChild(QLabel, "brandLogo")
        self.assertIsNotNone(brand_logo)
        self.assertFalse(brand_logo.pixmap().isNull())
        self.assertFalse(window.flash_button.isEnabled())
        disabled_pixel = window.flash_button.grab().toImage().pixelColor(
            10, window.flash_button.height() // 2
        )
        self.assertGreater(
            disabled_pixel.blue(), disabled_pixel.red(),
            "Disabled Flash must look neutral, not safety-orange/actionable",
        )
        self.assertEqual(window.probe_combo.count(), 1)
        self.assertIsNone(window.findChild(QWidget, "comSelector"))
        self.assertIn("Sector 3–7", window.flash_plan_label.text())
        self.assertEqual(window.plan_table.verticalScrollBar().maximum(), 0)
        self.assertEqual(window.log_view.horizontalScrollBar().value(), 0)
        self.assertEqual(window.about_action.text(), "Giới thiệu")
        self.assertIn("Core v0.1.0", window.log_view.toPlainText())
        window.close()

    def test_valid_image_enables_dry_run_without_hardware_write(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, b"\x01")
            self.assertTrue(window.load_image_path(image))
            self.assertFalse(window.flash_button.isEnabled())
            window.apply_target_info(TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected"))
            self.assertTrue(window.flash_button.isEnabled())
            window.show_dry_run()
        self.assertIn("DRY-RUN", window.log_view.toPlainText())
        self.assertIn("flash erase_sector 0 3 7", window.log_view.toPlainText())
        window.close()

    def test_target_worker_survives_until_read_only_inspection_finishes(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        window.inspect_target()
        deadline = time.monotonic() + 1.0
        while window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(window.busy)
        self.assertTrue(window.target_ready)
        self.assertIn("0x101F6413", window.target_summary.text())
        while window._threads and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_multiple_probes_require_explicit_serial_selection(self) -> None:
        probes = (
            ProbeInfo("AAA111", "ST-Link A", "test"),
            ProbeInfo("BBB222", "ST-Link B", "test"),
        )
        window = MainWindow(service=FakeService(), probe_loader=lambda: probes)
        self.assertIsNone(window.probe_combo.currentData())
        self.assertFalse(window.inspect_target_button.isEnabled())
        self.assertNotIn("Auto-select", window.probe_combo.currentText())

        window.probe_combo.setCurrentIndex(1)
        self.assertEqual(window.probe_combo.currentData(), "AAA111")
        self.assertTrue(window.inspect_target_button.isEnabled())
        window.close()

    def test_flash_phase_updates_determinate_progress(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        window._flash_phase_changed(
            FlashPhaseEvent("verifying", 60, "Verifying Application")
        )
        self.assertEqual((window.progress.minimum(), window.progress.maximum()), (0, 100))
        self.assertEqual(window.progress.value(), 60)
        self.assertIn("Verifying", window.progress.format())
        window.close()

    def test_close_is_refused_while_hardware_operation_is_active(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        window.busy = True
        event = QCloseEvent()
        window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertIn("đang chạy", window.status_banner.text().lower())
        window.busy = False
        window.close()

    def test_function_worker_cancel_sets_cooperative_event(self) -> None:
        started = threading.Event()
        observed = []

        def operation(log, phase, cancel_event):
            started.set()
            cancel_event.wait(timeout=1)
            return cancel_event.is_set()

        worker = FunctionWorker(operation)
        worker.completed.connect(observed.append)
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        worker.cancel()
        self.assertTrue(worker.wait(2000))
        self.app.processEvents()
        self.assertEqual(observed, [True])

    def test_structured_worker_failure_renders_phase_cause_and_next_action(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        window.busy = True
        window._operation_failed(SimpleNamespace(
            phase="target_check",
            message="Unsupported target",
            next_action="Select the F407 board",
            traceback="trace detail",
        ))
        status = window.status_banner.text()
        self.assertIn("target_check", status)
        self.assertIn("Unsupported target", status)
        self.assertIn("Select the F407 board", status)
        window.close()


if __name__ == "__main__":
    unittest.main()
