from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from b300_core.hex_image import inspect_image
from b300_core.models import ProbeRef, TargetInfo
from b300_core.policy import build_flash_plan, build_flash_preview


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
    def test_inspect_image_returns_hash_and_application_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_hex(directory, 0x08010000, b"\x01\x02")
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest().upper()
            info = inspect_image(path)

        self.assertEqual(info.start_address, 0x08010000)
        self.assertEqual(info.end_address, 0x08010001)
        self.assertEqual(info.size, 2)
        self.assertEqual(info.data_record_count, 1)
        self.assertEqual(info.sha256, expected_hash)

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
            info = inspect_image(write_hex(directory, 0x08010000, b"\xAA"))
            target = TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected")
            plan = build_flash_plan(info, ProbeRef(serial="ABC123"), target)

        self.assertEqual(plan.erase_sectors, (3, 4, 5, 6, 7))
        self.assertEqual(plan.probe.serial, "ABC123")
        self.assertEqual(plan.image, info)
        self.assertEqual(plan.target, target)

    def test_flash_plan_rejects_unsupported_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info = inspect_image(write_hex(directory, 0x08010000, b"\xAA"))
            wrong_target = TargetInfo(0x10006419, 2048, 3.09, "not protected")
            with self.assertRaisesRegex(ValueError, "(?i)unsupported target"):
                build_flash_plan(info, ProbeRef("ABC123"), wrong_target)

    def test_offline_preview_is_not_a_flash_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info = inspect_image(write_hex(directory, 0x08010000, b"\xAA"))
            preview = build_flash_preview(info, ProbeRef("ABC123"))
        self.assertEqual(preview.erase_sectors, (3, 4, 5, 6, 7))
        self.assertFalse(hasattr(preview, "target"))

    def test_sparse_image_size_counts_payload_not_address_span(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sparse.hex"
            path.write_text("\n".join([
                hex_record(0, 4, (0x0801).to_bytes(2, "big")),
                hex_record(0x0000, 0, b"\xAA"),
                hex_record(0x0010, 0, b"\xBB"),
                hex_record(0, 1, b""),
            ]) + "\n", encoding="ascii")
            info = inspect_image(path)
        self.assertEqual(info.start_address, 0x08010000)
        self.assertEqual(info.end_address, 0x08010010)
        self.assertEqual(info.size, 2)


if __name__ == "__main__":
    unittest.main()
