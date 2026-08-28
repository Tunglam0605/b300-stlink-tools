from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.debug_service import DebugState
from b300_core.hardware_session import HardwareSessionManager
from b300_core.models import TargetInfo
from b300_gui.main_window import MainWindow
from tests.test_core_hex_policy import write_hex
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
        image = write_hex(Path(temp.name), 0x08010000, b"\x01")
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
        self.assertFalse(window.factory_inspect_button.isEnabled())
        self.assertFalse(window.factory_probe_combo.isEnabled())
        self.assertFalse(window.memory_tab.read_button.isEnabled())
        self.assertFalse(window.memory_tab.metadata_button.isEnabled())
        self.assertTrue(window.debug_tab.stop_button.isEnabled())

        debug.state = DebugState.STOPPED
        window._hardware_activity_changed(False)
        self.assertTrue(window.flash_button.isEnabled())
        self.assertTrue(window.memory_tab.read_button.isEnabled())
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
