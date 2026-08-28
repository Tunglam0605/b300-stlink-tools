from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.debug_service import DebugConfig, DebugState
from b300_core.models import ProbeRef
from b300_gui.debug_tab import DebugTab


class FakeDebugService:
    def __init__(self, state: DebugState = DebugState.READY) -> None:
        self.state = state
        self.connected = False

    def start(self, config, event_sink=None):
        self.state = DebugState.READY
        if event_sink:
            event_sink("OpenOCD ready")
        return self.state

    def mark_connected(self) -> None:
        self.connected = True
        self.state = DebugState.CONNECTED

    def stop(self) -> None:
        self.state = DebugState.STOPPED


class FakeGdbBackend:
    def __init__(self, connect_error: Exception | None = None) -> None:
        self.commands = []
        self.connect_error = connect_error

    def start(self) -> None:
        self.commands.append("start")

    def connect(self, host: str, port: int):
        self.commands.append(("connect", host, port))
        if self.connect_error is not None:
            raise self.connect_error
        return object()

    def load_symbols(self, path: Path):
        self.commands.append(("symbols", Path(path)))
        return object()

    def interrupt(self):
        self.commands.append("halt")
        return object()

    def continue_execution(self):
        self.commands.append("continue")
        return object()

    def reset_halt(self):
        self.commands.append("reset_halt")
        return object()

    def stop(self) -> None:
        self.commands.append("stop")


class DebugTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for Qt worker completion")

    def test_debug_surface_has_symbols_and_basic_target_controls(self) -> None:
        service = FakeDebugService(DebugState.STOPPED)
        tab = DebugTab(service, lambda: ProbeRef("DEBUG123"), gdb_backend=FakeGdbBackend())
        self.assertEqual(tab.gdb_port.text(), "3333")
        self.assertIn("ELF/AXF", tab.symbol_browse_button.text())
        self.assertFalse(tab.halt_button.isEnabled())
        self.assertFalse(tab.continue_button.isEnabled())
        self.assertFalse(tab.reset_button.isEnabled())
        tab.close()

    def test_connect_loads_symbols_then_marks_verified_connection(self) -> None:
        service = FakeDebugService(DebugState.READY)
        gdb = FakeGdbBackend()
        tab = DebugTab(service, lambda: ProbeRef("DEBUG123"), gdb_backend=gdb)
        tab._active_config = DebugConfig(ProbeRef("DEBUG123"), "127.0.0.1", 3333)
        with TemporaryDirectory() as directory:
            symbols = Path(directory) / "application.elf"
            symbols.write_bytes(b"ELF")
            tab.symbol_path.setText(str(symbols))
            tab.connect_gdb()
            self.wait_until(lambda: tab._worker is None)

        self.assertEqual(gdb.commands[:3], [
            "start",
            ("symbols", symbols),
            ("connect", "127.0.0.1", 3333),
        ])
        self.assertTrue(service.connected)
        self.assertEqual(service.state, DebugState.CONNECTED)
        self.assertTrue(tab.halt_button.isEnabled())
        self.assertTrue(tab.continue_button.isEnabled())
        self.assertTrue(tab.reset_button.isEnabled())
        self.assertIn("GDB đã kết nối", tab.status_label.text())
        tab.stop_debug()
        tab.close()

    def test_gdb_connect_failure_keeps_openocd_session_active_and_retryable(self) -> None:
        service = FakeDebugService(DebugState.READY)
        gdb = FakeGdbBackend(RuntimeError("remote rejected"))
        tab = DebugTab(service, lambda: ProbeRef("DEBUG123"), gdb_backend=gdb)
        tab._active_config = DebugConfig(ProbeRef("DEBUG123"), "127.0.0.1", 3333)
        tab.connect_gdb()
        self.wait_until(lambda: tab._worker is None)
        self.assertEqual(service.state, DebugState.READY)
        self.assertFalse(tab.start_button.isEnabled())
        self.assertTrue(tab.connect_button.isEnabled())
        self.assertTrue(tab.stop_button.isEnabled())
        self.assertIn("OpenOCD vẫn đang chạy", tab.status_label.text())
        tab.stop_debug()
        tab.close()

    def test_openocd_failure_watchdog_releases_gui_interlock_and_gdb(self) -> None:
        service = FakeDebugService(DebugState.READY)
        gdb = FakeGdbBackend()
        tab = DebugTab(service, lambda: ProbeRef("DEBUG123"), gdb_backend=gdb)
        tab._active_config = DebugConfig(ProbeRef("DEBUG123"), "127.0.0.1", 3333)
        tab._watchdog.start()
        service.state = DebugState.FAILED
        tab._poll_debug_service()
        self.assertFalse(tab._watchdog.isActive())
        self.assertIn("stop", gdb.commands)
        self.assertIsNone(tab._active_config)
        self.assertTrue(tab.start_button.isEnabled())
        self.assertFalse(tab.stop_button.isEnabled())
        self.assertIn("dừng bất ngờ", tab.status_label.text())
        tab.close()

    def test_external_interlock_blocks_start_but_not_offline_symbol_selection(self) -> None:
        service = FakeDebugService(DebugState.STOPPED)
        tab = DebugTab(service, lambda: ProbeRef("DEBUG123"), gdb_backend=FakeGdbBackend())
        self.assertTrue(tab.start_button.isEnabled())
        tab.set_external_blocked(True)
        self.assertFalse(tab.start_button.isEnabled())
        self.assertTrue(tab.symbol_path.isEnabled())
        self.assertTrue(tab.symbol_browse_button.isEnabled())
        tab.set_external_blocked(False)
        self.assertTrue(tab.start_button.isEnabled())
        tab.close()


if __name__ == "__main__":
    unittest.main()
