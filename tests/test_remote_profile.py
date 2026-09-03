from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from b300_core.remote_connectivity import build_connectivity_argv, check_remote_connectivity
from b300_core.remote_profile import (
    RemoteGatewayProfile, clear_remote_profile, default_remote_profile_path,
    load_remote_profile, save_remote_profile,
)


class RemoteGatewayProfileTests(unittest.TestCase):
    def test_windows_default_profile_path_is_per_user(self):
        path = default_remote_profile_path(
            home=Path("C:/Users/test"), environ={"LOCALAPPDATA": "C:/Users/test/AppData/Local"},
            system_name="Windows",
        )
        self.assertEqual(path, Path("C:/Users/test/AppData/Local/B300-STLink/remote_gateway.json"))

    def test_linux_default_profile_path_uses_xdg(self):
        path = default_remote_profile_path(
            home=Path("/home/test"), environ={"XDG_CONFIG_HOME": "/tmp/config"},
            system_name="Linux",
        )
        self.assertEqual(path, Path("/tmp/config/b300-stlink/remote_gateway.json"))

    def test_save_load_and_clear_profile_contains_no_secret_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "profile.json"
            profile = RemoteGatewayProfile("gateway.local", "automation", 22)
            saved = save_remote_profile(profile, target)
            raw = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(set(raw), {"schema_version", "host", "user", "port"})
            self.assertEqual(load_remote_profile(target), profile)
            self.assertTrue(clear_remote_profile(target))
            self.assertIsNone(load_remote_profile(target))
            self.assertFalse(clear_remote_profile(target))

    def test_corrupt_profile_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "profile.json"
            target.write_text('{"schema_version": 1, "host": "bad host", "user": "u", "port": 22}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_remote_profile(target)

    def test_username_validation_rejects_shell_metacharacters(self):
        with self.assertRaises(ValueError):
            RemoteGatewayProfile("gateway.local", "user;whoami", 22).validate()


class RemoteConnectivityTests(unittest.TestCase):
    def test_connectivity_argv_is_password_interactive_and_public_ports_are_not_used(self):
        argv = build_connectivity_argv(
            RemoteGatewayProfile("192.168.1.109", "automation", 22),
            ssh_executable="ssh-test",
        )
        expected_options = (
            "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "PasswordAuthentication=yes",
            "-o", "KbdInteractiveAuthentication=yes",
            "-o", "PubkeyAuthentication=no",
        )
        self.assertEqual(argv[2:2 + len(expected_options)], expected_options)
        joined = " ".join(argv)
        self.assertNotIn("BatchMode=yes", joined)
        self.assertNotIn("-i", argv)
        self.assertNotIn("IdentityFile", joined)
        self.assertNotIn("KnownHostsFile", joined)
        self.assertNotIn("ClearAllForwardings=yes", argv)
        self.assertNotIn("3333", joined)
        self.assertNotIn("6666", joined)
        self.assertEqual(argv[-1], "echo B300_SSH_READY")

    def test_connectivity_passes_only_on_expected_ready_token(self):
        calls = []
        def runner(argv, timeout):
            calls.append((tuple(argv), timeout))
            return subprocess.CompletedProcess(argv, 0, "B300_SSH_READY\n", "")
        result = check_remote_connectivity(
            RemoteGatewayProfile("gateway.local", "automation", 2222), runner=runner,
            ssh_executable="ssh-test",
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.reason_code, "SSH_READY")
        self.assertIn("automation@gateway.local:2222", result.gateway)
        self.assertEqual(len(calls), 1)

    def test_connectivity_failure_reports_no_password_value(self):
        def runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 255, "", "Permission denied (password).")
        result = check_remote_connectivity(
            RemoteGatewayProfile("gateway.local", "automation", 22), runner=runner,
            ssh_executable="ssh-test",
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.reason_code, "SSH_CONNECT_FAILED")
        self.assertIn("Permission denied", result.message)
        self.assertNotIn("password=", result.message.lower())


if __name__ == "__main__":
    unittest.main()
