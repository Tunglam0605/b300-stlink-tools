"""Strict Intel HEX inspection for bounded B300 firmware images."""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional, Tuple

from .application_vector import inspect_application_vector
from .models import ImageInfo
from .policy import (
    APPLICATION_ADDRESS,
    FLASH_END_ADDRESS,
    FLASH_START_ADDRESS,
    METADATA_ADDRESS,
)


def _inspect_hex(path: Path, *, label: str, allowed_start: int,
                 allowed_end: int, required_start: int,
                 range_message: str) -> Tuple[ImageInfo, Dict[int, int]]:
    path = Path(path).expanduser().resolve()
    try:
        raw_file = path.read_bytes()
        lines = raw_file.decode("ascii").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError("Cannot read %s HEX: %s" % (label.lower(), error)) from error

    base_address = 0
    first_address: Optional[int] = None
    last_address: Optional[int] = None
    data_record_count = 0
    data_byte_count = 0
    eof_seen = False
    memory: Dict[int, int] = {}

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if eof_seen:
            raise ValueError("HEX contains data after EOF at line %d." % line_number)
        if not line.startswith(":"):
            raise ValueError("HEX line %d does not start with ':'." % line_number)
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as error:
            raise ValueError("HEX line %d is not hexadecimal." % line_number) from error
        if len(record) < 5 or len(record) != record[0] + 5:
            raise ValueError("HEX line %d has an invalid length." % line_number)
        if sum(record) & 0xFF:
            raise ValueError("HEX line %d has an invalid checksum." % line_number)

        length = record[0]
        offset = (record[1] << 8) | record[2]
        record_type = record[3]
        payload = record[4:4 + length]

        if record_type == 0x00:
            if length == 0:
                continue
            start = base_address + offset
            end = start + length
            if start < allowed_start or end > allowed_end:
                raise ValueError(range_message % (start, end - 1))
            first_address = start if first_address is None else min(first_address, start)
            last_address = end - 1 if last_address is None else max(last_address, end - 1)
            data_record_count += 1
            data_byte_count += length
            for index, value in enumerate(payload):
                address = start + index
                previous = memory.get(address)
                if previous is not None and previous != value:
                    raise ValueError(
                        "HEX contains conflicting data at 0x%08X (line %d)." %
                        (address, line_number)
                    )
                memory[address] = value
        elif record_type == 0x01:
            if length != 0:
                raise ValueError("HEX line %d has an invalid EOF record." % line_number)
            eof_seen = True
        elif record_type == 0x02:
            if length != 2:
                raise ValueError("HEX line %d has invalid segment address." % line_number)
            base_address = int.from_bytes(payload, "big") << 4
        elif record_type == 0x04:
            if length != 2:
                raise ValueError("HEX line %d has invalid extended address." % line_number)
            base_address = int.from_bytes(payload, "big") << 16
        elif record_type in (0x03, 0x05):
            if length != 4:
                raise ValueError("HEX line %d has an invalid start address." % line_number)
        else:
            raise ValueError("HEX line %d has unsupported record type 0x%02X." %
                             (line_number, record_type))

    if first_address is None or last_address is None:
        raise ValueError("%s HEX contains no data records." % label)
    if not eof_seen:
        raise ValueError("%s HEX has no EOF record." % label)
    if first_address != required_start:
        raise ValueError(
            "%s image must start at 0x%08X; found 0x%08X." %
            (label, required_start, first_address)
        )

    return ImageInfo(
        path=path,
        sha256=hashlib.sha256(raw_file).hexdigest().upper(),
        start_address=first_address,
        end_address=last_address,
        size=data_byte_count,
        data_record_count=data_record_count,
    ), memory


def inspect_image(path: Path) -> ImageInfo:
    image, memory = _inspect_hex(
        path,
        label="Application",
        allowed_start=APPLICATION_ADDRESS,
        allowed_end=FLASH_END_ADDRESS,
        required_start=APPLICATION_ADDRESS,
        range_message="HEX touches protected range 0x%08X..0x%08X.",
    )
    try:
        vector_data = bytes(memory[APPLICATION_ADDRESS + index] for index in range(8))
    except KeyError as error:
        raise ValueError("Application vector table is incomplete.") from error
    vector = inspect_application_vector(vector_data)
    if not vector.valid:
        raise ValueError("Application vector table is invalid: %s" % vector.reason)

    # The Bootloader validates one continuous range starting at APPLICATION_ADDRESS.
    # Intel HEX may be sparse, but the normal provisioning transaction erases S3-S7
    # first, so every hole inside that range is deterministically 0xFF in Flash.
    flash_span_size = image.end_address - APPLICATION_ADDRESS + 1
    canonical = bytearray(b"\xFF" * flash_span_size)
    for address, value in memory.items():
        canonical[address - APPLICATION_ADDRESS] = value
    flash_crc32 = zlib.crc32(canonical) & 0xFFFFFFFF

    return replace(
        image,
        initial_msp=vector.initial_msp,
        reset_vector=vector.reset_vector,
        flash_span_size=flash_span_size,
        flash_crc32=flash_crc32,
    )


def inspect_bootloader_image(path: Path) -> ImageInfo:
    image, memory = _inspect_hex(
        path,
        label="Bootloader",
        allowed_start=FLASH_START_ADDRESS,
        allowed_end=METADATA_ADDRESS,
        required_start=FLASH_START_ADDRESS,
        range_message="Bootloader HEX data is outside sectors 0..2 at 0x%08X..0x%08X.",
    )
    try:
        vector = bytes(memory[FLASH_START_ADDRESS + index] for index in range(8))
    except KeyError as error:
        raise ValueError("Bootloader HEX vector table is incomplete.") from error
    initial_sp = int.from_bytes(vector[:4], "little")
    reset_handler = int.from_bytes(vector[4:], "little")
    reset_address = reset_handler & ~1
    if not (
        0x20000000 <= initial_sp < 0x20020000 and
        reset_handler & 1 and
        FLASH_START_ADDRESS <= reset_address < METADATA_ADDRESS
    ):
        raise ValueError("Bootloader HEX vector table is not valid for B300 F407.")
    return image
