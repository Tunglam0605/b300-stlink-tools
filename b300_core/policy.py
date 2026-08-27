"""Fixed STM32F407ZE flash map and provisioning policy."""

from __future__ import annotations

from typing import Tuple

from .models import FlashPlan, ImageInfo, ProbeRef, SectorInfo


FLASH_START_ADDRESS = 0x08000000
METADATA_ADDRESS = 0x0800C000
APPLICATION_ADDRESS = 0x08010000
FLASH_END_ADDRESS = 0x08080000
STLINK_PROVISION_MAGIC = 0x53544C4B

SECTORS: Tuple[SectorInfo, ...] = (
    SectorInfo(0, 0x08000000, 0x08003FFF, "Bootloader", False),
    SectorInfo(1, 0x08004000, 0x08007FFF, "Bootloader", False),
    SectorInfo(2, 0x08008000, 0x0800BFFF, "Bootloader", False),
    SectorInfo(3, 0x0800C000, 0x0800FFFF, "OTA metadata", False),
    SectorInfo(4, 0x08010000, 0x0801FFFF, "Application", True),
    SectorInfo(5, 0x08020000, 0x0803FFFF, "Application", True),
    SectorInfo(6, 0x08040000, 0x0805FFFF, "Application", True),
    SectorInfo(7, 0x08060000, 0x0807FFFF, "Application", True),
)


def build_flash_plan(image: ImageInfo, probe: ProbeRef) -> FlashPlan:
    if image.start_address != APPLICATION_ADDRESS:
        raise ValueError("Application image must start at 0x08010000.")
    if image.end_address >= FLASH_END_ADDRESS:
        raise ValueError("Application image exceeds B300 F407 flash.")
    return FlashPlan(image=image, probe=probe, erase_sectors=(3, 4, 5, 6, 7))


def sector_by_index(index: int) -> SectorInfo:
    try:
        return SECTORS[index]
    except (IndexError, TypeError) as error:
        raise ValueError("Sector index must be in range 0..7.") from error


def validate_read_range(address: int, length: int) -> None:
    if length <= 0:
        raise ValueError("Read length must be positive.")
    if address < FLASH_START_ADDRESS or address + length > FLASH_END_ADDRESS:
        raise ValueError("Read range is outside B300 flash.")

