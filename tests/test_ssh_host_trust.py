from __future__ import annotations

import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core.ssh_host_trust import (
    expected_known_hosts_field, local_gateway_host_key, scan_gateway_host_key,
    trust_gateway_host_key, trusted_known_hosts_file, validate_gateway_host,
)
from tests.test_ssh_identity import key_line


class SshHostTrustTests(unittest.TestCase):
    def _openssh_option_value(self, argv, name):
        encoded = next(
            argv[index + 1]
            for index, item in enumerate(argv[:-1])
            if item == "-o" and argv[index + 1].startswith(name + "=")
        )
        parsed = shlex.split(encoded, posix=True)
        self.assertEqual(
            len(parsed), 1,
            "OpenSSH config parsing split the option value on whitespace",
        )
        key, separator, value = parsed[0].partition("=")
        self.assertEqual((key, separator), (name, "="))
        return value

    def test_gateway_host_rejects_option_like_value(self):
        with self.assertRaisesRegex(ValueError, "unsupported characters"):
            validate_gateway_host("-Fattacker-config")

    def test_expected_host_field_uses_brackets_only_for_custom_port(self):
        self.assertEqual(expected_known_hosts_field("gateway.local", 22), "gateway.local")
        self.assertEqual(expected_known_hosts_field("gateway.local", 2222), "[gateway.local]:2222")

    def test_scan_accepts_one_ed25519_key_and_returns_fingerprint(self):
        public = key_line(81).split()
        output = "gateway.local %s %s\n" % (public[0], public[1])
        def runner(argv, timeout):
            self.assertEqual(argv[-1], "gateway.local")
            self.assertIn("ed25519", argv)
            return subprocess.CompletedProcess(argv, 0, output, "")
        scanned = scan_gateway_host_key("gateway.local", 22, runner=runner, executable="ssh-keyscan")
        self.assertEqual(scanned.host_field, "gateway.local")
        self.assertTrue(scanned.fingerprint.startswith("SHA256:"))
        self.assertTrue(scanned.public_key.startswith("ssh-ed25519 "))

    def test_scan_uses_passwordless_ssh_fallback_for_incompatible_keyscan_kex(self):
        public = key_line(96, "gateway-modern-openssh").split()
        calls = []

        def runner(argv, timeout):
            calls.append(tuple(argv))
            if argv[0] == "ssh-keyscan":
                return subprocess.CompletedProcess(
                    argv, 1, "",
                    "choose_kex: unsupported KEX method sntrup761x25519-sha512@openssh.com",
                )
            known_hosts = Path(self._openssh_option_value(argv, "UserKnownHostsFile"))
            known_hosts.write_text(
                "gateway.local %s %s\n" % (public[0], public[1]),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(argv, 255, "", "Permission denied")

        with mock.patch(
            "b300_core.ssh_host_trust.resolve_ssh_client_executable",
            side_effect=lambda name: Path(name),
        ):
            try:
                scanned = scan_gateway_host_key(
                    "gateway.local", 22, runner=runner, executable="ssh-keyscan",
                )
            except RuntimeError as error:
                self.fail("safe SSH host-key fallback was not used: %s" % error)

        self.assertEqual(scanned.public_key, "%s %s" % (public[0], public[1]))
        self.assertTrue(scanned.fingerprint.startswith("SHA256:"))
        self.assertEqual(len(calls), 2)
        ssh_call = calls[1]
        self.assertIn("-F", ssh_call)
        self.assertEqual(ssh_call[ssh_call.index("-F") + 1], "none")
        self.assertIn("-n", ssh_call)
        self.assertIn("-l", ssh_call)
        self.assertEqual(
            ssh_call[ssh_call.index("-l") + 1],
            "b300-host-key-scan-invalid",
        )
        for option in (
            "BatchMode=yes",
            "PreferredAuthentications=none",
            "PasswordAuthentication=no",
            "KbdInteractiveAuthentication=no",
            "PubkeyAuthentication=no",
            "HostbasedAuthentication=no",
            "GSSAPIAuthentication=no",
            "IdentityAgent=none",
            "IdentityFile=none",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "ControlMaster=no",
            "ControlPath=none",
            "StrictHostKeyChecking=accept-new",
            "HashKnownHosts=no",
            "HostKeyAlgorithms=ssh-ed25519",
            "KexAlgorithms=curve25519-sha256",
        ):
            with self.subTest(option=option):
                self.assertIn(option, ssh_call)

    def test_ssh_fallback_quotes_known_hosts_path_with_spaces_for_openssh(self):
        public = key_line(97, "gateway-spaced-temp").split()

        def runner(argv, timeout):
            if argv[0] == "ssh-keyscan":
                return subprocess.CompletedProcess(
                    argv, 1, "", "choose_kex: unsupported KEX method test-kex",
                )
            known_hosts = Path(self._openssh_option_value(argv, "UserKnownHostsFile"))
            known_hosts.write_text(
                "[gateway.local]:2222 %s %s\n" % (public[0], public[1]),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(argv, 255, "", "Permission denied")

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory) / "temporary root with spaces"
            temp_root.mkdir()
            with mock.patch.object(tempfile, "tempdir", str(temp_root)), mock.patch(
                "b300_core.ssh_host_trust.resolve_ssh_client_executable",
                side_effect=lambda name: Path(name),
            ):
                scanned = scan_gateway_host_key(
                    "gateway.local", 2222, runner=runner, executable="ssh-keyscan",
                )

        self.assertEqual(scanned.host_field, "[gateway.local]:2222")
        self.assertEqual(scanned.public_key, "%s %s" % (public[0], public[1]))

    def test_scan_does_not_use_ssh_fallback_for_other_keyscan_failures(self):
        calls = []

        def runner(argv, timeout):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 1, "", "Connection refused")

        with mock.patch(
            "b300_core.ssh_host_trust.resolve_ssh_client_executable",
            side_effect=AssertionError("SSH fallback must not be resolved"),
        ):
            with self.assertRaisesRegex(RuntimeError, "No ssh-ed25519 host key"):
                scan_gateway_host_key(
                    "gateway.local", 22, runner=runner, executable="ssh-keyscan",
                )

        self.assertEqual(len(calls), 1)

    def test_scan_rejects_multiple_different_keys(self):
        first = key_line(82).split()
        second = key_line(83).split()
        output = (
            "gateway.local %s %s\n" % (first[0], first[1]) +
            "gateway.local %s %s\n" % (second[0], second[1])
        )
        runner = lambda argv, timeout: subprocess.CompletedProcess(argv, 0, output, "")
        with self.assertRaisesRegex(RuntimeError, "multiple different"):
            scan_gateway_host_key("gateway.local", runner=runner, executable="ssh-keyscan")

    def test_trust_is_idempotent_and_returns_managed_file_for_matching_host(self):
        public = key_line(84)
        scanned = type("Scan", (), {
            "host": "gateway.local", "port": 22, "host_field": "gateway.local",
            "public_key": public, "fingerprint": "SHA256:TEST",
        })()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "b300_known_hosts"
            first = trust_gateway_host_key(scanned, known_hosts_file=target)
            second = trust_gateway_host_key(scanned, known_hosts_file=target)
            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertEqual(trusted_known_hosts_file("gateway.local", 22, known_hosts_file=target), target)
            self.assertIsNone(trusted_known_hosts_file("other.local", 22, known_hosts_file=target))

    def test_conflicting_existing_host_key_fails_closed_and_is_not_overwritten(self):
        old = key_line(85)
        new = key_line(86)
        scanned = type("Scan", (), {
            "host": "gateway.local", "port": 22, "host_field": "gateway.local",
            "public_key": new, "fingerprint": "SHA256:NEW",
        })()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "b300_known_hosts"
            parts = old.split()
            target.write_text("gateway.local %s %s\n" % (parts[0], parts[1]), encoding="utf-8")
            before = target.read_text(encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "HOST_KEY_CONFLICT"):
                trust_gateway_host_key(scanned, known_hosts_file=target)
            self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_local_gateway_host_key_reads_public_key_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ssh"
            root.mkdir()
            public = key_line(87, "gateway-host")
            (root / "ssh_host_ed25519_key.pub").write_text(public + "\n", encoding="utf-8")
            result = local_gateway_host_key(
                system_name="linux", etc_ssh=root, keyscan_executable="",
            )
            self.assertTrue(result.public_key.startswith("ssh-ed25519 "))
            self.assertTrue(result.fingerprint.startswith("SHA256:"))
            self.assertNotIn("PRIVATE", repr(result))

    def test_local_gateway_host_key_prefers_loopback_scan_without_programdata_read(self):
        public = key_line(88, "gateway-loopback").split()
        output = "localhost %s %s\n" % (public[0], public[1])
        calls = []

        def runner(argv, timeout):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, output, "")

        with tempfile.TemporaryDirectory() as directory:
            # Deliberately do not create ProgramData/ssh or any public-key file.
            result = local_gateway_host_key(
                system_name="windows", program_data=Path(directory),
                runner=runner, keyscan_executable="ssh-keyscan",
            )
        self.assertTrue(calls)
        self.assertEqual(calls[0][-1], "localhost")
        self.assertTrue(result.public_key.startswith("ssh-ed25519 "))
        self.assertTrue(result.fingerprint.startswith("SHA256:"))

    def test_local_gateway_host_key_falls_back_to_public_file_when_loopback_scan_fails(self):
        def runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 1, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ssh"
            root.mkdir()
            public = key_line(89, "gateway-file-fallback")
            (root / "ssh_host_ed25519_key.pub").write_text(public + "\n", encoding="utf-8")
            result = local_gateway_host_key(
                system_name="windows", program_data=Path(directory),
                runner=runner, keyscan_executable="ssh-keyscan",
            )
        self.assertTrue(result.public_key.startswith("ssh-ed25519 "))
        self.assertTrue(result.fingerprint.startswith("SHA256:"))

    def test_local_gateway_host_key_scans_the_requested_custom_port(self):
        public = key_line(90, "gateway-custom-port").split()
        output = "[localhost]:2222 %s %s\n" % (public[0], public[1])
        calls = []

        def runner(argv, timeout):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, output, "")

        result = local_gateway_host_key(
            port=2222, system_name="windows", runner=runner,
            keyscan_executable="ssh-keyscan",
        )
        self.assertEqual(
            calls,
            [("ssh-keyscan", "-T", "5", "-p", "2222", "-t", "ed25519", "localhost")],
        )
        self.assertEqual(result.port, 2222)
        self.assertEqual(result.host_field, "[localhost]:2222")

    def test_local_gateway_host_key_does_not_fallback_on_ambiguous_scan(self):
        first = key_line(91, "gateway-first").split()
        second = key_line(92, "gateway-second").split()
        output = (
            "localhost %s %s\n" % (first[0], first[1]) +
            "localhost %s %s\n" % (second[0], second[1])
        )

        def runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 0, output, "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ssh"
            root.mkdir()
            (root / "ssh_host_ed25519_key.pub").write_text(
                key_line(93, "fallback-must-not-win") + "\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "multiple different"):
                local_gateway_host_key(
                    system_name="windows", program_data=Path(directory),
                    runner=runner, keyscan_executable="ssh-keyscan",
                )

    def test_local_gateway_host_key_does_not_fallback_on_malformed_scan(self):
        def runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 0, "localhost malformed\n", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ssh"
            root.mkdir()
            (root / "ssh_host_ed25519_key.pub").write_text(
                key_line(94, "fallback-must-not-win") + "\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "malformed|untrusted"):
                local_gateway_host_key(
                    system_name="windows", program_data=Path(directory),
                    runner=runner, keyscan_executable="ssh-keyscan",
                )

    def test_local_gateway_host_key_falls_back_when_scanner_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ssh"
            root.mkdir()
            public = key_line(95, "gateway-no-scanner")
            (root / "ssh_host_ed25519_key.pub").write_text(public + "\n", encoding="utf-8")
            result = local_gateway_host_key(
                system_name="windows", program_data=Path(directory),
                keyscan_executable="",
            )
        self.assertEqual(result.public_key, public)
        self.assertTrue(result.fingerprint.startswith("SHA256:"))

    def test_local_gateway_host_key_reports_scanner_and_public_file_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                    RuntimeError, "ssh-keyscan is unavailable.*public-key file fallback failed"):
                local_gateway_host_key(
                    system_name="windows", program_data=Path(directory),
                    keyscan_executable="",
                )


if __name__ == "__main__":
    unittest.main()
