from __future__ import annotations

import unittest

from b300_core.remote_debug_guard import RemoteDebugGuard


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ManualScheduler:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.pending = []

    def __call__(self, delay_seconds, callback) -> None:
        self.pending.append((self.clock() + delay_seconds, callback))

    def run_ready(self) -> None:
        ready = [job for job in self.pending if job[0] <= self.clock()]
        self.pending = [job for job in self.pending if job[0] > self.clock()]
        for _deadline, callback in ready:
            callback()


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

    def test_two_gdb_connections_restore_only_after_last_disconnect(self) -> None:
        tcl = FakeTcl("running")
        guard = RemoteDebugGuard(tcl)
        guard.capture_initial_state()
        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")

        tcl.state = "halted"
        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        self.assertEqual(tcl.resume_calls, 0)
        self.assertEqual(tcl.state, "halted")

        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        self.assertEqual(tcl.resume_calls, 1)
        self.assertEqual(tcl.state, "running")

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

    def test_last_client_detached_is_emitted_once_after_a_real_attach(self) -> None:
        tcl = FakeTcl("running")
        clock = ManualClock()
        scheduler = ManualScheduler(clock)
        detached = []
        guard = RemoteDebugGuard(
            tcl,
            last_client_detached_sink=lambda snapshot: detached.append(snapshot),
            reclaim_delay_seconds=1.0, reclaim_scheduler=scheduler, clock=clock,
        )
        guard.capture_initial_state()

        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        self.assertEqual(detached, [])

        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        tcl.state = "halted"
        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        guard.handle_openocd_line("Info : dropped 'gdb' connection")

        clock.advance(1.0)
        scheduler.run_ready()
        self.assertEqual(len(detached), 1)
        self.assertTrue(detached[0].restored)
        self.assertEqual(tcl.resume_calls, 1)

    def test_reclaim_waits_for_grace_before_notifying_owner(self) -> None:
        tcl = FakeTcl("running")
        clock = ManualClock()
        scheduler = ManualScheduler(clock)
        detached = []
        guard = RemoteDebugGuard(
            tcl, last_client_detached_sink=detached.append,
            reclaim_delay_seconds=1.0, reclaim_scheduler=scheduler, clock=clock,
        )
        guard.capture_initial_state()

        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        scheduler.run_ready()
        self.assertEqual(detached, [])

        clock.advance(1.0)
        scheduler.run_ready()
        self.assertEqual(len(detached), 1)

    def test_rapid_reattach_invalidates_old_reclaim_and_later_detach_reclaims(self) -> None:
        tcl = FakeTcl("running")
        clock = ManualClock()
        scheduler = ManualScheduler(clock)
        detached = []
        guard = RemoteDebugGuard(
            tcl, last_client_detached_sink=detached.append,
            reclaim_delay_seconds=1.0, reclaim_scheduler=scheduler, clock=clock,
        )
        guard.capture_initial_state()

        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        clock.advance(1.0)
        scheduler.run_ready()
        self.assertEqual(detached, [])

        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        scheduler.run_ready()
        self.assertEqual(detached, [])
        clock.advance(1.0)
        scheduler.run_ready()
        self.assertEqual(len(detached), 1)

    def test_two_clients_reclaim_only_after_both_disconnect(self) -> None:
        tcl = FakeTcl("running")
        clock = ManualClock()
        scheduler = ManualScheduler(clock)
        detached = []
        guard = RemoteDebugGuard(
            tcl, last_client_detached_sink=detached.append,
            reclaim_delay_seconds=1.0, reclaim_scheduler=scheduler, clock=clock,
        )
        guard.capture_initial_state()

        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        guard.handle_openocd_line("Info : accepting 'gdb' connection on tcp/3333")
        tcl.state = "halted"
        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        clock.advance(1.0)
        scheduler.run_ready()
        self.assertEqual(detached, [])
        self.assertEqual(tcl.resume_calls, 0)

        guard.handle_openocd_line("Info : dropped 'gdb' connection")
        self.assertEqual(tcl.resume_calls, 1)
        clock.advance(1.0)
        scheduler.run_ready()
        self.assertEqual(len(detached), 1)


if __name__ == "__main__":
    unittest.main()
