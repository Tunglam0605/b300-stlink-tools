from __future__ import annotations

import unittest

from b300_core.live_service import LiveMonitorService
from b300_core.models import ProbeRef


class FakeLease:
    def __init__(self):
        self.released = False
    def release(self):
        self.released = True


class FakeSessionManager:
    def __init__(self):
        self.lease = FakeLease()
        self.probe = None
    def acquire_monitoring(self, probe):
        self.probe = probe
        return self.lease


class FakeProcess:
    def __init__(self):
        self.code = None
        self.stdout = iter([
            "Info : Listening on port 16666 for tcl connections\n",
            "Info : telnet server disabled\n",
        ])
        self.terminated = False
    def poll(self):
        return self.code
    def terminate(self):
        self.terminated = True
        self.code = 0
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self.code = -9


class LiveMonitorServiceTests(unittest.TestCase):
    def test_command_is_tcl_only_and_contains_no_halt_or_write_surface(self):
        service = LiveMonitorService(executable="openocd", session_manager=FakeSessionManager())
        command = service.command(ProbeRef("ABC"), 16666)
        rendered = " ".join(command).lower()
        self.assertIn("bindto 127.0.0.1", rendered)
        self.assertIn("gdb port disabled", rendered)
        self.assertIn("telnet port disabled", rendered)
        self.assertIn("tcl port 16666", rendered)
        for token in (" halt", "resume", "reset", "flash erase", "program {", "mww ", "mwh ", "mwb "):
            self.assertNotIn(token, rendered)

    def test_start_holds_hardware_lease_until_stop(self):
        manager = FakeSessionManager()
        process = FakeProcess()
        captured = {}
        service = LiveMonitorService(
            executable="openocd", session_manager=manager,
            process_factory=lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or process,
        )
        probe = ProbeRef("ABC")
        service.start(probe, 16666, readiness_timeout_seconds=0.5)
        self.assertEqual(manager.probe, probe)
        self.assertFalse(manager.lease.released)
        self.assertIn("gdb port disabled", " ".join(captured["command"]).lower())
        service.stop()
        self.assertTrue(manager.lease.released)
        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
