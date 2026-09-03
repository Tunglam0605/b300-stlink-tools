from __future__ import annotations

import unittest

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
    def test_argv_forwards_tcl_only_with_password_interactive_ssh(self):
        config = SshLiveTunnelConfig("gateway.local", "automation", local_tcl_port=16666, gateway_tcl_port=6666)
        argv = config.argv("ssh")
        rendered = " ".join(argv)
        self.assertIn("PreferredAuthentications=password,keyboard-interactive", rendered)
        self.assertIn("PasswordAuthentication=yes", rendered)
        self.assertIn("KbdInteractiveAuthentication=yes", rendered)
        self.assertIn("PubkeyAuthentication=no", rendered)
        self.assertIn("ExitOnForwardFailure=yes", rendered)
        self.assertIn("127.0.0.1:16666:127.0.0.1:6666", rendered)
        self.assertNotIn(":3333", rendered)
        self.assertEqual(argv.count("-L"), 1)
        self.assertNotIn("0.0.0.0", rendered)

    def test_argv_has_no_managed_identity_or_known_hosts_override(self):
        argv = SshLiveTunnelConfig("gateway.local", "automation").argv("ssh")
        rendered = " ".join(argv)
        self.assertNotIn("-i", argv)
        self.assertNotIn("IdentityFile", rendered)
        self.assertNotIn("KnownHostsFile", rendered)
        self.assertNotIn("BatchMode=yes", rendered)

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
