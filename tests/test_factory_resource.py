from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b300_core.factory_resource import (
    TRUSTED_BOOTLOADER_SHA256,
    load_trusted_bootloader,
)


EXPECTED_SHA256 = "657F71605E00795BEA3C5601AAF569104E74D9DEE8D5B6E602514C4D72264F05"
EXPECTED_COMMIT = "92e70f8e1cc94c17be39034fcc9a20e385325a2f"


class FactoryResourceTests(unittest.TestCase):
    def test_bundled_bootloader_matches_audited_artifact_and_provenance(self) -> None:
        trusted = load_trusted_bootloader()
        self.assertEqual(TRUSTED_BOOTLOADER_SHA256, EXPECTED_SHA256)
        self.assertEqual(trusted.image.sha256, EXPECTED_SHA256)
        self.assertEqual(trusted.image.start_address, 0x08000000)
        self.assertEqual(trusted.image.end_address, 0x08004B4F)
        self.assertEqual(trusted.source_commit, EXPECTED_COMMIT)
        self.assertEqual(
            trusted.source_path,
            "firmware/bootloader/BOOTLOAER/bootloader_std.hex",
        )
        self.assertEqual(trusted.firmware_version, "0x00050001")
        self.assertEqual(trusted.board_token, "B300_F407ZE")
        self.assertEqual(trusted.transport, "COM3")

    def test_resource_loader_fails_closed_when_artifact_hash_changes(self) -> None:
        trusted = load_trusted_bootloader()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads(trusted.manifest_path.read_text(encoding="utf-8"))
            (root / manifest["artifact"]).write_bytes(
                trusted.image.path.read_bytes() + b"\n"
            )
            (root / "b300_bootloader_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_trusted_bootloader(root)

    def test_resource_loader_fails_closed_when_manifest_provenance_changes(self) -> None:
        trusted = load_trusted_bootloader()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads(trusted.manifest_path.read_text(encoding="utf-8"))
            (root / manifest["artifact"]).write_bytes(trusted.image.path.read_bytes())
            manifest["source"]["commit"] = "0" * 40
            (root / "b300_bootloader_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "provenance"):
                load_trusted_bootloader(root)


if __name__ == "__main__":
    unittest.main()
