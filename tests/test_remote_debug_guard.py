from __future__ import annotations

import unittest

from b300_core.remote_debug_guard import RemoteDebugGuard


class FakeTcl:
    def __init__(self, state="running") -> None:
        self.state = state
        self.resume_calls = 0

    def wait_target_state(self):
        return self.state

    def resume_target(self):
        self.resume_calls += 1
        self.state = "running"
        return self.state


class RemoteDebugGuardTests(unittest.TestCase):
    def test_disconnect_restores_running_target(self) -> None:
        tcl = FakeTcl("running")
        events = []
        guard = RemoteDebugGuard(tcl, lambda event, message: events.append((event, message)))
        self.assertEqual(guard.capture_initial_state(), "running")
        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        tcl.state = "halted"
        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        self.assertEqual(tcl.resume_calls, 1)
        self.assertEqual(tcl.state, "running")
        self.assertTrue(any(event == "restored" for event, _message in events))

    def test_preexisting_halted_target_is_never_forced_running(self) -> None:
        tcl = FakeTcl("halted")
        guard = RemoteDebugGuard(tcl)
        guard.capture_initial_state()
        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        self.assertEqual(tcl.resume_calls, 0)
        self.assertEqual(tcl.state, "halted")

    def test_shutdown_restores_if_session_was_left_halted(self) -> None:
        tcl = FakeTcl("running")
        guard = RemoteDebugGuard(tcl)
        guard.capture_initial_state()
        tcl.state = "halted"
        snapshot = guard.restore_initial_state(reason="server_shutdown")
        self.assertTrue(snapshot.restored)
        self.assertEqual(snapshot.initial_target_state, "running")
        self.assertEqual(snapshot.final_target_state, "running")

    def test_drop_before_any_gdb_connection_is_ignored(self) -> None:
        tcl = FakeTcl("running")
        guard = RemoteDebugGuard(tcl)
        guard.capture_initial_state()
        tcl.state = "halted"
        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        self.assertEqual(tcl.resume_calls, 0)
        self.assertEqual(tcl.state, "halted")


if __name__ == "__main__":
    unittest.main()
