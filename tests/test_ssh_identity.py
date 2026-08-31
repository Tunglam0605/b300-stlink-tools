from __future__ import annotations

import base64
import os
import re
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

    def test_windows_target_comes_from_effective_sshd_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            sshd = Path(directory) / "sshd.exe"
            sshd.write_bytes(b"")

            def runner(argv, timeout):
                if Path(argv[0]) == sshd:
                    return subprocess.CompletedProcess(
                        argv, 0,
                        "authorizedkeysfile __PROGRAMDATA__/ssh/administrators_authorized_keys\n", "",
                    )
                return subprocess.CompletedProcess(argv, 0, "DESKTOP\\admin\n", "")

            with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", return_value=sshd):
                target, is_admin = ssh_identity.authorized_keys_target(
                    system_name="windows", runner=runner,
                    home=Path(directory) / "home", program_data=Path(directory) / "ProgramData",
                )
            self.assertTrue(is_admin)
            self.assertEqual(target, Path(directory) / "ProgramData" / "ssh" / "administrators_authorized_keys")

    def test_windows_target_uses_name_sam_compatible_identity_for_sshd_match_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            sshd = Path(directory) / "sshd.exe"
            sshd.write_bytes(b"")
            seen = []

            def runner(argv, timeout):
                seen.append(tuple(argv))
                if Path(argv[0]) == sshd:
                    return subprocess.CompletedProcess(
                        argv, 0,
                        "authorizedkeysfile __PROGRAMDATA__/ssh/administrators_authorized_keys\n", "",
                    )
                return subprocess.CompletedProcess(argv, 0, "DOMAIN\\jdoe\n", "")

            with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", return_value=sshd):
                target, is_admin = ssh_identity.authorized_keys_target(
                    system_name="windows", runner=runner,
                    home=Path(directory) / "home", program_data=Path(directory) / "ProgramData",
                )

            sshd_argv = next(argv for argv in seen if Path(argv[0]) == sshd)
            self.assertEqual(sshd_argv[sshd_argv.index("-C") + 1], "user=DOMAIN\\jdoe,host=localhost,addr=127.0.0.1")
            self.assertTrue(is_admin)
            self.assertEqual(target, Path(directory) / "ProgramData" / "ssh" / "administrators_authorized_keys")

    def test_windows_rejects_unknown_effective_sshd_authorized_key_target(self):
        with tempfile.TemporaryDirectory() as directory:
            sshd = Path(directory) / "sshd.exe"
            sshd.write_bytes(b"")

            def runner(argv, timeout):
                if Path(argv[0]) == sshd:
                    return subprocess.CompletedProcess(
                        argv, 0, "authorizedkeysfile C:/unmanaged/authorized_keys\n", "",
                    )
                return subprocess.CompletedProcess(argv, 0, "DESKTOP\\admin\n", "")

            with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", return_value=sshd):
                with self.assertRaisesRegex(RuntimeError, "cannot safely determine"):
                    ssh_identity.authorized_keys_target(
                        system_name="windows", runner=runner,
                        home=Path(directory) / "home", program_data=Path(directory) / "ProgramData",
                    )

    def test_windows_uses_primary_file_from_standard_two_file_sshd_default(self):
        with tempfile.TemporaryDirectory() as directory:
            sshd = Path(directory) / "sshd.exe"
            sshd.write_bytes(b"")
            home = Path(directory) / "home"

            def runner(argv, timeout):
                if Path(argv[0]) == sshd:
                    return subprocess.CompletedProcess(
                        argv, 0, "authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2\n", "",
                    )
                return subprocess.CompletedProcess(argv, 0, "DESKTOP\\admin\n", "")

            with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", return_value=sshd):
                target, is_admin = ssh_identity.authorized_keys_target(
                    system_name="windows", runner=runner, home=home,
                    program_data=Path(directory) / "ProgramData",
                )

            self.assertEqual(target, home / ".ssh" / "authorized_keys")
            self.assertFalse(is_admin)

    def test_windows_existing_user_key_is_repaired_not_reported_as_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            target = home / ".ssh" / "authorized_keys"
            target.parent.mkdir(parents=True)
            target.write_text(key_line(27) + "\n", encoding="utf-8")
            sshd = Path(directory) / "sshd.exe"
            sshd.write_bytes(b"")

            def runner(argv, timeout):
                if Path(argv[0]) == sshd:
                    return subprocess.CompletedProcess(
                        argv, 0, "authorizedkeysfile .ssh/authorized_keys\n", "",
                    )
                rendered = " ".join(argv)
                if "WindowsIdentity]::GetCurrent().Name" in rendered:
                    return subprocess.CompletedProcess(argv, 0, "DESKTOP\\admin\n", "")
                if "Get-Acl" in rendered:
                    return subprocess.CompletedProcess(argv, 0, '{"key_present":true,"acl_safe":true}', "")
                return subprocess.CompletedProcess(argv, 0, "", "")

            with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", return_value=sshd):
                result = ssh_identity.install_gateway_public_key(
                    key_line(27), system_name="windows", runner=runner,
                    home=home, program_data=Path(directory) / "ProgramData",
                )

            self.assertEqual(result.target, target)
            self.assertTrue(result.changed)

    def test_windows_admin_key_verifies_after_elevation_without_child_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            sshd = Path(directory) / "sshd.exe"
            sshd.write_bytes(b"")

            def runner(argv, timeout):
                rendered = " ".join(argv)
                if Path(argv[0]) == sshd:
                    return subprocess.CompletedProcess(
                        argv, 0,
                        "authorizedkeysfile __PROGRAMDATA__/ssh/administrators_authorized_keys\n", "",
                    )
                if "WindowsIdentity]::GetCurrent().Name" in rendered:
                    return subprocess.CompletedProcess(argv, 0, "DESKTOP\\admin\n", "")
                if "Start-Process" in rendered:
                    self.assertIn(
                        r"-filepath 'c:\windows\system32\windowspowershell\v1.0\powershell.exe'",
                        rendered.lower(),
                    )
                    self.assertNotIn("attacker", rendered.lower())
                    self.assertNotIn("'-File'", rendered)
                    encoded = re.search(r"'-EncodedCommand','([^']+)'", rendered).group(1)
                    script = base64.b64decode(encoded).decode("utf-16le")
                    self.assertIn("$line='ssh-ed25519 ", script)
                    self.assertNotIn("$statusPath", script)
                    self.assertNotIn("Set-Content -LiteralPath $status", script)
                    self.assertIn("exit 12", script)
                    return subprocess.CompletedProcess(argv, 0, "", "")
                if "Get-Acl" in rendered:
                    self.fail("administrator verification must remain elevated")
                self.fail("unexpected command: %s" % rendered)

            with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", return_value=sshd), \
                    mock.patch.object(ssh_identity.shutil, "which", return_value=r"C:\\attacker\\powershell.exe"):
                result = ssh_identity.install_gateway_public_key(
                    key_line(29), system_name="windows", runner=runner,
                    home=Path(directory) / "home", program_data=Path(directory) / "ProgramData",
                )

            self.assertTrue(result.changed)
            self.assertTrue(result.target_verified)

    def test_windows_authorization_discovers_effective_target_elevated_when_hostkeys_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            attacker_sshd = Path(directory) / "attacker" / "sshd.exe"
            attacker_sshd.parent.mkdir()
            attacker_sshd.write_bytes(b"")
            trusted_sshd = Path(directory) / "System32" / "OpenSSH" / "sshd.exe"
            trusted_sshd.parent.mkdir(parents=True)
            trusted_sshd.write_bytes(b"")
            home = Path(directory) / "home"
            program_data = Path(directory) / "ProgramData"

            def runner(argv, timeout):
                rendered = " ".join(argv)
                if Path(argv[0]) == attacker_sshd:
                    return subprocess.CompletedProcess(argv, 1, "", "sshd: no hostkeys available -- exiting.\n")
                if "WindowsIdentity]::GetCurrent().Name" in rendered:
                    return subprocess.CompletedProcess(argv, 0, "DESKTOP\\caller\n", "")
                if "Start-Process" in rendered:
                    self.assertNotIn("'-File'", rendered)
                    encoded = re.search(r"'-EncodedCommand','([^']+)'", rendered).group(1)
                    script = base64.b64decode(encoded).decode("utf-16le")
                    self.assertIn("$config=& $sshd -T -C", script)
                    self.assertIn("$sshd='%s'" % trusted_sshd, script)
                    self.assertNotIn(str(attacker_sshd), script)
                    self.assertIn("$user='DESKTOP\\caller'", script)
                    self.assertNotIn("WindowsIdentity]::GetCurrent().Name", script)
                    self.assertIn("[Security.Principal.NTAccount]::new($user)", script)
                    self.assertIn("else{$callerSid}", script)
                    self.assertIn("$userTarget='%s'" % (home / ".ssh" / "authorized_keys"), script)
                    self.assertIn("$adminTargetPath='%s'" % (program_data / "ssh" / "administrators_authorized_keys"), script)
                    self.assertNotIn(str(Path(directory) / "poisoned"), script)
                    self.assertIn("__programdata__/ssh/administrators_authorized_keys", script.lower())
                    self.assertIn("exit 21", script)
                    return subprocess.CompletedProcess(argv, 21, "", "")
                self.fail("unexpected command: %s" % rendered)

            with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", return_value=attacker_sshd), \
                    mock.patch.object(ssh_identity, "_trusted_windows_sshd_executable", return_value=trusted_sshd), \
                    mock.patch.object(ssh_identity, "_trusted_windows_profile_directory", return_value=home), \
                    mock.patch.object(ssh_identity, "_trusted_windows_program_data_directory", return_value=program_data), \
                    mock.patch.dict(os.environ, {"USERPROFILE": str(Path(directory) / "poisoned"), "PROGRAMDATA": str(Path(directory) / "poisoned")}, clear=False):
                result = ssh_identity.install_gateway_public_key(key_line(31), system_name="windows", runner=runner)

            self.assertEqual(
                result.target, program_data / "ssh" / "administrators_authorized_keys",
            )
            self.assertTrue(result.administrator_target)
            self.assertTrue(result.target_verified)

    def test_windows_authorization_fails_closed_when_acl_verification_finds_extra_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            sshd = Path(directory) / "sshd.exe"
            sshd.write_bytes(b"")

            def runner(argv, timeout):
                rendered = " ".join(argv)
                if Path(argv[0]) == sshd:
                    return subprocess.CompletedProcess(
                        argv, 0,
                        "authorizedkeysfile __PROGRAMDATA__/ssh/administrators_authorized_keys\n", "",
                    )
                if "WindowsIdentity]::GetCurrent().Name" in rendered:
                    return subprocess.CompletedProcess(argv, 0, "DESKTOP\\admin\n", "")
                if "Start-Process" in rendered:
                    self.assertNotIn("'-File'", rendered)
                    return subprocess.CompletedProcess(argv, 12, "", "")
                if "Get-Acl" in rendered:
                    self.fail("administrator verification must remain elevated")
                self.fail("unexpected command: %s" % rendered)

            with mock.patch.object(ssh_identity, "resolve_ssh_client_executable", return_value=sshd):
                with self.assertRaisesRegex(RuntimeError, "ACL verification"):
                    ssh_identity.install_gateway_public_key(
                        key_line(30), system_name="windows", runner=runner,
                        home=Path(directory) / "home", program_data=Path(directory) / "ProgramData",
                    )

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
        with mock.patch.object(ssh_identity.shutil, "which", return_value=r"C:\\attacker\\powershell.exe"):
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
        self.assertIn(
            r"-filepath 'c:\windows\system32\windowspowershell\v1.0\powershell.exe'",
            elevated.lower(),
        )
        self.assertNotIn("attacker", elevated.lower())
        self.assertNotIn("'-File'", elevated)
        encoded = re.search(r"'-EncodedCommand','([^']+)'", elevated).group(1)
        script = base64.b64decode(encoded).decode("utf-16le")
        self.assertIn("Add-WindowsCapability", script)
        self.assertNotIn("Get-Content -LiteralPath", script)

    def test_managed_identity_returns_path_only_when_pair_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "id"
            self.assertIsNone(ssh_identity.managed_identity_file(private))
            private.write_text("SECRET", encoding="utf-8")
            Path(str(private) + ".pub").write_text(key_line(11), encoding="utf-8")
            self.assertEqual(ssh_identity.managed_identity_file(private), private)


if __name__ == "__main__":
    unittest.main()
