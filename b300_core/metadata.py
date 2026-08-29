"""Canonical 44-byte B300 Application metadata shared by OTA and ST-Link."""

from __future__ import annotations

import struct
import zlib

from .models import ImageInfo, OtaMetadata


OTA_META_SIZE = 44
OTA_META_MAGIC_OTA = 0x4F54414D  # OTAM
OTA_META_MAGIC_STLINK = 0x53544C4D  # STLM
# Backward-compatible name used by older callers/tests.
OTA_META_MAGIC = OTA_META_MAGIC_OTA
OTA_META_FORMAT_VERSION = 1
OTA_BOARD_TOKEN = "B300_F407ZE"
OTA_MAX_IMAGE_SIZE = 0x00070000

STATE_EMPTY = 0
STATE_IN_PROGRESS = 1
STATE_VERIFIED = 2
STATE_CONFIRMED = 3
STATE_NAMES = {
    STATE_EMPTY: "EMPTY",
    STATE_IN_PROGRESS: "IN_PROGRESS",
    STATE_VERIFIED: "VERIFIED",
    STATE_CONFIRMED: "CONFIRMED",
}

_METADATA_STRUCT = struct.Struct("<IIIII16sII")


def _board_token_bytes() -> bytes:
    token = OTA_BOARD_TOKEN.encode("ascii")
    if len(token) > 16:
        raise ValueError("B300 board token exceeds metadata field.")
    return token.ljust(16, b"\0")


def _state_allowed(magic: int, state: int) -> bool:
    if magic == OTA_META_MAGIC_OTA:
        return state in (STATE_IN_PROGRESS, STATE_VERIFIED, STATE_CONFIRMED)
    if magic == OTA_META_MAGIC_STLINK:
        return state in (STATE_VERIFIED, STATE_CONFIRMED)
    return False


def decode_ota_metadata(data: bytes) -> OtaMetadata:
    if len(data) < OTA_META_SIZE:
        raise ValueError("OTA metadata requires 44 bytes.")
    record = bytes(data[:OTA_META_SIZE])
    values = _METADATA_STRUCT.unpack(record)
    magic, version, state, image_size, image_crc, token_raw, sequence, stored_crc = values
    calculated_crc = zlib.crc32(record[:-4]) & 0xFFFFFFFF

    if record == b"\xFF" * OTA_META_SIZE:
        classification = "ERASED"
        valid = False
        token = ""
    else:
        token_clean = token_raw.split(b"\0", 1)[0]
        try:
            token = token_clean.decode("ascii")
        except UnicodeDecodeError:
            token = ""
        valid = (
            magic in (OTA_META_MAGIC_OTA, OTA_META_MAGIC_STLINK)
            and version == OTA_META_FORMAT_VERSION
            and _state_allowed(magic, state)
            and 0 < image_size <= OTA_MAX_IMAGE_SIZE
            and token == OTA_BOARD_TOKEN
            and stored_crc == calculated_crc
        )
        classification = "VALID" if valid else "CORRUPT"

    return OtaMetadata(
        classification=classification,
        valid=valid,
        magic=magic,
        format_version=version,
        state=state,
        state_name=STATE_NAMES.get(state, "UNKNOWN"),
        image_size=image_size,
        image_crc32=image_crc,
        board_token=token,
        sequence=sequence,
        meta_crc32=stored_crc,
        calculated_meta_crc32=calculated_crc,
    )


def build_stlink_metadata(image: ImageInfo, sequence: int = 1) -> bytes:
    """Build the exact STLM+VERIFIED record accepted by Bootloader v0.6.5."""
    if (not isinstance(image.flash_span_size, int) or isinstance(image.flash_span_size, bool)
            or not 0 < image.flash_span_size <= OTA_MAX_IMAGE_SIZE):
        raise ValueError("Application canonical flash span is missing or invalid.")
    if (not isinstance(image.flash_crc32, int) or isinstance(image.flash_crc32, bool)
            or not 0 <= image.flash_crc32 <= 0xFFFFFFFF):
        raise ValueError("Application canonical flash CRC32 is missing or invalid.")
    if (not isinstance(sequence, int) or isinstance(sequence, bool)
            or not 0 <= sequence <= 0xFFFFFFFF):
        raise ValueError("ST-Link metadata sequence must be in range 0..0xFFFFFFFF.")

    head = struct.pack(
        "<IIIII16sI",
        OTA_META_MAGIC_STLINK,
        OTA_META_FORMAT_VERSION,
        STATE_VERIFIED,
        image.flash_span_size,
        image.flash_crc32,
        _board_token_bytes(),
        sequence,
    )
    meta_crc32 = zlib.crc32(head) & 0xFFFFFFFF
    record = head + struct.pack("<I", meta_crc32)
    if len(record) != OTA_META_SIZE:
        raise AssertionError("B300 AppMeta layout must remain exactly 44 bytes.")
    return record
