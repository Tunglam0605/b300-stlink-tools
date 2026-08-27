"""Strict Intel HEX inspection for B300 Application images."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .models import ImageInfo
from .policy import APPLICATION_ADDRESS, FLASH_END_ADDRESS


def inspect_image(path: Path) -> ImageInfo:
    path = Path(path).expanduser().resolve()
    try:
        raw_file = path.read_bytes()
        lines = raw_file.decode("ascii").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError("Cannot read application HEX: %s" % error) from error

    base_address = 0
    first_address: Optional[int] = None
    last_address: Optional[int] = None
    data_record_count = 0
    data_byte_count = 0
    eof_seen = False

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
            if start < APPLICATION_ADDRESS or end > FLASH_END_ADDRESS:
                raise ValueError(
                    "HEX touches protected range 0x%08X..0x%08X." % (start, end - 1)
                )
            first_address = start if first_address is None else min(first_address, start)
            last_address = end - 1 if last_address is None else max(last_address, end - 1)
            data_record_count += 1
            data_byte_count += length
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
        raise ValueError("Application HEX contains no application data records.")
    if not eof_seen:
        raise ValueError("Application HEX has no EOF record.")
    if first_address != APPLICATION_ADDRESS:
        raise ValueError(
            "Application image must start at 0x%08X; found 0x%08X." %
            (APPLICATION_ADDRESS, first_address)
        )

    return ImageInfo(
        path=path,
        sha256=hashlib.sha256(raw_file).hexdigest().upper(),
        start_address=first_address,
        end_address=last_address,
        size=data_byte_count,
        data_record_count=data_record_count,
    )
