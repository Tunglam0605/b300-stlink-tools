from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from scripts.release.verify_published import verify_once, verify_with_retry


VERSION = "1.2.3"
BASE = "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v%s/" % VERSION
MANIFEST_URL = "https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/latest.json"
SIGNATURE_URL = MANIFEST_URL + ".minisig"
EXPECTED_UPDATE_FILES = {
    "windows-x64": "B300-STLink-GUI-Windows-x64.exe",
    "linux-x64-appimage": "B300-STLink-GUI-Ubuntu-x64.AppImage",
    "linux-x64-deb": "b300-stlink-gui_amd64.deb",
    "linux-arm64-appimage": "B300-STLink-GUI-Ubuntu-arm64.AppImage",
    "linux-arm64-deb": "b300-stlink-gui_arm64.deb",
    "windows-x64-cli": "B300-STLink-CLI-Windows-x64.zip",
    "linux-x64-cli": "B300-STLink-CLI-Linux-x64.tar.gz",
    "linux-arm64-cli": "B300-STLink-CLI-Linux-arm64.tar.gz",
}


def manifest_bytes(platforms=None) -> bytes:
    selected = platforms or EXPECTED_UPDATE_FILES
    value = {
        "notes": "release",
        "platforms": {
            platform: {
                "file": filename,
                "sha256": "a" * 64,
                "size": 123,
                "url": BASE + filename,
            }
            for platform, filename in selected.items()
        },
        "product": "B300 ST-Link Tools",
        "published_at": "2026-08-28T01:00:00Z",
        "release_page": "https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v%s" % VERSION,
        "schema_version": 1,
        "version": VERSION,
    }
    return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")


class PublishedReleaseVerifierTests(unittest.TestCase):
    def test_signed_manifest_and_every_update_asset_are_verified(self) -> None:
        manifest = manifest_bytes()
        probed = []
        commands = []

        def fetch(url: str, timeout: float) -> bytes:
            self.assertEqual(timeout, 2.0)
            return manifest if url == MANIFEST_URL else b"signature"

        def probe(url: str, timeout: float) -> None:
            probed.append(url)

        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        verify_once(
            version=VERSION,
            manifest_url=MANIFEST_URL,
            signature_url=SIGNATURE_URL,
            minisign=Path("minisign"),
            public_key="PUBLIC",
            timeout=2.0,
            fetch=fetch,
            probe=probe,
            run_command=run,
        )

        self.assertEqual(len(commands), 1)
        self.assertIn("-Vm", commands[0])
        self.assertEqual(set(probed), {BASE + name for name in EXPECTED_UPDATE_FILES.values()})

    def test_signature_failure_is_fail_closed_before_asset_probe(self) -> None:
        probed = []
        with self.assertRaisesRegex(ValueError, "signature is invalid"):
            verify_once(
                version=VERSION,
                manifest_url=MANIFEST_URL,
                signature_url=SIGNATURE_URL,
                minisign=Path("minisign"),
                public_key="PUBLIC",
                timeout=2.0,
                fetch=lambda url, timeout: manifest_bytes() if url == MANIFEST_URL else b"bad",
                probe=lambda url, timeout: probed.append(url),
                run_command=lambda command, **kwargs: subprocess.CompletedProcess(
                    command, 1, stdout="", stderr="bad signature"
                ),
            )
        self.assertEqual(probed, [])

    def test_missing_platform_is_rejected_even_with_valid_signature(self) -> None:
        subset = dict(EXPECTED_UPDATE_FILES)
        subset.pop(next(iter(subset)))
        with self.assertRaisesRegex(ValueError, "platform set mismatch"):
            verify_once(
                version=VERSION,
                manifest_url=MANIFEST_URL,
                signature_url=SIGNATURE_URL,
                minisign=Path("minisign"),
                public_key="PUBLIC",
                timeout=2.0,
                fetch=lambda url, timeout: manifest_bytes(subset) if url == MANIFEST_URL else b"sig",
                probe=lambda url, timeout: None,
                run_command=lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
            )

    def test_retry_handles_release_cdn_propagation_then_succeeds(self) -> None:
        attempts = []

        def fetch(url: str, timeout: float) -> bytes:
            if url == MANIFEST_URL:
                attempts.append(url)
                if len(attempts) == 1:
                    raise OSError("not propagated")
                return manifest_bytes()
            return b"sig"

        with mock.patch("scripts.release.verify_published.time.sleep") as sleep:
            verify_with_retry(
                version=VERSION,
                manifest_url=MANIFEST_URL,
                signature_url=SIGNATURE_URL,
                minisign=Path("minisign"),
                public_key="PUBLIC",
                timeout=2.0,
                attempts=3,
                delay=0.1,
                fetch=fetch,
                probe=lambda url, timeout: None,
                run_command=lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
            )
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once_with(0.1)


if __name__ == "__main__":
    unittest.main()
