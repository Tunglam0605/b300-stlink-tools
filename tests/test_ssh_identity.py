from __future__ import annotations

import base64
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core import ssh_identity


def key_line(seed: int = 1, comment: str = "client") -> str:
    key_type = b"ssh-ed25519"
    key = bytes(((seed + index) % 256 for index in range(32)))
    blob = (
        struct.pack(">I", len(key_type)) + key_type +
        struct.pack(">I", len(key)) + key
    )
    return "ssh-ed25519 %s %s" % (base64.b64encode(blob).decode("ascii"), comment)


class SshIdentityTests(unittest.TestCase):
    def test_windows_resolver_prefers_system_openssh_then_uses_path_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            system_binary = Path(directory) / "System32" / "OpenSSH" / "ssh-keyscan.exe"
            system_binary.parent.mkdir(parents=True)
            system_binary.write_bytes(b"")
            with mock.patch.dict(os.environ, {"WINDIR": directory}), \
                    mock.patch.object(ssh_identity.platform, "system", return_value="Windows"), \
                    mock.patch.object(
                        ssh_identity.shutil, "which",
                        return_value=r"C:\PortableOpenSSH\ssh-keyscan.exe",
                    ):
                self.assertEqual(
                    ssh_identity.resolve_ssh_client_executable("ssh-keyscan"), system_binary,
                )

            system_binary.unlink()
            with mock.patch.dict(os.environ, {"WINDIR": directory}), \
                    mock.patch.object(ssh_identity.platform, "system", return_value="Windows"), \
                    mock.patch.object(
                        ssh_identity.shutil, "which",
                        return_value=r"C:\PortableOpenSSH\ssh-keyscan.exe",
                    ):
                self.assertEqual(
                    ssh_identity.resolve_ssh_client_executable("ssh-keyscan"),
                    Path(r"C:\PortableOpenSSH\ssh-keyscan.exe"),
                )

    def test_valid_key_normalizes_and_fingerprint_is_comment_independent(self):
        first = key_line(3, "first")
        second = key_line(3, "second comment")
        self.assertEqual(ssh_identity.public_key_identity(first), ssh_identity.public_key_identity(second))
        self.assertEqual(ssh_identity.public_key_fingerprint(first), ssh_identity.public_key_fingerprint(second))
        self.assertTrue(ssh_identity.public_key_fingerprint(first).startswith("SHA256:"))

    def test_rejects_multiline_wrong_type_bad_base64_and_noncanonical_blob(self):
        with self.assertRaisesRegex(ValueError, "Exactly one"):
            ssh_identity.validate_public_key(key_line() + "\n" + key_line(2))
        with self.assertRaisesRegex(ValueError, "ssh-ed25519"):
            ssh_identity.validate_public_key("ssh-rsa AAAA test")
        with self.assertRaisesRegex(ValueError, "base64"):
            ssh_identity.validate_public_key("ssh-ed25519 !!! test")
        arbitrary = base64.b64encode(b"x" * 64).decode("ascii")
        with self.assertRaisesRegex(ValueError, "canonical"):
            ssh_identity.validate_public_key("ssh-ed25519 %s test" % arbitrary)

    def test_inspect_missing_and_partial_pair_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "id"
            missing = ssh_identity.inspect_ssh_identity(private)
            self.assertFalse(missing.ready)
            self.assertFalse(missing.pair_exists)
            private.write_text("PRIVATE", encoding="utf-8")
            partial = ssh_identity.inspect_ssh_identity(private)
            self.assertFalse(partial.ready)
            self.assertFalse(partial.pair_exists)
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                ssh_identity.ensure_ssh_identity(private)

    def test_existing_valid_pair_is_reused_without_keygen(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "id"
            private.write_text("PRIVATE-CONTENT-MUST-NOT-BE-READ", encoding="utf-8")
            Path(str(private) + ".pub").write_text(key_line(), encoding="utf-8")
            runner = mock.Mock(side_effect=AssertionError("keygen must not execute"))
            report = ssh_identity.ensure_ssh_identity(private, runner=runner)
            self.assertTrue(report.ready)
            self.assertEqual(report.private_key, private)
            self.assertNotIn("PRIVATE-CONTENT", repr(report))
            runner.assert_not_called()

    def test_generate_identity_uses_ed25519_and_empty_passphrase(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / ".ssh" / "b300_gateway_ed25519"
            def fake_runner(argv, timeout):
                self.assertIn("-t", argv)
                self.assertEqual(argv[argv.index("-t") + 1], "ed25519")
                self.assertEqual(argv[argv.index("-N") + 1], "")
                output = Path(argv[argv.index("-f") + 1])
                output.write_text("PRIVATE-KEY", encoding="utf-8")
                Path(str(output) + ".pub").write_text(key_line(7, "b300-stlink-tools") + "\n", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, "", "")
            with mock.patch.object(ssh_identity.shutil, "which", return_value="ssh-keygen"):
                report = ssh_identity.ensure_ssh_identity(private, runner=fake_runner)
            self.assertTrue(report.ready)
            self.assertEqual(report.public_key_text, key_line(7, "b300-stlink-tools"))
            self.assertNotIn("PRIVATE-KEY", repr(report))

    def test_user_authorized_keys_append_is_idempotent_and_comment_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            first = ssh_identity.install_gateway_public_key(
                key_line(9, "first"), system_name="linux", home=home
            )
            second = ssh_identity.install_gateway_public_key(
                key_line(9, "different-comment"), system_name="linux", home=home
            )
            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            lines = first.target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len([line for line in lines if line.strip()]), 1)
            self.assertEqual(first.fingerprint, second.fingerprint)

    def test_windows_admin_uses_programdata_authorized_keys(self):
        def admin_runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 0, "YES\n", "")
        with tempfile.TemporaryDirectory() as directory:
            target, is_admin = ssh_identity.authorized_keys_target(
                system_name="windows", runner=admin_runner,
                home=Path(directory) / "home", program_data=Path(directory) / "ProgramData",
            )
            self.assertTrue(is_admin)
            self.assertEqual(target, Path(directory) / "ProgramData" / "ssh" / "administrators_authorized_keys")

    def test_windows_non_admin_uses_profile_authorized_keys(self):
        def user_runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 0, "NO\n", "")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            target, is_admin = ssh_identity.authorized_keys_target(
                system_name="windows", runner=user_runner, home=home,
                program_data=Path(directory) / "ProgramData",
            )
            self.assertFalse(is_admin)
            self.assertEqual(target, home / ".ssh" / "authorized_keys")

    def test_windows_client_prerequisite_detects_ready_and_missing_states(self):
        def installed_runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 0, "Installed\n", "")
        with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", side_effect=[Path("C:/OpenSSH/ssh.exe"), Path("C:/OpenSSH/ssh-keygen.exe")]):
            ready = ssh_identity.inspect_ssh_client_prerequisites(
                runner=installed_runner, system_name="windows"
            )
        self.assertTrue(ready.ready)
        self.assertEqual(ready.actions, ())

        def missing_runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 0, "NotPresent\n", "")
        with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", side_effect=[None, None]):
            missing = ssh_identity.inspect_ssh_client_prerequisites(
                runner=missing_runner, system_name="windows"
            )
        self.assertFalse(missing.ready)
        self.assertEqual(missing.actions, ("install_openssh_client",))

    def test_prepare_client_prerequisite_is_idempotent_when_ready(self):
        ready = ssh_identity.SshClientPrerequisiteReport(
            "windows", Path("ssh.exe"), Path("ssh-keygen.exe"), True, True, (), False
        )
        inspector = mock.Mock(return_value=ready)
        runner = mock.Mock(side_effect=AssertionError("no elevated command expected"))
        result = ssh_identity.prepare_ssh_client_prerequisites(
            runner=runner, system_name="windows", inspector=inspector
        )
        self.assertTrue(result.succeeded)
        self.assertFalse(result.changed)
        runner.assert_not_called()

    def test_prepare_windows_client_runs_one_elevated_install_then_verifies(self):
        missing = ssh_identity.SshClientPrerequisiteReport(
            "windows", None, None, False, False, ("install_openssh_client",), True
        )
        ready = ssh_identity.SshClientPrerequisiteReport(
            "windows", Path("ssh.exe"), Path("ssh-keygen.exe"), True, True, (), False
        )
        inspector = mock.Mock(side_effect=[missing, ready])
        commands = []
        def runner(argv, timeout):
            commands.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")
        result = ssh_identity.prepare_ssh_client_prerequisites(
            runner=runner, system_name="windows", inspector=inspector
        )
        self.assertTrue(result.succeeded)
        self.assertTrue(result.changed)
        self.assertEqual(inspector.call_count, 2)
        elevated = next(" ".join(command) for command in commands if "Start-Process" in " ".join(command))
        self.assertIn("-Verb RunAs", elevated)
        self.assertIn("-WindowStyle Hidden", elevated)
        self.assertIn("'-NonInteractive'", elevated)

    def test_managed_identity_returns_path_only_when_pair_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "id"
            self.assertIsNone(ssh_identity.managed_identity_file(private))
            private.write_text("SECRET", encoding="utf-8")
            Path(str(private) + ".pub").write_text(key_line(11), encoding="utf-8")
            self.assertEqual(ssh_identity.managed_identity_file(private), private)


if __name__ == "__main__":
    unittest.main()
