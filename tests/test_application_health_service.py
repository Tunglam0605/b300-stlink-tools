from __future__ import annotations

import contextlib
import struct
import unittest
import zlib
from unittest import mock

from b300_core.hardware_session import HardwareMode
from b300_core.metadata import (
    OTA_BOARD_TOKEN, OTA_META_MAGIC_STLINK, STATE_CONFIRMED,
)
from b300_core.models import ProbeRef
from b300_core.policy import APPLICATION_ADDRESS, METADATA_ADDRESS
from b300_core.service import B300Service


def payload(size=64):
    data = bytearray([0x11] * size)
    struct.pack_into("<II", data, 0, 0x20001000, 0x08010101)
    return bytes(data)


def metadata_bytes(data: bytes) -> bytes:
    token = OTA_BOARD_TOKEN.encode("ascii").ljust(16, b"\0")
    image_crc = zlib.crc32(data) & 0xFFFFFFFF
    head = struct.pack(
        "<IIIII16sI", OTA_META_MAGIC_STLINK, 1, STATE_CONFIRMED, len(data), image_crc, token, 4
    )
    return head + struct.pack("<I", zlib.crc32(head) & 0xFFFFFFFF)


class ApplicationHealthServiceTests(unittest.TestCase):
    def test_health_uses_one_reading_lease_and_exact_metadata_image_lengths(self) -> None:
        data = payload(96)
        meta = metadata_bytes(data)
        service = B300Service(executable="openocd")
        probe = ProbeRef("TEST")
        reads = []

        def fake_read(selected_probe, address, length, *_args):
            reads.append((selected_probe, address, length))
            if address == METADATA_ADDRESS:
                return meta
            if address == APPLICATION_ADDRESS:
                return data
            raise AssertionError(address)

        with mock.patch.object(
            service, "_exclusive_hardware_operation", return_value=contextlib.nullcontext()
        ) as lease, mock.patch.object(service, "_read_memory", side_effect=fake_read):
            health = service.inspect_application_health(probe)

        lease.assert_called_once_with(HardwareMode.READING, probe)
        self.assertEqual(reads, [
            (probe, METADATA_ADDRESS, 44),
            (probe, APPLICATION_ADDRESS, len(data)),
        ])
        self.assertEqual(health.lifecycle, "BOOTABLE")
        self.assertTrue(health.bootable)

    def test_health_read_failure_becomes_incomplete_evidence_not_write_retry(self) -> None:
        data = payload(80)
        meta = metadata_bytes(data)
        service = B300Service(executable="openocd")
        probe = ProbeRef("TEST")
        calls = []

        def fake_read(_probe, address, length, *_args):
            calls.append((address, length))
            if address == METADATA_ADDRESS:
                return meta
            raise OSError("USB link dropped")

        with mock.patch.object(
            service, "_exclusive_hardware_operation", return_value=contextlib.nullcontext()
        ), mock.patch.object(service, "_read_memory", side_effect=fake_read):
            health = service.inspect_application_health(probe)

        self.assertEqual(calls, [(METADATA_ADDRESS, 44), (APPLICATION_ADDRESS, len(data))])
        self.assertEqual(health.lifecycle, "IMAGE_READ_INCOMPLETE")
        self.assertEqual(health.reason, "USB link dropped")
        self.assertFalse(health.bootable)
        self.assertEqual(health.bytes_checked, 0)

    def test_invalid_metadata_only_reads_vector_sized_application_probe(self) -> None:
        service = B300Service(executable="openocd")
        probe = ProbeRef("TEST")
        erased = b"\xFF" * 44
        vector = struct.pack("<II", 0x20001000, 0x08010101)
        reads = []

        def fake_read(_probe, address, length, *_args):
            reads.append((address, length))
            return erased if address == METADATA_ADDRESS else vector

        with mock.patch.object(
            service, "_exclusive_hardware_operation", return_value=contextlib.nullcontext()
        ), mock.patch.object(service, "_read_memory", side_effect=fake_read):
            health = service.inspect_application_health(probe)

        self.assertEqual(reads, [(METADATA_ADDRESS, 44), (APPLICATION_ADDRESS, 8)])
        self.assertEqual(health.lifecycle, "UNMANAGED_RECOVERY")
        self.assertFalse(health.bootable)


if __name__ == "__main__":
    unittest.main()
