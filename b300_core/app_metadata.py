"""Canonical B300 Bootloader v6.5 Application Metadata contract."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Optional


APP_METADATA_ADDRESS = 0x0800C000
APPLICATION_ADDRESS = 0x08010000
FLASH_END_ADDRESS = 0x08080000
APP_METADATA_FORMAT = "<IIIII16sII"
APP_METADATA_SIZE = 44
APP_METADATA_FORMAT_VERSION = 1
APP_METADATA_MAGIC_OTA = 0x4F54414D
APP_METADATA_MAGIC_STLINK = 0x53544C4D
APP_METADATA_BOARD_TOKEN = b"B300_F407ZE" + b"\x00" * 5


class AppMetadataSource(Enum):
    OTA = "OTA"
    STLINK = "ST-Link"


class AppMetadataState(IntEnum):
    EMPTY = 0
    IN_PROGRESS = 1
    VERIFIED = 2
    CONFIRMED = 3


@dataclass(frozen=True)
class B300AppMetadata:
    """The eight authoritative fields of a packed B300_AppMeta record."""

    magic: int
    format_version: int
    state: int
    image_size: int
    image_crc32: int
    board_token_bytes: bytes
    sequence: int
    meta_crc32: int

    @property
    def classification(self) -> str:
        if _is_erased(self):
            return "ERASED"
        return "VALID" if validate_app_metadata(self) else "CORRUPT"

    @property
    def valid(self) -> bool:
        return validate_app_metadata(self)

    @property
    def source(self) -> Optional[AppMetadataSource]:
        if self.magic == APP_METADATA_MAGIC_OTA:
            return AppMetadataSource.OTA
        if self.magic == APP_METADATA_MAGIC_STLINK:
            return AppMetadataSource.STLINK
        return None

    @property
    def state_name(self) -> str:
        try:
            return AppMetadataState(self.state).name
        except ValueError:
            return "UNKNOWN"

    @property
    def board_token(self) -> str:
        display_bytes = bytes(self.board_token_bytes).split(b"\x00", 1)[0]
        try:
            return display_bytes.decode("ascii")
        except UnicodeDecodeError:
            return ""

    @property
    def calculated_meta_crc32(self) -> int:
        return calculate_metadata_crc(_pack_head(self))


def calculate_metadata_crc(data: bytes) -> int:
    """Calculate the Bootloader-compatible reflected CRC-32."""
    return zlib.crc32(bytes(data)) & 0xFFFFFFFF


def calculate_image_crc(data: bytes) -> int:
    """Calculate the Bootloader-compatible Application CRC-32."""
    return zlib.crc32(bytes(data)) & 0xFFFFFFFF


def _is_uint32(value: object) -> bool:
    return isinstance(value, int) and 0 <= value <= 0xFFFFFFFF


def _is_erased(metadata: B300AppMetadata) -> bool:
    return all(
        value == 0xFFFFFFFF
        for value in (
            metadata.magic,
            metadata.format_version,
            metadata.state,
            metadata.image_size,
            metadata.image_crc32,
            metadata.sequence,
            metadata.meta_crc32,
        )
    ) and metadata.board_token_bytes == b"\xFF" * 16


def _pack_head(metadata: B300AppMetadata) -> bytes:
    return struct.pack(
        "<IIIII16sI",
        metadata.magic,
        metadata.format_version,
        metadata.state,
        metadata.image_size,
        metadata.image_crc32,
        metadata.board_token_bytes,
        metadata.sequence,
    )

def validate_app_metadata(metadata: B300AppMetadata) -> bool:
    if not isinstance(metadata, B300AppMetadata):
        return False
    if not all(_is_uint32(getattr(metadata, name)) for name in (
        "magic", "format_version", "state", "image_size", "image_crc32",
        "sequence", "meta_crc32",
    )):
        return False
    if not isinstance(metadata.board_token_bytes, bytes):
        return False
    if len(metadata.board_token_bytes) != 16:
        return False
    if metadata.magic not in (APP_METADATA_MAGIC_OTA, APP_METADATA_MAGIC_STLINK):
        return False
    if metadata.format_version != APP_METADATA_FORMAT_VERSION:
        return False
    if not 1 <= metadata.image_size <= FLASH_END_ADDRESS - APPLICATION_ADDRESS:
        return False
    if metadata.board_token_bytes != APP_METADATA_BOARD_TOKEN:
        return False
    allowed_states = (
        (AppMetadataState.IN_PROGRESS, AppMetadataState.VERIFIED,
         AppMetadataState.CONFIRMED)
        if metadata.magic == APP_METADATA_MAGIC_OTA else
        (AppMetadataState.VERIFIED, AppMetadataState.CONFIRMED)
    )
    if metadata.state not in allowed_states:
        return False
    try:
        calculated = calculate_metadata_crc(_pack_head(metadata))
    except (struct.error, TypeError):
        return False
    return metadata.meta_crc32 == calculated


def decode_app_metadata(data: bytes) -> B300AppMetadata:
    record = bytes(data)
    if len(record) != APP_METADATA_SIZE:
        raise ValueError("Application metadata requires exactly 44 bytes.")
    values = struct.unpack(APP_METADATA_FORMAT, record)
    return B300AppMetadata(*values)


def pack_app_metadata(metadata: B300AppMetadata) -> bytes:
    if not validate_app_metadata(metadata):
        raise ValueError("Cannot pack invalid or noncanonical Application metadata.")
    return struct.pack(APP_METADATA_FORMAT, *(
        metadata.magic,
        metadata.format_version,
        metadata.state,
        metadata.image_size,
        metadata.image_crc32,
        metadata.board_token_bytes,
        metadata.sequence,
        metadata.meta_crc32,
    ))


def build_stlink_verified_metadata(
    image_size: int, image_crc32: int, sequence: int
) -> B300AppMetadata:
    max_image_size = FLASH_END_ADDRESS - APPLICATION_ADDRESS
    if not isinstance(image_size, int) or not 1 <= image_size <= max_image_size:
        raise ValueError("Application image size is outside the F407 flash range.")
    image_crc32 &= 0xFFFFFFFF
    sequence &= 0xFFFFFFFF
    metadata = B300AppMetadata(
        APP_METADATA_MAGIC_STLINK,
        APP_METADATA_FORMAT_VERSION,
        AppMetadataState.VERIFIED,
        image_size,
        image_crc32,
        APP_METADATA_BOARD_TOKEN,
        sequence,
        0,
    )
    return B300AppMetadata(
        metadata.magic,
        metadata.format_version,
        metadata.state,
        metadata.image_size,
        metadata.image_crc32,
        metadata.board_token_bytes,
        metadata.sequence,
        calculate_metadata_crc(_pack_head(metadata)),
    )
