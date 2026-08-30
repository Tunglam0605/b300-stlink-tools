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
    def _files(self, root: Path):
        ssh = root / "ssh.exe"
        identity = root / "id_ed25519"
        known = root / "known_hosts"
        for item in (ssh, identity, known):
            item.write_text("x", encoding="utf-8")
        return ssh, identity, known

    def test_connectivity_argv_is_batch_strict_and_public_ports_are_not_used(self):
        with tempfile.TemporaryDirectory() as directory:
            ssh, identity, known = self._files(Path(directory))
            argv = build_connectivity_argv(
                RemoteGatewayProfile("192.168.1.109", "automation", 22),
                ssh_executable=ssh, identity_file=identity, known_hosts_file=known,
            )
        joined = " ".join(argv)
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("StrictHostKeyChecking=yes", joined)
        self.assertIn("IdentitiesOnly=yes", joined)
        self.assertIn("PasswordAuthentication=no", joined)
        self.assertNotIn("3333", joined)
        self.assertNotIn("6666", joined)
        self.assertEqual(argv[-1], "echo B300_SSH_READY")

    def test_connectivity_passes_only_on_expected_ready_token(self):
        with tempfile.TemporaryDirectory() as directory:
            ssh, identity, known = self._files(Path(directory))
            calls = []
            def runner(argv, timeout):
                calls.append((tuple(argv), timeout))
                return subprocess.CompletedProcess(argv, 0, "B300_SSH_READY\n", "")
            result = check_remote_connectivity(
                RemoteGatewayProfile("gateway.local", "automation", 2222), runner=runner,
                ssh_executable=ssh, identity_file=identity, known_hosts_file=known,
            )
        self.assertTrue(result.ready)
        self.assertEqual(result.reason_code, "SSH_READY")
        self.assertIn("automation@gateway.local:2222", result.gateway)
        self.assertEqual(len(calls), 1)

    def test_connectivity_failure_is_bounded_and_noninteractive(self):
        with tempfile.TemporaryDirectory() as directory:
            ssh, identity, known = self._files(Path(directory))
            def runner(argv, timeout):
                return subprocess.CompletedProcess(argv, 255, "", "Permission denied (publickey).")
            result = check_remote_connectivity(
                RemoteGatewayProfile("gateway.local", "automation", 22), runner=runner,
                ssh_executable=ssh, identity_file=identity, known_hosts_file=known,
            )
        self.assertFalse(result.ready)
        self.assertEqual(result.reason_code, "SSH_CONNECT_FAILED")
        self.assertIn("Permission denied", result.message)


if __name__ == "__main__":
    unittest.main()
