from __future__ import annotations

import struct
import unittest
import zlib

from b300_core.app_health import evaluate_application_health
from b300_core.metadata import (
    OTA_BOARD_TOKEN, OTA_META_MAGIC_OTA, OTA_META_MAGIC_STLINK,
    STATE_CONFIRMED, STATE_IN_PROGRESS, STATE_VERIFIED,
)
from b300_core.models import OtaMetadata


def application_payload(size: int = 64) -> bytes:
    payload = bytearray([0xA5] * size)
    struct.pack_into("<II", payload, 0, 0x20001000, 0x08010101)
    return bytes(payload)


def metadata_for(payload: bytes, *, magic=OTA_META_MAGIC_STLINK, state=STATE_CONFIRMED,
                 classification="VALID", valid=True, image_crc32=None) -> OtaMetadata:
    image_crc = zlib.crc32(payload) & 0xFFFFFFFF if image_crc32 is None else image_crc32
    state_names = {STATE_IN_PROGRESS: "IN_PROGRESS", STATE_VERIFIED: "VERIFIED", STATE_CONFIRMED: "CONFIRMED"}
    return OtaMetadata(
        classification=classification, valid=valid, magic=magic, format_version=1,
        state=state, state_name=state_names.get(state, "UNKNOWN"), image_size=len(payload),
        image_crc32=image_crc, board_token=OTA_BOARD_TOKEN, sequence=7,
        meta_crc32=0x12345678, calculated_meta_crc32=0x12345678,
    )


class ApplicationHealthTests(unittest.TestCase):
    def test_confirmed_stlink_image_with_valid_vector_and_crc_is_bootable(self) -> None:
        payload = application_payload()
        health = evaluate_application_health(metadata_for(payload), payload)
        self.assertEqual(health.lifecycle, "BOOTABLE")
        self.assertTrue(health.bootable)
        self.assertTrue(health.vector_valid)
        self.assertTrue(health.image_crc_valid)
        self.assertEqual(health.actual_image_crc32, zlib.crc32(payload) & 0xFFFFFFFF)
        self.assertEqual(health.bytes_checked, len(payload))
        self.assertEqual(health.next_action, "No action is required.")

    def test_ota_verified_is_bootable_when_image_evidence_matches(self) -> None:
        payload = application_payload()
        health = evaluate_application_health(
            metadata_for(payload, magic=OTA_META_MAGIC_OTA, state=STATE_VERIFIED), payload
        )
        self.assertEqual(health.lifecycle, "BOOTABLE")
        self.assertTrue(health.bootable)

    def test_stlink_verified_is_pending_not_bootable(self) -> None:
        payload = application_payload()
        health = evaluate_application_health(
            metadata_for(payload, magic=OTA_META_MAGIC_STLINK, state=STATE_VERIFIED), payload
        )
        self.assertEqual(health.lifecycle, "STLINK_VERIFIED_PENDING")
        self.assertFalse(health.bootable)
        self.assertIn("Reset once", health.next_action)

    def test_ota_in_progress_is_not_bootable_even_with_matching_bytes(self) -> None:
        payload = application_payload()
        health = evaluate_application_health(
            metadata_for(payload, magic=OTA_META_MAGIC_OTA, state=STATE_IN_PROGRESS), payload
        )
        self.assertEqual(health.lifecycle, "OTA_IN_PROGRESS")
        self.assertFalse(health.bootable)

    def test_erased_and_corrupt_metadata_never_use_vector_fallback(self) -> None:
        payload = application_payload()
        erased = metadata_for(payload, classification="ERASED", valid=False)
        corrupt = metadata_for(payload, classification="CORRUPT", valid=False)
        self.assertEqual(evaluate_application_health(erased, payload).lifecycle, "UNMANAGED_RECOVERY")
        self.assertEqual(evaluate_application_health(corrupt, payload).lifecycle, "INVALID_METADATA")
        self.assertFalse(evaluate_application_health(erased, payload).bootable)
        self.assertFalse(evaluate_application_health(corrupt, payload).bootable)

    def test_short_or_failed_image_read_is_incomplete(self) -> None:
        payload = application_payload()
        metadata = metadata_for(payload)
        short = evaluate_application_health(metadata, payload[:-1])
        failed = evaluate_application_health(metadata, None, "OpenOCD read failed")
        self.assertEqual(short.lifecycle, "IMAGE_READ_INCOMPLETE")
        self.assertIn("expected 64 bytes", short.reason)
        self.assertEqual(failed.lifecycle, "IMAGE_READ_INCOMPLETE")
        self.assertEqual(failed.reason, "OpenOCD read failed")
        self.assertIsNone(failed.image_crc_valid)

    def test_invalid_vector_is_distinct_from_crc_mismatch(self) -> None:
        payload = application_payload()
        bad_vector = bytearray(payload)
        struct.pack_into("<I", bad_vector, 0, 0xDEADBEEF)
        bad_vector = bytes(bad_vector)
        vector_health = evaluate_application_health(metadata_for(bad_vector), bad_vector)
        self.assertEqual(vector_health.lifecycle, "INVALID_VECTOR")
        self.assertFalse(vector_health.bootable)

        crc_health = evaluate_application_health(
            metadata_for(payload, image_crc32=(zlib.crc32(payload) + 1) & 0xFFFFFFFF), payload
        )
        self.assertEqual(crc_health.lifecycle, "IMAGE_CRC_MISMATCH")
        self.assertFalse(crc_health.image_crc_valid)
        self.assertFalse(crc_health.bootable)


if __name__ == "__main__":
    unittest.main()
