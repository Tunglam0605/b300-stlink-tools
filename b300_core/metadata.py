"""Decode the fixed 44-byte B300 OTA metadata record."""

from __future__ import annotations

import struct
import zlib

from .models import OtaMetadata


OTA_META_SIZE = 44
OTA_META_MAGIC = 0x4F54414D
OTA_META_FORMAT_VERSION = 1
OTA_BOARD_TOKEN = "B300_F407ZE"
STATE_NAMES = {
    0: "EMPTY",
    1: "IN_PROGRESS",
    2: "VERIFIED",
    3: "CONFIRMED",
}


def decode_ota_metadata(data: bytes) -> OtaMetadata:
    if len(data) < OTA_META_SIZE:
        raise ValueError("OTA metadata requires 44 bytes.")
    record = bytes(data[:OTA_META_SIZE])
    values = struct.unpack("<IIIII16sII", record)
    magic, version, state, image_size, image_crc, token_raw, sequence, stored_crc = values
    calculated_crc = zlib.crc32(record[:-4]) & 0xFFFFFFFF
    token = token_raw.split(b"\0", 1)[0].decode("ascii", errors="replace")

    if record == b"\xFF" * OTA_META_SIZE:
        classification = "ERASED"
        valid = False
    else:
        valid = (
            magic == OTA_META_MAGIC
            and version == OTA_META_FORMAT_VERSION
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

