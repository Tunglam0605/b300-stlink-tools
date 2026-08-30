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
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget

from b300_gui.main_window import MainWindow
from b300_core.hex_image import inspect_image
from b300_core.models import FlashPhaseEvent, ProbeInfo, TargetInfo
from b300_core.service import FactoryResult
from b300_gui.workers import FunctionWorker
from b300_core.policy import build_flash_plan
from b300_version import __version__
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex


class FakeService:
    def doctor(self):
        return True, "openocd"

    def inspect_image(self, path):
        return inspect_image(path)

    def plan(self, image, probe, target):
        return build_flash_plan(image, probe, target)

    def flash_command(self, plan):
        return ["openocd", "-c", "flash erase_sector 0 3 7"]

    def reset_command(self, probe):
        return ["openocd", "-c", "reset run"]

    def inspect_target(self, probe, event_sink=None, cancel_event=None):
        if event_sink:
            event_sink("read-only target inspection")
        return TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True)


class OneClickFactoryService(FakeService):
    def __init__(self, target=None) -> None:
        self.calls = []
        self.target = target or TargetInfo(
            0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True, False
        )

    def inspect_target(self, probe, event_sink=None, cancel_event=None):
        self.calls.append(("inspect", probe.serial))
        if event_sink:
            event_sink("one-click factory preflight")
        return self.target

    def factory_plan(self, image, probe, target):
        self.calls.append(("plan", probe.serial))
        if target.readout_protected:
            raise ValueError("RDP/security is enabled")
        return SimpleNamespace(image=image, probe=probe, target=target)

    def provision_bootloader(self, plan, event_sink=None, phase_sink=None):
        self.calls.append(("provision", plan.probe.serial))
        if phase_sink:
            phase_sink(FlashPhaseEvent("programming", 50, "Programming trusted Bootloader"))
            phase_sink(FlashPhaseEvent("succeeded", 100, "Factory complete"))
        return FactoryResult("succeeded", final_target=plan.target)


class MissingOpenOcdService(FakeService):
    def __init__(self) -> None:
        self.executable = "openocd"

    def doctor(self):
        return Path(self.executable).name == "openocd.exe", self.executable


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
        self.assertLessEqual(window.minimumHeight(), 460)
        self.assertLessEqual(window.minimumWidth(), 760)
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
        self.assertIn(
            "Debug", [window.tabs.tabText(index) for index in range(window.tabs.count())]
        )
        self.assertFalse(window.debug_tab.stop_button.isEnabled())
        self.assertIn("Stable", window.update_channel_label.text())
        self.assertIn("Sector 3–7", window.flash_plan_label.text())
        self.assertEqual(window.plan_table.verticalScrollBar().maximum(), 0)
        self.assertEqual(window.log_view.horizontalScrollBar().value(), 0)
        self.assertEqual(window.log_view.document().maximumBlockCount(), 10000)
        self.assertEqual(window.debug_tab.log_view.document().maximumBlockCount(), 5000)
        self.assertEqual(window.about_action.text(), "Giới thiệu")
        self.assertIn("Core v%s" % __version__, window.log_view.toPlainText())
        window.close()

    def test_valid_image_enables_dry_run_without_hardware_write(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            self.assertTrue(window.load_image_path(image))
            self.assertFalse(window.flash_button.isEnabled())
            window.apply_target_info(TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True))
            self.assertTrue(window.flash_button.isEnabled())
            window.show_dry_run()
        self.assertIn("DRY-RUN", window.log_view.toPlainText())
        self.assertIn("flash erase_sector 0 3 7", window.log_view.toPlainText())
        self.assertIn("reset run", window.log_view.toPlainText())
        self.assertNotIn("marker", window.log_view.toPlainText().lower())
        self.assertNotIn("53544C4B", window.log_view.toPlainText())
        window.close()

    def test_missing_openocd_shows_accessible_offline_setup_action(self) -> None:
        window = MainWindow(service=MissingOpenOcdService(), probe_loader=lambda: ())
        self.assertFalse(window.setup_button.isHidden())
        self.assertTrue(window.setup_button.isEnabled())
        self.assertEqual(window.setup_button.text(), "Thiết lập môi trường")
        self.assertIn("offline", window.setup_button.accessibleDescription().lower())
        window.close()

    def test_offline_setup_button_installs_then_rechecks_environment(self) -> None:
        service = MissingOpenOcdService()
        window = MainWindow(
            service=service,
            probe_loader=lambda: (),
            setup_bundle_provider=lambda: Path("offline-bundle.zip"),
            setup_installer=lambda bundle: Path("installed/openocd.exe"),
        )
        with mock.patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            window.setup_button.click()
        deadline = time.monotonic() + 1.0
        while window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(Path(service.executable), Path("installed/openocd.exe"))
        self.assertTrue(window.setup_button.isHidden())
        self.assertIn("OpenOCD sẵn sàng", window.status_banner.text())
        while window._threads and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        window.close()

    def test_offline_setup_completion_does_not_discover_or_touch_probe(self) -> None:
        service = MissingOpenOcdService()
        window = MainWindow(service=service, probe_loader=lambda: ())

        def forbidden_probe_discovery():
            raise AssertionError("setup completion must not discover ST-Link probes")

        window.probe_loader = forbidden_probe_discovery
        window.busy = True
        window._offline_setup_finished(Path("installed/openocd.exe"))
        self.assertFalse(window.busy)
        self.assertTrue(window.setup_button.isHidden())
        self.assertIn("OpenOCD sẵn sàng", window.status_banner.text())
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


    def test_factory_tab_is_one_click_and_runs_preflight_before_provision(self) -> None:
        probes = (ProbeInfo("FACTORY123", "ST-Link Factory", "test"),)
        service = OneClickFactoryService()
        window = MainWindow(service=service, probe_loader=lambda: probes)
        tab_names = [window.tabs.tabText(index) for index in range(window.tabs.count())]
        self.assertIn("Nạp firmware", tab_names)
        self.assertIsNotNone(window.factory_trusted)
        self.assertEqual(window.factory_profile_combo.count(), 1)
        self.assertEqual(window.factory_profile_combo.currentData(), "b300-f407ze-com3-v00060500")
        profile_text = window.factory_artifact_label.text()
        for token in ("085E44E8", "COM3", "USART1", "230400", "PB6", "PB7", "PC13",
                      "DMA2 Stream5 Channel 4", "0x00030000", "0x0800C000", "0x08010000"):
            self.assertIn(token, profile_text)
        self.assertIn("cổng logic", profile_text)
        self.assertFalse(hasattr(window, "factory_import_button"))
        self.assertFalse(hasattr(window, "factory_custom_hex_button"))
        self.assertEqual(window.factory_probe_combo.currentData(), "FACTORY123")
        self.assertTrue(window.factory_provision_button.isEnabled())
        self.assertFalse(hasattr(window, "factory_ack"))
        self.assertFalse(hasattr(window, "factory_inspect_button"))
        self.assertFalse(hasattr(window, "factory_dry_run_button"))

        window.factory_provision_button.click()
        deadline = time.monotonic() + 2.0
        while (window.busy or window._threads) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

        self.assertEqual([name for name, _serial in service.calls], ["inspect", "plan", "provision"])
        self.assertTrue(all(serial == "FACTORY123" for _name, serial in service.calls))
        self.assertEqual(window.factory_progress.format(), "Factory OK")
        self.assertIn("PRE-FLIGHT OK", window.factory_log_view.toPlainText())
        window.close()

    def test_factory_one_click_blocks_when_wrp_is_not_reported(self) -> None:
        probes = (ProbeInfo("FACTORY123", "ST-Link Factory", "test"),)
        service = OneClickFactoryService(TargetInfo(
            0x101F6413, 512, 3.09, "Protection status not reported", (), False, False
        ))
        window = MainWindow(service=service, probe_loader=lambda: probes)
        self.assertTrue(window.factory_provision_button.isEnabled())

        window.factory_provision_button.click()
        deadline = time.monotonic() + 2.0
        while (window.busy or window._threads) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

        self.assertEqual([name for name, _serial in service.calls], ["inspect"])
        self.assertIn("preflight", window.status_banner.text().lower())
        self.assertIn("write-protection", window.factory_log_view.toPlainText())
        window.close()

    def test_factory_one_click_blocks_rdp_before_provisioning(self) -> None:
        probes = (ProbeInfo("FACTORY123", "ST-Link Factory", "test"),)
        service = OneClickFactoryService(TargetInfo(
            0x101F6413, 512, 3.09, "Sector 0-2 protected", (0, 1, 2), True, True
        ))
        window = MainWindow(service=service, probe_loader=lambda: probes)
        window.factory_provision_button.click()
        deadline = time.monotonic() + 2.0
        while (window.busy or window._threads) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

        self.assertEqual([name for name, _serial in service.calls], ["inspect", "plan"])
        self.assertNotIn("provision", [name for name, _serial in service.calls])
        self.assertIn("RDP", window.status_banner.text())
        window.close()



if __name__ == "__main__":
    unittest.main()
