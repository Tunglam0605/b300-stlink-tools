from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b300_core.ssh_debug_tunnel import SshDebugTunnel, SshDebugTunnelConfig


class FakeProcess:
    def __init__(self, code=None):
        self.code = code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def wait(self, timeout=None):
        return 0 if self.code is None else self.code

    def kill(self):
        self.killed = True
        self.code = -9


class FakeTcl:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def version(self):
        return "OpenOCD forwarded"


class SshDebugTunnelTests(unittest.TestCase):
    def test_argv_forwards_only_loopback_gdb_and_tcl(self):
        config = SshDebugTunnelConfig("gateway.local", "automation", local_gdb_port=13333, local_tcl_port=16666)
        argv = config.argv("ssh")
        rendered = " ".join(argv)
        self.assertIn("BatchMode=yes", rendered)
        self.assertIn("StrictHostKeyChecking=yes", rendered)
        self.assertIn("127.0.0.1:13333:127.0.0.1:3333", rendered)
        self.assertIn("127.0.0.1:16666:127.0.0.1:6666", rendered)
        self.assertTrue(rendered.endswith("automation@gateway.local"))
        self.assertNotIn("0.0.0.0", rendered)

    def test_verified_identity_is_added_without_changing_loopback_forwarding(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / "id_ed25519"
            identity.write_text("private-placeholder", encoding="utf-8")
            config = SshDebugTunnelConfig(
                "gateway.local", "automation", local_gdb_port=13333, local_tcl_port=16666,
                identity_file=identity,
            )
            argv = config.argv("ssh")
            rendered = " ".join(argv)
            self.assertIn("IdentitiesOnly=yes", rendered)
            self.assertIn("-i", argv)
            self.assertIn(str(identity), argv)
            self.assertIn("127.0.0.1:13333:127.0.0.1:3333", rendered)
            self.assertNotIn("0.0.0.0", rendered)

    def test_start_waits_for_forwarded_tcl_and_stop_owns_ssh_process(self):
        captured = {}
        process = FakeProcess()
        tunnel = SshDebugTunnel(
            SshDebugTunnelConfig("gateway.local", "automation", local_gdb_port=13333, local_tcl_port=16666),
            ssh_executable="ssh-test",
            process_factory=lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or process,
            tcl_factory=FakeTcl,
        )
        self.assertEqual(tunnel.start(), "OpenOCD forwarded")
        self.assertTrue(tunnel.active)
        self.assertEqual(tunnel.gdb_endpoint, ("127.0.0.1", 13333))
        self.assertEqual(tunnel.tcl_endpoint, ("127.0.0.1", 16666))
        self.assertEqual(captured["command"][0], "ssh-test")
        tunnel.stop()
        self.assertTrue(process.terminated)
        self.assertFalse(tunnel.active)

    def test_ssh_process_exit_fails_closed(self):
        tunnel = SshDebugTunnel(
            SshDebugTunnelConfig("gateway.local", "automation"),
            ssh_executable="ssh-test",
            process_factory=lambda *args, **kwargs: FakeProcess(255),
            tcl_factory=FakeTcl,
        )
        with self.assertRaisesRegex(RuntimeError, "SSH debug tunnel exited"):
            tunnel.start(timeout_seconds=0.5)

    def test_invalid_identity_and_duplicate_local_ports_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "host"):
            SshDebugTunnelConfig("host;bad", "user").validate()
        with self.assertRaisesRegex(ValueError, "user"):
            SshDebugTunnelConfig("host", "bad user").validate()
        with self.assertRaisesRegex(ValueError, "distinct"):
            SshDebugTunnelConfig("host", "user", local_gdb_port=3333, local_tcl_port=3333).validate()


if __name__ == "__main__":
    unittest.main()
