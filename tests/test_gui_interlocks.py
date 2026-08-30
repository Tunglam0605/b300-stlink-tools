from __future__ import annotations

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.debug_service import DebugState
from b300_core.hardware_session import HardwareSessionManager
from b300_core.metadata import decode_ota_metadata
from b300_core.models import BootVerification, CommandResult, TargetInfo
from b300_core.service import FlashResult
from b300_gui.main_window import MainWindow
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex
from tests.test_core_probe_memory_metadata import make_metadata
from tests.test_gui_smoke import FakeService


class FakeDebugService:
    def __init__(self, state: DebugState = DebugState.STOPPED) -> None:
        self.state = state

    def start(self, *_args, **_kwargs):
        self.state = DebugState.READY
        return self.state

    def mark_connected(self) -> None:
        self.state = DebugState.CONNECTED

    def stop(self) -> None:
        self.state = DebugState.STOPPED


class ServiceWithSession(FakeService):
    def __init__(self, manager) -> None:
        self.session_manager = manager


class GuiHardwareInterlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def prepare_flash_ready(self, window: MainWindow) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        image = write_hex(Path(temp.name), 0x08010000, APPLICATION_VECTOR)
        self.assertTrue(window.load_image_path(image))
        window.apply_target_info(TargetInfo(
            0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True, False
        ))
        self.assertTrue(window.flash_button.isEnabled())

    def test_default_debug_service_reuses_b300_service_session_manager(self) -> None:
        manager = HardwareSessionManager()
        service = ServiceWithSession(manager)
        window = MainWindow(service=service, probe_loader=lambda: ())
        self.assertIs(window.debug_service.session_manager, manager)
        window.close()

    def test_debug_session_disables_flash_factory_probe_changes_and_memory_reads(self) -> None:
        debug = FakeDebugService(DebugState.STOPPED)
        window = MainWindow(
            service=FakeService(), debug_service=debug, probe_loader=lambda: ()
        )
        self.prepare_flash_ready(window)
        self.assertTrue(window.memory_tab.read_button.isEnabled())

        debug.state = DebugState.READY
        window._hardware_activity_changed(True)

        self.assertFalse(window.flash_button.isEnabled())
        self.assertFalse(window.inspect_target_button.isEnabled())
        self.assertFalse(window.probe_combo.isEnabled())
        self.assertFalse(window.factory_provision_button.isEnabled())
        self.assertFalse(window.factory_probe_combo.isEnabled())
        self.assertFalse(window.memory_tab.read_button.isEnabled())
        self.assertFalse(window.memory_tab.metadata_button.isEnabled())
        self.assertFalse(window.support_bundle_action.isEnabled())
        self.assertTrue(window.debug_tab.stop_button.isEnabled())

        debug.state = DebugState.STOPPED
        window._hardware_activity_changed(False)
        self.assertTrue(window.flash_button.isEnabled())
        self.assertTrue(window.memory_tab.read_button.isEnabled())
        self.assertTrue(window.support_bundle_action.isEnabled())
        window.close()

    def test_main_worker_completion_releases_memory_interlock_without_gui_restart(self) -> None:
        window = MainWindow(
            service=FakeService(), debug_service=FakeDebugService(DebugState.STOPPED),
            probe_loader=lambda: (),
        )
        self.assertTrue(window.memory_tab.metadata_button.isEnabled())
        window.busy = True
        window._update_controls()
        self.assertFalse(window.memory_tab.metadata_button.isEnabled())

        def completed(_result) -> None:
            # Matches flash/inspect callbacks: logical busy clears before the
            # QThread.finished signal removes the worker from window._threads.
            window.busy = False
            window._update_controls()
            self.assertFalse(window.memory_tab.metadata_button.isEnabled())

        window._start_worker(lambda _log, _phase, _cancel: "done", completed)
        deadline = time.monotonic() + 1.0
        while window._threads and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(window._threads)
        self.assertFalse(window.busy)
        self.assertTrue(window.memory_tab.metadata_button.isEnabled())
        self.assertTrue(window.memory_tab.read_button.isEnabled())
        window.close()

    def test_flash_completion_stales_metadata_then_allows_immediate_reread(self) -> None:
        class WorkflowService(FakeService):
            def read_metadata(self, probe, event_sink=None, cancel_event=None):
                return decode_ota_metadata(b"\xFF" * 44)

        window = MainWindow(
            service=WorkflowService(),
            debug_service=FakeDebugService(DebugState.STOPPED),
            probe_loader=lambda: (),
        )
        window.memory_tab.show_metadata(decode_ota_metadata(make_metadata(state=1)))
        self.assertEqual(window.memory_tab.metadata_values["State"].text(), "IN_PROGRESS (1)")

        result = FlashResult(
            "succeeded",
            CommandResult(("openocd",), 0, "ok"),
            None,
            None,
            BootVerification(0x08010000, 0, True, "Application running"),
        )
        window.busy = True
        window._update_controls()
        window._start_worker(lambda _log, _phase, _cancel: result, window._flash_finished)

        deadline = time.monotonic() + 1.0
        while window._threads and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(window.busy)
        self.assertFalse(window._threads)
        self.assertEqual(window.memory_tab.metadata_values["Classification"].text(), "STALE")
        self.assertTrue(window.memory_tab.metadata_button.isEnabled())

        window.memory_tab.read_metadata()
        deadline = time.monotonic() + 1.0
        while window.memory_tab.has_active_operation and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertEqual(window.memory_tab.metadata_values["Classification"].text(), "ERASED")
        self.assertEqual(window.memory_tab.metadata_values["State"].text(), "—")
        self.assertTrue(window.memory_tab.metadata_button.isEnabled())
        window.close()

    def test_gui_support_bundle_runs_in_main_read_only_worker_and_releases_interlock(self) -> None:
        window = MainWindow(
            service=FakeService(), debug_service=FakeDebugService(DebugState.STOPPED),
            probe_loader=lambda: (),
        )
        snapshot = {
            "diagnostics": {"conclusion": "READY_FOR_APPLICATION_FLASH"},
            "application_health": {"lifecycle": "BOOTABLE"},
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "support.zip"
            result = SimpleNamespace(
                path=destination.resolve(), sha256="B" * 64, size_bytes=1776, snapshot=snapshot
            )
            with mock.patch(
                "b300_gui.main_window.QFileDialog.getSaveFileName",
                return_value=(str(destination), "ZIP archive (*.zip)"),
            ), mock.patch(
                "b300_gui.main_window.collect_support_snapshot", return_value=snapshot
            ) as collect, mock.patch(
                "b300_gui.main_window.write_support_bundle", return_value=result
            ) as write:
                window.export_support_bundle()
                self.assertTrue(window.busy)
                self.assertFalse(window.support_bundle_action.isEnabled())
                self.assertFalse(window.memory_tab.read_button.isEnabled())
                deadline = time.monotonic() + 1.0
                while window._threads and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.app.processEvents()

        self.assertFalse(window.busy)
        self.assertFalse(window._threads)
        self.assertTrue(window.support_bundle_action.isEnabled())
        self.assertTrue(window.memory_tab.read_button.isEnabled())
        self.assertIn("Support bundle đã tạo", window.status_banner.text())
        collect.assert_called_once()
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], destination)
        self.assertFalse(write.call_args.kwargs["force"])
        window.close()

    def test_gui_support_bundle_refuses_to_start_while_hardware_busy(self) -> None:
        window = MainWindow(
            service=FakeService(), debug_service=FakeDebugService(DebugState.STOPPED),
            probe_loader=lambda: (),
        )
        window.busy = True
        window._update_controls()
        with mock.patch("b300_gui.main_window.QFileDialog.getSaveFileName") as dialog:
            window.export_support_bundle()
        dialog.assert_not_called()
        self.assertIn("ST-Link đang bận", window.status_banner.text())
        window.busy = False
        window._update_controls()
        window.close()

    def test_main_or_memory_activity_disables_starting_debug(self) -> None:
        debug = FakeDebugService(DebugState.STOPPED)
        window = MainWindow(
            service=FakeService(), debug_service=debug, probe_loader=lambda: ()
        )
        self.assertTrue(window.debug_tab.start_button.isEnabled())

        window.busy = True
        window._update_controls()
        self.assertFalse(window.debug_tab.start_button.isEnabled())
        self.assertFalse(window.memory_tab.read_button.isEnabled())

        window.busy = False
        window.memory_tab._threads.append(object())
        window._hardware_activity_changed(True)
        self.assertFalse(window.debug_tab.start_button.isEnabled())
        self.assertFalse(window.flash_button.isEnabled())

        window.memory_tab._threads.clear()
        window._hardware_activity_changed(False)
        self.assertTrue(window.debug_tab.start_button.isEnabled())
        window.close()


if __name__ == "__main__":
    unittest.main()
