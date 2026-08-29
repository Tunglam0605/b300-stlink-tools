from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from b300_core.debug_service import DebugState
from b300_core.debug_session import DebugSession, DebugSessionConfig
from b300_core.models import ProbeRef


class FakeService:
    def __init__(self, events):
        self.events = events
        self.state = DebugState.STOPPED
        self.config = None

    def start(self, config, event_sink=None):
        self.config = config
        self.events.append("openocd-start")
        self.state = DebugState.READY
        return self.state

    def mark_connected(self):
        self.events.append("openocd-connected")
        self.state = DebugState.CONNECTED

    def stop(self):
        self.events.append("openocd-stop")
        self.state = DebugState.STOPPED


class FakeGdb:
    def __init__(self, events, fail_connect=False):
        self.events = events
        self.running = False
        self.fail_connect = fail_connect

    def start(self):
        self.events.append("gdb-start")
        self.running = True

    def load_symbols(self, path):
        self.events.append(("symbols", str(path)))

    def connect(self, host, port):
        self.events.append(("gdb-connect", host, port))
        if self.fail_connect:
            raise RuntimeError("connect failed")

    def stop(self):
        self.events.append("gdb-stop")
        self.running = False

    def current_frame(self):
        return "frame"

    def stack_frames(self, limit):
        return ("stack", limit)

    def register_values(self):
        return ("registers",)

    def evaluate_variable(self, expression):
        return ("variable", expression)

    def insert_hardware_breakpoint(self, location):
        self.events.append(("break", location))
        return SimpleNamespace(number=1, kind="hardware-breakpoint", location=location)

    def insert_watchpoint(self, expression):
        self.events.append(("watch", expression))
        return SimpleNamespace(number=2, kind="watchpoint", location=expression)

    def delete_breakpoint(self, number):
        self.events.append(("delete", number))
        return ("delete", number)

    def interrupt(self):
        return "halt"

    def interrupt_and_wait_stopped(self):
        self.events.append("interrupt-stopped")
        return "stopped"

    def continue_execution(self):
        self.events.append("continue")
        return "continue"

    def continue_and_wait_stopped(self, timeout_seconds=None):
        self.events.append(("continue-wait", timeout_seconds))
        return SimpleNamespace(
            prefix="*", body='stopped,reason="breakpoint-hit",bkptno="1"'
        )

    def step(self):
        return "step"

    def next(self):
        return "next"

    def reset_halt(self):
        return "reset-halt"


class FakeTcl:
    def __init__(self, endpoint, events, poll_state="halted"):
        self.endpoint = endpoint
        self.events = events
        self.poll_state = poll_state
        self.events.append(("tcl-create", endpoint.host, endpoint.port))

    def version(self):
        self.events.append("tcl-version")
        return "OpenOCD test"

    def target_state(self):
        return self.poll_state

    def wait_target_state(self):
        return self.poll_state

    def read_words(self, address, count):
        return (address, count)


class DebugSessionTests(unittest.TestCase):
    def make_session(self, *, fail_connect=False, poll_state="halted"):
        events = []
        service = FakeService(events)
        gdb = FakeGdb(events, fail_connect=fail_connect)
        session = DebugSession(
            service=service,
            gdb=gdb,
            tcl_factory=lambda endpoint: FakeTcl(endpoint, events, poll_state),
        )
        return session, service, gdb, events

    def test_start_verifies_tcl_then_loads_symbols_and_connects_gdb(self) -> None:
        session, service, _gdb, events = self.make_session()
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.elf"
            symbols.write_bytes(b"ELF")
            info = session.start(DebugSessionConfig(ProbeRef("TEST"), symbols))
        self.assertEqual(info.state, "CONNECTED")
        self.assertEqual(info.gdb_endpoint, "127.0.0.1:3333")
        self.assertEqual(info.tcl_endpoint, "127.0.0.1:6666")
        self.assertTrue(session.active)
        self.assertEqual(service.config.tcl_port, 6666)
        self.assertLess(events.index("tcl-version"), events.index("gdb-start"))
        symbol_event = next(item for item in events if isinstance(item, tuple) and item[0] == "symbols")
        connect_event = next(item for item in events if isinstance(item, tuple) and item[0] == "gdb-connect")
        self.assertLess(events.index(symbol_event), events.index(connect_event))
        session.stop()
        self.assertFalse(session.active)

    def test_partial_gdb_failure_cleans_gdb_and_openocd(self) -> None:
        session, service, gdb, events = self.make_session(fail_connect=True)
        with self.assertRaisesRegex(RuntimeError, "connect failed"):
            session.start(DebugSessionConfig(ProbeRef("TEST")))
        self.assertEqual(service.state, DebugState.STOPPED)
        self.assertFalse(gdb.running)
        self.assertFalse(session.active)
        self.assertLess(events.index("gdb-stop"), events.index("openocd-stop"))

    def test_inspect_preserves_running_target_state(self) -> None:
        session, _service, _gdb, events = self.make_session(poll_state="running")
        session.start(DebugSessionConfig(ProbeRef("TEST")))
        snapshot = session.inspect(4)
        self.assertEqual(snapshot.target_state_before, "running")
        self.assertTrue(snapshot.resumed)
        self.assertIn("interrupt-stopped", events)
        self.assertIn("continue", events)
        self.assertLess(events.index("interrupt-stopped"), events.index("continue"))
        session.stop()

    def test_inspect_keeps_preexisting_halt_state(self) -> None:
        session, _service, _gdb, events = self.make_session(poll_state="halted")
        session.start(DebugSessionConfig(ProbeRef("TEST")))
        snapshot = session.inspect(4)
        self.assertFalse(snapshot.resumed)
        self.assertNotIn("interrupt-stopped", events)
        self.assertNotIn("continue", events)
        session.stop()

    def test_break_once_verifies_hit_deletes_resource_and_resumes(self) -> None:
        session, _service, gdb, events = self.make_session(poll_state="halted")
        session.start(DebugSessionConfig(ProbeRef("TEST")))
        session.initial_target_state = "running"
        hit = session.break_once("main", timeout_seconds=2.5)
        self.assertEqual(hit.kind, "hardware-breakpoint")
        self.assertEqual(hit.number, 1)
        self.assertEqual(hit.reason, "breakpoint-hit")
        self.assertEqual(hit.frame, "frame")
        self.assertIn(("break", "main"), events)
        self.assertIn(("continue-wait", 2.5), events)
        self.assertIn(("delete", 1), events)
        self.assertEqual(events[-1], "continue")
        session.stop()

    def test_break_once_failure_still_deletes_resource_and_resumes(self) -> None:
        session, _service, gdb, events = self.make_session(poll_state="halted")
        session.start(DebugSessionConfig(ProbeRef("TEST")))
        session.initial_target_state = "running"

        def fail_wait(timeout_seconds=None):
            events.append(("continue-wait", timeout_seconds))
            raise RuntimeError("timeout")

        gdb.continue_and_wait_stopped = fail_wait
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            session.break_once("main", timeout_seconds=1.0)
        self.assertIn(("delete", 1), events)
        self.assertEqual(events[-1], "continue")
        session.stop()

    def test_watch_once_accepts_watchpoint_trigger_and_cleans_up(self) -> None:
        session, _service, gdb, events = self.make_session(poll_state="halted")
        session.start(DebugSessionConfig(ProbeRef("TEST")))
        session.initial_target_state = "running"
        gdb.continue_and_wait_stopped = lambda timeout_seconds=None: SimpleNamespace(
            prefix="*", body='stopped,reason="watchpoint-trigger",wpt={number="2",exp="speed"}'
        )
        hit = session.watch_once("speed", timeout_seconds=3.0)
        self.assertEqual(hit.kind, "watchpoint")
        self.assertEqual(hit.number, 2)
        self.assertEqual(hit.reason, "watchpoint-trigger")
        self.assertIn(("delete", 2), events)
        self.assertEqual(events[-1], "continue")
        session.stop()

    def test_integrated_session_rejects_non_loopback_bind(self) -> None:
        session, _service, _gdb, events = self.make_session()
        with self.assertRaisesRegex(ValueError, "loopback-only"):
            session.start(DebugSessionConfig(ProbeRef("TEST"), bind_address="0.0.0.0"))
        self.assertEqual(events, [])

    def test_debug_operations_delegate_only_when_connected(self) -> None:
        session, _service, _gdb, _events = self.make_session()
        with self.assertRaisesRegex(RuntimeError, "not CONNECTED"):
            session.where()
        session.start(DebugSessionConfig(ProbeRef("TEST")))
        self.assertEqual(session.where(), "frame")
        self.assertEqual(session.stack(4), ("stack", 4))
        self.assertEqual(session.registers(), ("registers",))
        self.assertEqual(session.variable("speed"), ("variable", "speed"))
        self.assertEqual(session.hardware_breakpoint("main").number, 1)
        self.assertEqual(session.watchpoint("speed").number, 2)
        self.assertEqual(session.target_poll(), "halted")
        self.assertEqual(session.read_words(0x20000000, 2), (0x20000000, 2))
        session.stop()


if __name__ == "__main__":
    unittest.main()
