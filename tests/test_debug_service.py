from __future__ import annotations

import unittest
import time
import threading
import subprocess
import socket
from unittest import mock

from b300_core.debug_service import DebugConfig, DebugService, DebugState
from b300_core.hardware_session import HardwareMode, HardwareSessionManager
from b300_core.models import ProbeRef


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.returncode = None
        self.stdout = iter(["Info : Listening on port 3333 for gdb connections\n"])

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

    def test_unrelated_or_wrong_port_logs_do_not_mark_debug_ready(self) -> None:
        process = FakeProcess()
        process.stdout = iter([
            "Info : Listening on port 4444 for gdb connections\n",
            "Info : unrelated startup message\n",
        ])
        manager = HardwareSessionManager()
        service = DebugService(
            executable="openocd",
            session_manager=manager,
            process_factory=lambda command, **kwargs: process,
        )

        with self.assertRaisesRegex(RuntimeError, "Last log"):
            service.start(DebugConfig(ProbeRef("DEBUG123")), readiness_timeout_seconds=0.02)

        self.assertTrue(process.terminated)
        self.assertEqual(service.state, DebugState.FAILED)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_start_forwards_openocd_stdout_to_live_log_sink(self) -> None:
        class OutputProcess(FakeProcess):
            def __init__(self) -> None:
                super().__init__()
                self.stdout = iter([
                    "Info : init\n",
                    "Info : Listening on port 3333 for gdb connections\n",
                ])

        messages = []
        service = DebugService(
            executable="openocd",
            process_factory=lambda command, **kwargs: OutputProcess(),
        )

        service.start(DebugConfig(ProbeRef("DEBUG123")), event_sink=messages.append)
        deadline = time.monotonic() + 1.0
        while len(messages) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        service.stop()

        self.assertEqual(messages, [
            "Info : init", "Info : Listening on port 3333 for gdb connections",
        ])

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

    def test_matching_listener_line_marks_ready_without_opening_a_tcp_connection(self) -> None:
        process = FakeProcess()
        calls = []
        service = DebugService(
            executable="openocd",
            process_factory=lambda command, **kwargs: calls.append(kwargs) or process,
            platform_name="windows",
        )
        with mock.patch("socket.create_connection") as connect, \
             mock.patch("b300_core.process_startup.subprocess.CREATE_NO_WINDOW", 0x08000000,
                        create=True):
            self.assertEqual(service.start(DebugConfig(ProbeRef("DEBUG123"))), DebugState.READY)
        connect.assert_not_called()
        self.assertTrue(calls[0]["creationflags"] & 0x08000000)
        self.assertEqual(calls[0]["stdout"], subprocess.PIPE)
        self.assertEqual(calls[0]["stderr"], subprocess.STDOUT)
        self.assertTrue(calls[0]["text"])
        self.assertFalse(calls[0]["shell"])
        service.stop()

    def test_process_exit_before_listener_line_reports_failure(self) -> None:
        process = FakeProcess()
        process.returncode = 9
        process.stdout = iter(["Error: adapter failed\n"])
        service = DebugService(executable="openocd", process_factory=lambda *args, **kwargs: process)
        with self.assertRaisesRegex(RuntimeError, "exited"):
            service.start(DebugConfig(ProbeRef("DEBUG123")), readiness_timeout_seconds=0.1)
        self.assertEqual(service.state, DebugState.FAILED)

    def test_listener_then_exit_never_reports_ready_or_holds_session(self) -> None:
        class ListenerThenExitProcess(FakeProcess):
            def __init__(self) -> None:
                super().__init__()
                self.emitted_listener = False
                self.stdout = self._lines()

            def _lines(self):
                self.emitted_listener = True
                yield "Info : Listening on port 3333 for gdb connections\n"

            def poll(self):
                return 9 if self.emitted_listener else None

        process = ListenerThenExitProcess()
        manager = HardwareSessionManager()
        service = DebugService(
            executable="openocd", session_manager=manager,
            process_factory=lambda *args, **kwargs: process,
        )

        with self.assertRaisesRegex(RuntimeError, "exited"):
            service.start(DebugConfig(ProbeRef("DEBUG123")))

        self.assertEqual(service.state, DebugState.FAILED)
        self.assertEqual(manager.snapshot().mode, HardwareMode.IDLE)

    def test_timeout_keeps_only_the_last_ten_log_lines_but_forwards_every_line(self) -> None:
        process = FakeProcess()
        process.stdout = iter(["Info : startup-%d\n" % number for number in range(12)])
        messages = []
        service = DebugService(executable="openocd", process_factory=lambda *args, **kwargs: process)

        with self.assertRaisesRegex(RuntimeError, "startup-11") as error:
            service.start(
                DebugConfig(ProbeRef("DEBUG123")), readiness_timeout_seconds=0.02,
                event_sink=messages.append,
            )

        self.assertNotIn("startup-0", str(error.exception))
        self.assertEqual(messages, ["Info : startup-%d" % number for number in range(12)])

    def test_log_buffer_storage_is_bounded_to_the_last_ten_lines(self) -> None:
        process = FakeProcess()
        process.stdout = iter(["Info : retained-%d\n" % number for number in range(12)])
        messages = []
        ready, logs, _log_lock = DebugService._forward_output(process, 3333, messages.append)
        deadline = time.monotonic() + 1.0
        while len(messages) < 12 and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertFalse(ready.is_set())
        self.assertEqual(messages, ["Info : retained-%d" % number for number in range(12)])
        self.assertEqual(list(logs), ["Info : retained-%d" % number for number in range(2, 12)])


if __name__ == "__main__":
    unittest.main()
