from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b300_core.ssh_live_tunnel import SshLiveTunnel, SshLiveTunnelConfig


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


class SshLiveTunnelTests(unittest.TestCase):
    def test_argv_forwards_tcl_only_and_keeps_strict_ssh_policy(self):
        config = SshLiveTunnelConfig(
            "gateway.local", "automation", local_tcl_port=16666, gateway_tcl_port=6666
        )
        argv = config.argv("ssh")
        rendered = " ".join(argv)
        self.assertIn("BatchMode=yes", rendered)
        self.assertIn("StrictHostKeyChecking=yes", rendered)
        self.assertIn("ExitOnForwardFailure=yes", rendered)
        self.assertIn("127.0.0.1:16666:127.0.0.1:6666", rendered)
        self.assertNotIn(":3333", rendered)
        self.assertEqual(argv.count("-L"), 1)
        self.assertNotIn("0.0.0.0", rendered)

    def test_verified_identity_is_added_to_live_tunnel(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / "id_ed25519"
            identity.write_text("private-placeholder", encoding="utf-8")
            known_hosts = Path(directory) / "b300_known_hosts"
            known_hosts.write_text("gateway.local ssh-ed25519 AAAAplaceholder\n", encoding="utf-8")
            config = SshLiveTunnelConfig(
                "gateway.local", "automation", local_tcl_port=16666,
                identity_file=identity, known_hosts_file=known_hosts,
            )
            argv = config.argv("ssh")
            rendered = " ".join(argv)
            self.assertIn("IdentitiesOnly=yes", rendered)
            self.assertIn(str(identity), argv)
            self.assertIn("UserKnownHostsFile=%s" % known_hosts, argv)
            self.assertIn("StrictHostKeyChecking=yes", rendered)
            self.assertEqual(argv.count("-L"), 1)
            self.assertNotIn(":3333", rendered)

    def test_start_checks_forwarded_tcl_and_stop_owns_process(self):
        captured = {}
        process = FakeProcess()
        tunnel = SshLiveTunnel(
            SshLiveTunnelConfig("gateway.local", "automation", local_tcl_port=16666),
            ssh_executable="ssh-test",
            process_factory=lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or process,
            tcl_factory=FakeTcl,
        )
        self.assertEqual(tunnel.start(), "OpenOCD forwarded")
        self.assertEqual(tunnel.tcl_endpoint, ("127.0.0.1", 16666))
        self.assertTrue(tunnel.active)
        self.assertNotIn(":3333", " ".join(captured["command"]))
        tunnel.stop()
        self.assertTrue(process.terminated)
        self.assertFalse(tunnel.active)

    def test_identity_and_ports_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "host"):
            SshLiveTunnelConfig("host;bad", "user").validate()
        with self.assertRaisesRegex(ValueError, "user"):
            SshLiveTunnelConfig("host", "bad user").validate()
        with self.assertRaisesRegex(ValueError, "port"):
            SshLiveTunnelConfig("host", "user", local_tcl_port=0).validate()


if __name__ == "__main__":
    unittest.main()
