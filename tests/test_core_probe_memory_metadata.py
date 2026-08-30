from __future__ import annotations

import json
import struct
import tempfile
import threading
import unittest
import zlib
from unittest import mock
from pathlib import Path

from b300_core.memory import build_read_memory_command, read_memory
from b300_core.metadata import (
    OTA_META_MAGIC_OTA,
    OTA_META_MAGIC_STLINK,
    STATE_CONFIRMED,
    STATE_VERIFIED,
    build_stlink_metadata,
    decode_ota_metadata,
)
from b300_core.models import CommandResult, ImageInfo, ProbeRef
from b300_core.policy import validate_read_range
from b300_core import probe as probe_module
from b300_core.probe import parse_linux_sysfs, parse_windows_pnp_output


def make_metadata(state: int = 3, image_size: int = 130008,
                  image_crc32: int = 0x9F1E2EB3, sequence: int = 1,
                  magic: int = OTA_META_MAGIC_OTA) -> bytes:
    token = b"B300_F407ZE\0".ljust(16, b"\0")
    head = struct.pack(
        "<IIIII16sI",
        magic,
        1,
        state,
        image_size,
        image_crc32,
        token,
        sequence,
    )
    return head + struct.pack("<I", zlib.crc32(head) & 0xFFFFFFFF)


class ProbeMemoryMetadataTests(unittest.TestCase):
    def test_windows_probe_discovery_hides_backend_powershell(self) -> None:
        completed = type("Result", (), {"stdout": "", "returncode": 0})()
        with mock.patch.object(probe_module.platform, "system", return_value="Windows"), \
             mock.patch.object(probe_module.shutil, "which", return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"), \
             mock.patch.object(probe_module, "child_process_kwargs", return_value={"creationflags": 0x08000000, "startupinfo": object()}), \
             mock.patch.object(probe_module.subprocess, "run", return_value=completed) as run:
            self.assertEqual(probe_module.list_probes(), ())
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertIn("startupinfo", kwargs)
        self.assertTrue(kwargs["capture_output"])

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

    def test_linux_probe_parser_tolerates_non_ascii_clone_serial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory) / "1-2"
            device.mkdir()
            (device / "idVendor").write_bytes(b"0483\n")
            (device / "idProduct").write_bytes(b"3748\n")
            (device / "serial").write_bytes(b"ST\xc3\xa9LINK\xff\n")
            probes = parse_linux_sysfs(Path(directory))
        # An unsafe/non-ASCII serial is not passed to `adapter serial`; the
        # physical clone is still discoverable for safe auto-selection.
        self.assertEqual(len(probes), 1)
        self.assertIsNone(probes[0].serial)

    def test_linux_serialless_clone_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory) / "1-3"
            device.mkdir()
            (device / "idVendor").write_text("0483\n", encoding="ascii")
            (device / "idProduct").write_text("3748\n", encoding="ascii")
            probe = parse_linux_sysfs(Path(directory))[0]
        self.assertIsNone(probe.serial)
        self.assertFalse(probe.serial_available)
        self.assertIn("0483:374", probe.usb_identity)

    def test_windows_composite_instance_is_reported_without_fake_serial(self) -> None:
        raw = json.dumps([{
            "FriendlyName": "ST-Link Composite",
            "InstanceId": r"USB\VID_0483&PID_3748&MI_00\7&ABCD&0&0000",
        }])
        probe = parse_windows_pnp_output(raw)[0]
        self.assertIsNone(probe.serial)
        self.assertNotEqual(probe.usb_identity, "")

    def test_windows_serialless_clones_are_deduplicated_and_sorted_by_identity(self) -> None:
        first = r"USB\VID_0483&PID_3748&MI_00\7&BBBB&0&0000"
        second = r"USB\VID_0483&PID_3748&MI_00\7&AAAA&0&0000"
        raw = json.dumps([
            {"FriendlyName": "ST-Link Clone", "InstanceId": first},
            {"FriendlyName": "ST-Link Clone", "InstanceId": second},
            {"FriendlyName": "ST-Link Clone", "InstanceId": first},
        ])
        probes = parse_windows_pnp_output(raw)
        self.assertEqual([probe.usb_identity for probe in probes], [second, first])

    def test_metadata_decoder_reports_confirmed(self) -> None:
        metadata = decode_ota_metadata(make_metadata(state=3))
        self.assertEqual(metadata.state_name, "CONFIRMED")
        self.assertEqual(metadata.board_token, "B300_F407ZE")
        self.assertTrue(metadata.valid)
        self.assertEqual(metadata.classification, "VALID")

    def test_metadata_decoder_accepts_stlink_verified_and_confirmed(self) -> None:
        for state in (STATE_VERIFIED, STATE_CONFIRMED):
            with self.subTest(state=state):
                metadata = decode_ota_metadata(make_metadata(state=state, magic=OTA_META_MAGIC_STLINK))
                self.assertTrue(metadata.valid)
                self.assertEqual(metadata.magic, OTA_META_MAGIC_STLINK)

    def test_metadata_decoder_rejects_invalid_stlink_state(self) -> None:
        metadata = decode_ota_metadata(make_metadata(state=1, magic=OTA_META_MAGIC_STLINK))
        self.assertFalse(metadata.valid)
        self.assertEqual(metadata.classification, "CORRUPT")

    def test_build_stlink_metadata_roundtrips_exact_44_byte_contract(self) -> None:
        image = ImageInfo(
            path=Path("application.hex"),
            sha256="0" * 64,
            start_address=0x08010000,
            end_address=0x0801000F,
            size=12,
            data_record_count=2,
            flash_span_size=16,
            flash_crc32=0x12345678,
        )
        record = build_stlink_metadata(image, sequence=7)
        decoded = decode_ota_metadata(record)
        self.assertEqual(len(record), 44)
        self.assertTrue(decoded.valid)
        self.assertEqual(decoded.magic, OTA_META_MAGIC_STLINK)
        self.assertEqual(decoded.state, STATE_VERIFIED)
        self.assertEqual(decoded.image_size, 16)
        self.assertEqual(decoded.image_crc32, 0x12345678)
        self.assertEqual(decoded.sequence, 7)

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

    def test_legacy_and_canonical_appmeta_constants_cannot_drift(self) -> None:
        from b300_core import app_metadata
        from b300_core import metadata

        self.assertEqual(app_metadata.APP_METADATA_ADDRESS, 0x0800C000)
        self.assertEqual(app_metadata.APP_METADATA_SIZE, metadata.OTA_META_SIZE)
        self.assertEqual(app_metadata.APP_METADATA_FORMAT_VERSION, metadata.OTA_META_FORMAT_VERSION)
        self.assertEqual(app_metadata.APP_METADATA_MAGIC_OTA, metadata.OTA_META_MAGIC_OTA)
        self.assertEqual(app_metadata.APP_METADATA_MAGIC_STLINK, metadata.OTA_META_MAGIC_STLINK)
        self.assertEqual(app_metadata.APP_METADATA_BOARD_TOKEN.rstrip(b"\x00").decode("ascii"),
                         metadata.OTA_BOARD_TOKEN)



if __name__ == "__main__":
    unittest.main()
