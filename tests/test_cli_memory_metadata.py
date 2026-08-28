from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from b300_cli.parser import parse_args
from b300_core.metadata import decode_ota_metadata
from b300_core.models import ProbeInfo, ProbeRef
from b300_core.policy import sector_by_index


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_metadata(state: int = 3, image_size: int = 130008,
                  image_crc32: int = 0x9F1E2EB3, sequence: int = 1) -> bytes:
    token = b"B300_F407ZE\0".ljust(16, b"\0")
    head = struct.pack(
        "<IIIII16sI", 0x4F54414D, 1, state, image_size, image_crc32, token, sequence,
    )
    return head + struct.pack("<I", zlib.crc32(head) & 0xFFFFFFFF)


class FakeService:
    def __init__(self, data: bytes = bytes(range(32)), metadata: bytes | None = None,
                 error: Exception | None = None) -> None:
        self.data = data
        self.metadata = decode_ota_metadata(metadata or make_metadata())
        self.error = error
        self.calls = []

    def read_memory(self, probe, address, length):
        self.calls.append(("read_memory", probe, address, length))
        if self.error is not None:
            raise self.error
        return self.data[:length]

    def read_sector(self, probe, sector):
        self.calls.append(("read_sector", probe, sector))
        if self.error is not None:
            raise self.error
        return self.data

    def read_metadata(self, probe):
        self.calls.append(("read_metadata", probe))
        if self.error is not None:
            raise self.error
        return self.metadata


def run_cli(argv, probes=(ProbeInfo("SAFE123", "ST-Link", "test", "usb:1"),),
            service: FakeService | None = None):
    module = tool()
    output = io.StringIO()
    service = service or FakeService()
    with mock.patch.object(module, "list_probes", return_value=probes), \
            mock.patch.object(module, "B300Service", lambda executable=None: service), \
            redirect_stdout(output):
        code = module.main(argv)
    text = output.getvalue()
    return code, json.loads(text) if "--json" in argv else text, service


class ParserAndRangeTests(unittest.TestCase):
    def test_memory_read_accepts_absolute_hex_address(self) -> None:
        args = parse_args(["memory", "read", "0x08010000", "64", "--json"])
        self.assertEqual(args.address, 0x08010000)

    def test_metadata_show_accepts_probe_serial(self) -> None:
        args = parse_args(["metadata", "show", "--probe-serial", "SAFE123"])
        self.assertEqual(args.probe_serial, "SAFE123")

    def test_memory_parser_rejects_invalid_number(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["memory", "read", "not-a-number", "8"])

    def test_memory_range_outside_f407_is_rejected_before_service(self) -> None:
        service = FakeService()
        code, value, service = run_cli(["memory", "read", "0x07FFFFFF", "8", "--json"],
                                       service=service)
        self.assertNotEqual(code, 0)
        self.assertEqual(value["reason_code"], "INVALID_MEMORY_RANGE")
        self.assertEqual(service.calls, [])

    def test_memory_rejects_zero_negative_and_overflow_lengths(self) -> None:
        for address, length in (("0x08010000", "0"), ("0x08010000", "-1"),
                                ("0x0807FFFF", "2")):
            with self.subTest(address=address, length=length):
                code, value, _ = run_cli(["memory", "read", address, length, "--json"])
                self.assertNotEqual(code, 0)
                self.assertEqual(value["reason_code"], "INVALID_MEMORY_RANGE")

    def test_read_sector_rejects_negative_and_outside_indices(self) -> None:
        for sector in ("-1", "8"):
            with self.subTest(sector=sector):
                code, value, service = run_cli(["memory", "read-sector", sector, "--json"])
                self.assertNotEqual(code, 0)
                self.assertEqual(value["reason_code"], "INVALID_SECTOR")
                self.assertEqual(service.calls, [])
        with self.assertRaisesRegex(ValueError, "0..7"):
            sector_by_index(-1)


class MemoryReadTests(unittest.TestCase):
    def test_memory_read_json_reports_absolute_range_and_lowercase_hex(self) -> None:
        data = bytes.fromhex("00ABCDef")
        code, value, service = run_cli(["memory", "read", "0x08010000", "4", "--json"],
                                       service=FakeService(data=data))
        self.assertEqual(code, 0)
        self.assertEqual(value["address"], "0x08010000")
        self.assertEqual(value["end_address"], "0x08010003")
        self.assertEqual(value["size"], 4)
        self.assertEqual(value["data"], "00abcdef")
        self.assertEqual(service.calls, [("read_memory", ProbeRef("SAFE123"), 0x08010000, 4)])

    def test_memory_read_text_uses_sixteen_byte_absolute_rows(self) -> None:
        code, text, _ = run_cli(["memory", "read", "0x08010000", "17"],
                                service=FakeService(data=bytes(range(17))))
        self.assertEqual(code, 0)
        self.assertIn("08010000  00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F", text)
        self.assertIn("08010010  10", text)

    def test_read_sector_uses_public_service_api_and_serialless_probe(self) -> None:
        service = FakeService(data=b"\x10\x20")
        serialless = (ProbeInfo(None, "Clone", "test", "usb:1"),)
        code, value, service = run_cli(["memory", "read-sector", "3", "--json"],
                                       probes=serialless, service=service)
        self.assertEqual(code, 0)
        self.assertEqual(value["address"], "0x0800C000")
        self.assertEqual(value["end_address"], "0x0800C001")
        self.assertEqual(service.calls, [("read_sector", ProbeRef(None), 3)])

    def test_memory_commands_keep_centralized_probe_errors(self) -> None:
        probes = (ProbeInfo("FIRST", "ST-Link", "test", "usb:1"),
                  ProbeInfo("SECOND", "ST-Link", "test", "usb:2"))
        code, value, _ = run_cli(["metadata", "show", "--json"], probes=probes)
        self.assertNotEqual(code, 0)
        self.assertEqual(value["reason_code"], "MULTIPLE_PROBES")


class MemoryDumpTests(unittest.TestCase):
    def test_dump_writes_exact_atomic_snapshot_and_hashes(self) -> None:
        data = bytes.fromhex("00112233")
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "snapshot.bin"
            code, value, _ = run_cli([
                "memory", "dump", "0x08010000", "4", str(output_path), "--json",
            ], service=FakeService(data=data))
            self.assertEqual(code, 0)
            self.assertEqual(output_path.read_bytes(), data)
            self.assertEqual(value["address"], "0x08010000")
            self.assertEqual(value["end_address"], "0x08010003")
            self.assertEqual(value["size"], 4)
            self.assertEqual(value["sha256"], hashlib.sha256(data).hexdigest())

    def test_dump_text_uses_uppercase_sha256(self) -> None:
        data = b"data"
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "snapshot.bin"
            code, text, _ = run_cli([
                "memory", "dump", "0x08010000", "4", str(output_path),
            ], service=FakeService(data=data))
        self.assertEqual(code, 0)
        self.assertIn(hashlib.sha256(data).hexdigest().upper(), text)

    def test_dump_refuses_existing_file_unless_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "snapshot.bin"
            output_path.write_bytes(b"old")
            code, value, _ = run_cli([
                "memory", "dump", "0x08010000", "3", str(output_path), "--json",
            ], service=FakeService(data=b"new"))
            self.assertNotEqual(code, 0)
            self.assertEqual(value["reason_code"], "OUTPUT_EXISTS")
            self.assertEqual(output_path.read_bytes(), b"old")
            code, value, _ = run_cli([
                "memory", "dump", "0x08010000", "3", str(output_path), "--force", "--json",
            ], service=FakeService(data=b"new"))
            self.assertEqual(code, 0)
            self.assertEqual(output_path.read_bytes(), b"new")

    def test_dump_rejects_directory_and_removes_partial_file_when_snapshot_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            code, value, _ = run_cli([
                "memory", "dump", "0x08010000", "4", str(path), "--json",
            ])
            self.assertNotEqual(code, 0)
            self.assertEqual(value["reason_code"], "INVALID_OUTPUT_PATH")
            failing = FakeService(error=RuntimeError("target failed"))
            output_path = path / "failed.bin"
            code, value, _ = run_cli([
                "memory", "dump", "0x08010000", "4", str(output_path), "--json",
            ], service=failing)
            self.assertNotEqual(code, 0)
            self.assertEqual(value["reason_code"], "MEMORY_READ_FAILED")
            self.assertFalse(output_path.exists())
            self.assertEqual(list(path.glob(".failed.bin.*")), [])
            module = tool()
            interrupted = path / "interrupted.bin"
            output = io.StringIO()
            with mock.patch.object(module, "list_probes", return_value=(
                    ProbeInfo("SAFE123", "ST-Link", "test", "usb:1"),)), \
                    mock.patch.object(module, "B300Service", lambda executable=None: FakeService(data=b"data")), \
                    mock.patch.object(module.os, "replace", side_effect=OSError("replace failed")), \
                    redirect_stdout(output):
                code = module.main([
                    "memory", "dump", "0x08010000", "4", str(interrupted), "--json",
                ])
            self.assertNotEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["reason_code"], "INVALID_OUTPUT_PATH")
            self.assertFalse(interrupted.exists())
            self.assertEqual(list(path.glob(".interrupted.bin.*")), [])


class MetadataPresentationTests(unittest.TestCase):
    def test_erased_metadata_uses_null_semantic_json_fields(self) -> None:
        code, value, _ = run_cli(["metadata", "show", "--json"],
                                 service=FakeService(metadata=b"\xFF" * 44))
        self.assertEqual(code, 0)
        metadata = value["metadata"]
        self.assertEqual(metadata["classification"], "ERASED")
        self.assertEqual(metadata["magic"], "0xFFFFFFFF")
        for field in ("format_version", "state", "state_name", "image_size", "image_crc32",
                      "board_token", "sequence", "meta_crc32", "calculated_meta_crc32"):
            self.assertIsNone(metadata[field])
        self.assertFalse(metadata["valid"])

    def test_valid_and_corrupt_metadata_preserve_decoder_fields(self) -> None:
        for data, classification in ((make_metadata(), "VALID"), (b"\0" * 44, "CORRUPT")):
            with self.subTest(classification=classification):
                code, value, _ = run_cli(["metadata", "show", "--json"],
                                         service=FakeService(metadata=data))
                self.assertEqual(code, 0)
                metadata = value["metadata"]
                decoded = decode_ota_metadata(data)
                self.assertEqual(metadata["classification"], classification)
                self.assertEqual(metadata["magic"], "0x%08X" % decoded.magic)
                self.assertEqual(metadata["format_version"], decoded.format_version)
                self.assertEqual(metadata["state"], decoded.state)
                self.assertEqual(metadata["state_name"], decoded.state_name)
                self.assertEqual(metadata["image_size"], decoded.image_size)
                self.assertEqual(metadata["image_crc32"], "0x%08X" % decoded.image_crc32)
                self.assertEqual(metadata["board_token"], decoded.board_token)
                self.assertEqual(metadata["sequence"], decoded.sequence)
                self.assertEqual(metadata["meta_crc32"], "0x%08X" % decoded.meta_crc32)
                self.assertEqual(metadata["calculated_meta_crc32"],
                                 "0x%08X" % decoded.calculated_meta_crc32)
                self.assertEqual(metadata["valid"], decoded.valid)


if __name__ == "__main__":
    unittest.main()
