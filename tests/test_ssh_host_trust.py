from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from b300_core.ssh_host_trust import (
    expected_known_hosts_field, local_gateway_host_key, scan_gateway_host_key,
    trust_gateway_host_key, trusted_known_hosts_file,
)
from tests.test_ssh_identity import key_line


class SshHostTrustTests(unittest.TestCase):
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
