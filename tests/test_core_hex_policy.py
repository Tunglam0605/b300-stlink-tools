from __future__ import annotations

import hashlib
import tempfile
import unittest
import zlib
from pathlib import Path

from b300_core.hex_image import inspect_image
from b300_core.models import ProbeRef, TargetInfo
from b300_core.policy import build_flash_plan, build_flash_preview


APPLICATION_VECTOR = (
    (0x20020000).to_bytes(4, "little") +
    (0x08010101).to_bytes(4, "little")
)
LITERAL_APPLICATION_HEX = (
    ":020000040801F1\n"
    ":080000000000022001010108CB\n"
    ":00000001FF\n"
)


def hex_record(address: int, record_type: int, payload: bytes) -> str:
    body = bytes([
        len(payload),
        (address >> 8) & 0xFF,
        address & 0xFF,
        record_type,
    ]) + payload
    return ":" + (body + bytes([(-sum(body)) & 0xFF])).hex().upper()


def write_hex(root: str, address: int, payload: bytes) -> Path:
    upper = (address >> 16).to_bytes(2, "big")
    lines = [
        hex_record(0, 4, upper),
        hex_record(address & 0xFFFF, 0, payload),
        hex_record(0, 1, b""),
    ]
    path = Path(root) / "application.hex"
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


class HexImagePolicyTests(unittest.TestCase):
    def test_inspect_image_extracts_application_vector_from_literal_hex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "application.hex"
            path.write_text(LITERAL_APPLICATION_HEX, encoding="ascii")

            info = inspect_image(path)

        self.assertEqual(info.initial_msp, 0x20020000)
        self.assertEqual(info.reset_vector, 0x08010101)

    def test_inspect_image_rejects_incomplete_or_invalid_application_vector(self) -> None:
        fixtures = {
            "incomplete": b"\x00\x00\x02\x20",
            "invalid": (0x10020000).to_bytes(4, "little") +
                       (0x08010101).to_bytes(4, "little"),
        }
        for label, payload in fixtures.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                path = write_hex(directory, 0x08010000, payload)
                with self.assertRaisesRegex(ValueError, "Application vector"):
                    inspect_image(path)

    def test_inspect_image_returns_hash_and_application_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest().upper()
            info = inspect_image(path)

        self.assertEqual(info.start_address, 0x08010000)
        self.assertEqual(info.end_address, 0x08010007)
        self.assertEqual(info.size, 8)
        self.assertEqual(info.data_record_count, 1)
        self.assertEqual(info.sha256, expected_hash)
        self.assertEqual(info.flash_span_size, 8)
        self.assertEqual(info.flash_crc32, zlib.crc32(APPLICATION_VECTOR) & 0xFFFFFFFF)

    def test_inspect_image_rejects_protected_bootloader_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_hex(directory, 0x08000000, b"\x00")
            with self.assertRaisesRegex(ValueError, "protected range"):
                inspect_image(path)

    def test_inspect_image_rejects_gap_before_application_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_hex(directory, 0x08010010, b"\x00")
            with self.assertRaisesRegex(ValueError, "must start at 0x08010000"):
                inspect_image(path)

    def test_build_plan_is_fixed_to_sectors_three_through_seven(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info = inspect_image(write_hex(directory, 0x08010000, APPLICATION_VECTOR))
            target = TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True)
            plan = build_flash_plan(info, ProbeRef(serial="ABC123"), target)

        self.assertEqual(plan.erase_sectors, (3, 4, 5, 6, 7))
        self.assertEqual(plan.probe.serial, "ABC123")
        self.assertEqual(plan.image, info)
        self.assertEqual(plan.target, target)

    def test_flash_plan_rejects_unsupported_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info = inspect_image(write_hex(directory, 0x08010000, APPLICATION_VECTOR))
            wrong_target = TargetInfo(0x10006419, 2048, 3.09, "not protected")
            with self.assertRaisesRegex(ValueError, "(?i)unsupported target"):
                build_flash_plan(info, ProbeRef("ABC123"), wrong_target)

    def test_offline_preview_is_not_a_flash_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info = inspect_image(write_hex(directory, 0x08010000, APPLICATION_VECTOR))
            preview = build_flash_preview(info, ProbeRef("ABC123"))
        self.assertEqual(preview.erase_sectors, (3, 4, 5, 6, 7))
        self.assertFalse(hasattr(preview, "target"))

    def test_sparse_image_size_counts_payload_not_address_span(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sparse.hex"
            path.write_text("\n".join([
                hex_record(0, 4, (0x0801).to_bytes(2, "big")),
                hex_record(0x0000, 0, APPLICATION_VECTOR),
                hex_record(0x0010, 0, b"\xBB"),
                hex_record(0, 1, b""),
            ]) + "\n", encoding="ascii")
            info = inspect_image(path)
        self.assertEqual(info.start_address, 0x08010000)
        self.assertEqual(info.end_address, 0x08010010)
        self.assertEqual(info.size, 9)
        self.assertEqual(info.flash_span_size, 17)
        canonical = APPLICATION_VECTOR + (b"\xFF" * 8) + b"\xBB"
        self.assertEqual(info.flash_crc32, zlib.crc32(canonical) & 0xFFFFFFFF)


    def test_plan_rejects_readout_protected_target_without_modifying_rdp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info = inspect_image(write_hex(directory, 0x08010000, APPLICATION_VECTOR))
            target = TargetInfo(
                0x101F6413, 512, 3.09, "S0-S2 protected",
                (0, 1, 2), True, True,
            )
            with self.assertRaisesRegex(ValueError, "readout protection"):
                build_flash_plan(info, ProbeRef(serial="ABC123"), target)


    def test_conflicting_duplicate_hex_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate-conflict.hex"
            path.write_text("\n".join([
                hex_record(0, 4, (0x0801).to_bytes(2, "big")),
                hex_record(0x0000, 0, APPLICATION_VECTOR),
                hex_record(0x0000, 0, APPLICATION_VECTOR[:4] +
                           bytes([APPLICATION_VECTOR[4] ^ 0x02]) + APPLICATION_VECTOR[5:]),
                hex_record(0, 1, b""),
            ]) + "\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, r"conflicting data at 0x08010004"):
                inspect_image(path)



if __name__ == "__main__":
    unittest.main()
