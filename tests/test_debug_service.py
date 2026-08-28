from __future__ import annotations

import unittest
import time
import threading

from b300_core.debug_service import DebugConfig, DebugService, DebugState
from b300_core.hardware_session import HardwareMode, HardwareSessionManager
from b300_core.models import ProbeRef


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.returncode = None
        self.stdout = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class DebugServiceTests(unittest.TestCase):
    def test_start_holds_debug_session_until_stop(self) -> None:
        commands = []
        process = FakeProcess()
        manager = HardwareSessionManager()
        service = DebugService(
            executable="openocd",
            session_manager=manager,
            process_factory=lambda command, **kwargs: commands.append(command) or process,
            port_waiter=lambda host, port, timeout: True,
        )

        state = service.start(DebugConfig(ProbeRef("DEBUG123")))

        self.assertEqual(state, DebugState.READY)
        self.assertEqual(manager.snapshot().mode, HardwareMode.DEBUGGING)
        self.assertIn("gdb port 3333", commands[0])
        self.assertIn("telnet port disabled", commands[0])
        self.assertIn("tcl port disabled", commands[0])
        rendered = " ".join(commands[0]).lower()
        self.assertNotIn("erase_sector", rendered)
        self.assertNotIn("program {", rendered)

        service.stop()

        self.assertTrue(process.terminated)
        self.assertEqual(service.state, DebugState.STOPPED)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_non_loopback_telnet_is_rejected_before_process_launch(self) -> None:
        launched = []
        service = DebugService(
            executable="openocd",
            process_factory=lambda command, **kwargs: launched.append(command),
        )

        with self.assertRaisesRegex(ValueError, "loopback"):
            service.start(DebugConfig(ProbeRef("DEBUG123"), "10.1.2.3", 3333, 4444))

        self.assertEqual(launched, [])
        self.assertEqual(service.state, DebugState.STOPPED)

    def test_port_readiness_failure_releases_session(self) -> None:
        process = FakeProcess()
        manager = HardwareSessionManager()
        service = DebugService(
            executable="openocd",
            session_manager=manager,
            process_factory=lambda command, **kwargs: process,
            port_waiter=lambda host, port, timeout: False,
        )

        with self.assertRaisesRegex(RuntimeError, "not ready"):
            service.start(DebugConfig(ProbeRef("DEBUG123")))

        self.assertTrue(process.terminated)
        self.assertEqual(service.state, DebugState.FAILED)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_start_forwards_openocd_stdout_to_live_log_sink(self) -> None:
        class OutputProcess(FakeProcess):
            def __init__(self) -> None:
                super().__init__()
                self.stdout = iter(["Info : init\n", "Info : gdb server started\n"])

        messages = []
        service = DebugService(
            executable="openocd",
            process_factory=lambda command, **kwargs: OutputProcess(),
            port_waiter=lambda host, port, timeout: True,
        )

        service.start(DebugConfig(ProbeRef("DEBUG123")), event_sink=messages.append)
        deadline = time.monotonic() + 1.0
        while len(messages) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        service.stop()

        self.assertEqual(messages, ["Info : init", "Info : gdb server started"])

    def test_busy_session_rejects_debug_without_releasing_current_owner(self) -> None:
        manager = HardwareSessionManager()
        service = DebugService(
            executable="openocd",
            session_manager=manager,
            process_factory=lambda *args, **kwargs: self.fail("must not launch"),
        )

        entered = threading.Event()
        errors = []

        def attempt_debug() -> None:
            entered.wait(timeout=2)
            try:
                service.start(DebugConfig(ProbeRef("DEBUG123")))
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=attempt_debug)
        worker.start()
        with manager.acquire(HardwareMode.FLASHING, ProbeRef("FLASH123")):
            entered.set()
            worker.join(timeout=2)
            self.assertEqual(1, len(errors))
            self.assertIn("FLASHING", str(errors[0]))
            self.assertEqual(manager.snapshot().mode, HardwareMode.FLASHING)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_process_crash_releases_debug_session_and_marks_failed(self) -> None:
        process = FakeProcess()
        manager = HardwareSessionManager()
        service = DebugService(
            executable="openocd", session_manager=manager,
            process_factory=lambda command, **kwargs: process,
            port_waiter=lambda host, port, timeout: True,
        )
        service.start(DebugConfig(ProbeRef("DEBUG123")))
        process.returncode = 9

        self.assertEqual(service.state, DebugState.FAILED)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_start_in_worker_and_stop_in_controller_thread_releases_session(self) -> None:
        process = FakeProcess()
        manager = HardwareSessionManager()
        service = DebugService(
            executable="openocd", session_manager=manager,
            process_factory=lambda command, **kwargs: process,
            port_waiter=lambda host, port, timeout: True,
        )
        errors = []

        def start_from_worker() -> None:
            try:
                service.start(DebugConfig(ProbeRef("DEBUG123")))
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=start_from_worker)
        worker.start()
        worker.join(timeout=1)
        self.assertEqual(errors, [])
        self.assertEqual(manager.snapshot().mode, HardwareMode.DEBUGGING)

        service.stop()

        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)
        self.assertEqual(service.state, DebugState.STOPPED)


if __name__ == "__main__":
    unittest.main()
