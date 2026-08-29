from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core.factory_resource import (
    TRUSTED_BOOTLOADER_SHA256,
    load_trusted_bootloader,
)


EXPECTED_ARTIFACT = "b300_bootloader_f407ze_com3_v00060500.hex"
EXPECTED_SHA256 = "085E44E8339D21EE2D136D11F86C2103295812CB2438807774B232647D3F75A1"
EXPECTED_COMMIT = "88b74f649497a5ea9c64b5394470407678795f42"
EXPECTED_BLOB = "51381f26edf343cee3054d0641dd65f5a2ee6f89"
EXPECTED_SOURCE_RAW_SHA256 = "E89FF64430EE1CA4F4CC4D66BA85A9AFFD1F3DB3511860A83191B3FDC07AFA51"
EXPECTED_TRANSFORMATION = {
    "name": "lf_to_crlf",
    "canonical_line_ending": "LF",
    "artifact_line_ending": "CRLF",
}


class FactoryResourceTests(unittest.TestCase):
    def test_bundled_bootloader_reverses_declared_crlf_transform_to_pinned_source(self) -> None:
        trusted = load_trusted_bootloader()
        self.assertEqual(TRUSTED_BOOTLOADER_SHA256, EXPECTED_SHA256)
        self.assertEqual(trusted.image.path.name, EXPECTED_ARTIFACT)
        self.assertEqual(trusted.image.sha256, EXPECTED_SHA256)
        self.assertEqual(trusted.image.start_address, 0x08000000)
        self.assertEqual(trusted.image.end_address, 0x08004BA3)
        self.assertEqual(trusted.image.size, 19364)
        self.assertEqual(trusted.source_commit, EXPECTED_COMMIT)
        self.assertEqual(trusted.source_git_blob, EXPECTED_BLOB)
        self.assertEqual(trusted.source_git_object_size, 53308)
        self.assertEqual(trusted.source_raw_sha256, EXPECTED_SOURCE_RAW_SHA256)
        self.assertEqual(trusted.artifact_transformation, EXPECTED_TRANSFORMATION)
        self.assertEqual(
            trusted.source_path,
            "firmware/bootloader/BOOTLOAER/bootloader_std.hex",
        )
        self.assertEqual(trusted.firmware_version, "0x00060500")
        self.assertEqual(trusted.protocol_version, "0x00030000")
        self.assertEqual(trusted.board_token, "B300_F407ZE")
        self.assertEqual(trusted.transport, "COM3")

        artifact = trusted.image.path.read_bytes()
        self.assertTrue(artifact.endswith(b"\r\n"))
        self.assertNotIn(b"\n", artifact.replace(b"\r\n", b""))
        self.assertNotIn(b"\r", artifact.replace(b"\r\n", b""))
        canonical = artifact.replace(b"\r\n", b"\n")
        self.assertEqual(len(canonical), 53308)
        self.assertEqual(hashlib.sha256(canonical).hexdigest().upper(),
                         EXPECTED_SOURCE_RAW_SHA256)
        git_object = b"blob %d\0" % len(canonical) + canonical
        self.assertEqual(hashlib.sha1(git_object).hexdigest(), EXPECTED_BLOB)
        self.assertEqual(artifact.split(b"\r\n")[:-1], canonical.split(b"\n")[:-1])

    def test_manifest_pins_v6500_appmeta_contract(self) -> None:
        trusted = load_trusted_bootloader()
        manifest = json.loads(trusted.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("app_metadata"), {
            "address": "0x0800C000",
            "size": 44,
            "format_version": 1,
            "ota_magic": "0x4F54414D",
            "stlink_magic": "0x53544C4D",
            "stlink_initial_state": {"name": "VERIFIED", "value": 2},
        })

    def test_loader_rejects_old_name_sha_commit_blob_version_range_or_size(self) -> None:
        trusted = load_trusted_bootloader()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads(trusted.manifest_path.read_text(encoding="utf-8"))
            (root / manifest["artifact"]).write_bytes(
                trusted.image.path.read_bytes()
            )
            mutations = (
                ("artifact", "b300_bootloader_f407ze_com3_v00050001.hex"),
                ("sha256", "657F71605E00795BEA3C5601AAF569104E74D9DEE8D5B6E602514C4D72264F05"),
                ("source.commit", "92e70f8e1cc94c17be39034fcc9a20e385325a2f"),
                ("source.git_blob", "b4e5be928a7524d566564b1b2b980ce854bfe68f"),
                ("source.raw_sha256", "0" * 64),
                ("source.raw_size", 53307),
                ("source.artifact_transformation.name", "identity"),
                ("profile.firmware_version", "0x00050001"),
                ("observed_data_range.end", "0x08004B4F"),
                ("observed_data_range.data_bytes", 19280),
            )
            for field, value in mutations:
                with self.subTest(field=field):
                    candidate = json.loads(json.dumps(manifest))
                    path = field.split(".")
                    parent = candidate
                    for key in path[:-1]:
                        parent = parent[key]
                    parent[path[-1]] = value
                    (root / "b300_bootloader_manifest.json").write_text(
                        json.dumps(candidate), encoding="utf-8"
                    )
                    with self.assertRaisesRegex(ValueError, "provenance"):
                        load_trusted_bootloader(root)

    def test_loader_rejects_malformed_artifact_line_endings_even_with_matching_artifact_sha(self) -> None:
        trusted = load_trusted_bootloader()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads(trusted.manifest_path.read_text(encoding="utf-8"))
            malformed = trusted.image.path.read_bytes().replace(b"\r\n", b"\n", 1)
            digest = hashlib.sha256(malformed).hexdigest().upper()
            manifest["sha256"] = digest
            (root / manifest["artifact"]).write_bytes(malformed)
            (root / "b300_bootloader_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with mock.patch("b300_core.factory_resource.TRUSTED_BOOTLOADER_SHA256", digest):
                with self.assertRaisesRegex(ValueError, "line ending"):
                    load_trusted_bootloader(root)

    def test_loader_recomputes_canonical_raw_sha_and_git_blob_after_reverse_transform(self) -> None:
        trusted = load_trusted_bootloader()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads(trusted.manifest_path.read_text(encoding="utf-8"))
            lines = trusted.image.path.read_bytes().split(b"\r\n")
            pair = next(
                index for index in range(len(lines) - 2)
                if lines[index][7:9] == b"00" and lines[index + 1][7:9] == b"00"
            )
            lines[pair], lines[pair + 1] = lines[pair + 1], lines[pair]
            reordered = b"\r\n".join(lines)
            digest = hashlib.sha256(reordered).hexdigest().upper()
            manifest["sha256"] = digest
            (root / manifest["artifact"]).write_bytes(reordered)
            (root / "b300_bootloader_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with mock.patch("b300_core.factory_resource.TRUSTED_BOOTLOADER_SHA256", digest):
                with self.assertRaisesRegex(ValueError, "canonical source"):
                    load_trusted_bootloader(root)

    def test_loader_rejects_legacy_stp1_or_bkp4r_manifest_fields(self) -> None:
        trusted = load_trusted_bootloader()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads(trusted.manifest_path.read_text(encoding="utf-8"))
            (root / manifest["artifact"]).write_bytes(trusted.image.path.read_bytes())
            for field, value in (("legacy_stp1", "0x31505453"), ("bkp4r", "0x40002860")):
                with self.subTest(field=field):
                    candidate = json.loads(json.dumps(manifest))
                    candidate.setdefault("app_metadata", {})[field] = value
                    (root / "b300_bootloader_manifest.json").write_text(
                        json.dumps(candidate), encoding="utf-8"
                    )
                    with self.assertRaisesRegex(ValueError, "provenance"):
                        load_trusted_bootloader(root)


if __name__ == "__main__":
    unittest.main()
