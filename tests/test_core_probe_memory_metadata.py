from __future__ import annotations

import json
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

from b300_core.memory import build_read_memory_command, read_memory
from b300_core.metadata import decode_ota_metadata
from b300_core.models import CommandResult, ProbeRef
from b300_core.policy import validate_read_range
from b300_core.probe import parse_linux_sysfs, parse_windows_pnp_output


def make_metadata(state: int = 3, image_size: int = 130008,
                  image_crc32: int = 0x9F1E2EB3, sequence: int = 1) -> bytes:
    token = b"B300_F407ZE\0".ljust(16, b"\0")
    head = struct.pack(
        "<IIIII16sI",
        0x4F54414D,
        1,
        state,
        image_size,
        image_crc32,
        token,
        sequence,
    )
    return head + struct.pack("<I", zlib.crc32(head) & 0xFFFFFFFF)


class ProbeMemoryMetadataTests(unittest.TestCase):
    def test_windows_probe_parser_extracts_unique_serials(self) -> None:
        raw = json.dumps([
            {"FriendlyName": "ST-Link Debug", "InstanceId":
             r"USB\VID_0483&PID_3748\ABC123"},
            {"FriendlyName": "ST-Link Debug", "InstanceId":
             r"USB\VID_0483&PID_3748\ABC123"},
        ])
        probes = parse_windows_pnp_output(raw)
        self.assertEqual(len(probes), 1)
        self.assertEqual(probes[0].serial, "ABC123")

    def test_linux_probe_parser_reads_sysfs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory) / "1-1"
            device.mkdir()
            (device / "idVendor").write_text("0483\n", encoding="ascii")
            (device / "idProduct").write_text("3748\n", encoding="ascii")
            (device / "serial").write_text("LINUX123\n", encoding="ascii")
            probes = parse_linux_sysfs(Path(directory))
        self.assertEqual([item.serial for item in probes], ["LINUX123"])

    def test_metadata_decoder_reports_confirmed(self) -> None:
        metadata = decode_ota_metadata(make_metadata(state=3))
        self.assertEqual(metadata.state_name, "CONFIRMED")
        self.assertEqual(metadata.board_token, "B300_F407ZE")
        self.assertTrue(metadata.valid)
        self.assertEqual(metadata.classification, "VALID")

    def test_metadata_decoder_distinguishes_erased_and_corrupt(self) -> None:
        erased = decode_ota_metadata(b"\xFF" * 44)
        corrupt = decode_ota_metadata(b"\x00" * 44)
        self.assertEqual(erased.classification, "ERASED")
        self.assertEqual(corrupt.classification, "CORRUPT")
        self.assertFalse(erased.valid)
        self.assertFalse(corrupt.valid)

    def test_memory_rejects_outside_f407_flash(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside B300 flash"):
            validate_read_range(0x08080000, 4)

    def test_read_command_is_read_only_and_selects_probe(self) -> None:
        command = build_read_memory_command(
            ProbeRef("SAFE123"), "openocd", Path("memory.bin"), 0x0800C000, 44
        )
        rendered = " ".join(command)
        read_step = next(item for item in command if "dump_image" in item)
        self.assertIn("catch {dump_image", read_step)
        self.assertLess(command.index(read_step), command.index("resume"))
        self.assertIn("shutdown error", rendered)
        self.assertIn("adapter serial SAFE123", rendered)
        self.assertNotIn("erase", rendered)
        self.assertNotIn("program", rendered)
        self.assertNotIn("mww", rendered)
        self.assertNotIn("reset", rendered)

    def test_cancelled_read_runs_separate_resume_recovery(self) -> None:
        class CancelledRunner:
            def __init__(self):
                self.commands = []

            def run(self, command, event_sink=None, **options):
                self.commands.append(tuple(command))
                if len(self.commands) == 1:
                    return CommandResult(
                        tuple(command), -1, "cancelled", cancelled=True
                    )
                return CommandResult(tuple(command), 0, "resumed")

        runner = CancelledRunner()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            read_memory(
                ProbeRef("SAFE123"),
                0x0800C000,
                44,
                executable="openocd",
                runner=runner,
                cancel_event=cancel,
            )
        self.assertEqual(len(runner.commands), 2)
        recovery = " ".join(runner.commands[1])
        self.assertIn("resume", recovery)
        self.assertNotIn("halt", recovery)


if __name__ == "__main__":
    unittest.main()
