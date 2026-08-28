from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b300_core.factory_policy import build_factory_plan, build_factory_preview
from b300_core.hex_image import inspect_bootloader_image
from b300_core.models import ProbeRef, TargetInfo
from tests.test_core_hex_policy import hex_record, write_hex


def bootloader_vector() -> bytes:
    return (0x20001910).to_bytes(4, "little") + (0x080002D5).to_bytes(4, "little")


class FactoryPolicyTests(unittest.TestCase):
    def test_bootloader_hex_accepts_only_plausible_vector_in_sectors_zero_to_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_hex(directory, 0x08000000, bootloader_vector() + b"\xAA")
            info = inspect_bootloader_image(path)
        self.assertEqual(info.start_address, 0x08000000)
        self.assertEqual(info.end_address, 0x08000008)

    def test_bootloader_hex_rejects_data_in_metadata_sector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bootloader.hex"
            path.write_text("\n".join([
                hex_record(0, 4, (0x0800).to_bytes(2, "big")),
                hex_record(0, 0, bootloader_vector()),
                hex_record(0xC000, 0, b"\x00"),
                hex_record(0, 1, b""),
            ]) + "\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "Bootloader HEX.*outside"):
                inspect_bootloader_image(path)

    def test_bootloader_hex_rejects_application_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_hex(directory, 0x08010000, b"\x00" * 8)
            with self.assertRaisesRegex(ValueError, "Bootloader HEX.*outside"):
                inspect_bootloader_image(path)

    def test_bootloader_hex_rejects_implausible_vector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_hex(directory, 0x08000000, b"\x00" * 8)
            with self.assertRaisesRegex(ValueError, "vector table"):
                inspect_bootloader_image(path)

    def test_factory_plan_is_fixed_to_sectors_zero_through_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = inspect_bootloader_image(
                write_hex(directory, 0x08000000, bootloader_vector())
            )
            target = TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True)
            plan = build_factory_plan(image, ProbeRef("FACTORY123"), target)
            preview = build_factory_preview(image, ProbeRef("FACTORY123"))
        self.assertEqual(plan.erase_sectors, (0, 1, 2))
        self.assertEqual(preview.erase_sectors, (0, 1, 2))
        self.assertEqual(plan.target, target)

    def test_factory_plan_rejects_non_f407_512k_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = inspect_bootloader_image(
                write_hex(directory, 0x08000000, bootloader_vector())
            )
            with self.assertRaisesRegex(ValueError, "Unsupported target"):
                build_factory_plan(
                    image,
                    ProbeRef("FACTORY123"),
                    TargetInfo(0x419, 2048, 3.09, "unknown"),
                )

    def test_factory_plan_rejects_incomplete_wrp_report_before_service_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = inspect_bootloader_image(
                write_hex(directory, 0x08000000, bootloader_vector())
            )
            with self.assertRaisesRegex(ValueError, "write-protection"):
                build_factory_plan(
                    image,
                    ProbeRef("FACTORY123"),
                    TargetInfo(0x413, 512, 3.09, "unknown", (), False),
                )

    def test_factory_plan_accepts_initially_protected_or_unprotected_bootloader(self) -> None:
        targets = (
            TargetInfo(0x413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True),
            TargetInfo(0x413, 512, 3.09, "S0-S2 unprotected", (), True),
        )
        with tempfile.TemporaryDirectory() as directory:
            image = inspect_bootloader_image(
                write_hex(directory, 0x08000000, bootloader_vector())
            )
            plans = [build_factory_plan(image, ProbeRef(None), target) for target in targets]

        self.assertEqual([plan.target for plan in plans], list(targets))


if __name__ == "__main__":
    unittest.main()
