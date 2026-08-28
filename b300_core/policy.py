"""Fixed STM32F407ZE flash map and provisioning policy."""

from __future__ import annotations

from typing import Tuple

from .models import FlashPlan, FlashPreview, ImageInfo, ProbeRef, SectorInfo, TargetInfo


FLASH_START_ADDRESS = 0x08000000
METADATA_ADDRESS = 0x0800C000
APPLICATION_ADDRESS = 0x08010000
FLASH_END_ADDRESS = 0x08080000
SUPPORTED_DEVICE_ID = 0x413
SUPPORTED_FLASH_KIB = 512

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


def _validate_image_policy(image: ImageInfo) -> None:
    if image.start_address != APPLICATION_ADDRESS:
        raise ValueError("Application image must start at 0x08010000.")
    if image.end_address >= FLASH_END_ADDRESS:
        raise ValueError("Application image exceeds B300 F407 flash.")


def build_flash_preview(image: ImageInfo, probe: ProbeRef) -> FlashPreview:
    _validate_image_policy(image)
    return FlashPreview(image=image, probe=probe, erase_sectors=(3, 4, 5, 6, 7))


def build_flash_plan(image: ImageInfo, probe: ProbeRef,
                     target: TargetInfo) -> FlashPlan:
    _validate_image_policy(image)
    validate_target_for_provisioning(target)
    validate_bootloader_write_protection(target)
    return FlashPlan(
        image=image,
        probe=probe,
        erase_sectors=(3, 4, 5, 6, 7),
        target=target,
    )


def validate_target_for_provisioning(target: TargetInfo) -> None:
    """Reject targets outside the fixed B300 STM32F407 512-KiB policy."""
    if ((target.device_id & 0xFFF) != SUPPORTED_DEVICE_ID or
            target.flash_kib != SUPPORTED_FLASH_KIB):
        raise ValueError(
            "Unsupported target: expected STM32F407 device 0x413 with 512 KiB flash; "
            "found device 0x%03X with %d KiB." %
            (target.device_id & 0xFFF, target.flash_kib)
        )
    if target.readout_protected:
        raise ValueError(
            "Target readout protection/security is enabled; B300 Tools will not modify RDP. "
            "Use the approved production/OTA recovery process instead."
        )


def validate_bootloader_write_protection(target: TargetInfo) -> None:
    """Require read-only evidence that Bootloader sectors 0..2 are WRP protected."""
    if not target.protection_reported:
        raise ValueError(
            "OpenOCD did not report sector write-protection; refusing normal Application flash."
        )
    missing = tuple(sector for sector in (0, 1, 2) if sector not in target.protected_sectors)
    if missing:
        raise ValueError(
            "Bootloader WRP is not enabled for Sector %s; use the separate factory provisioning flow." %
            ",".join(str(item) for item in missing)
        )


def sector_by_index(index: int) -> SectorInfo:
    if (not isinstance(index, int) or isinstance(index, bool) or
            not 0 <= index < len(SECTORS)):
        raise ValueError("Sector index must be in range 0..7.")
    return SECTORS[index]


def validate_read_range(address: int, length: int) -> None:
    if (not isinstance(address, int) or isinstance(address, bool) or
            not isinstance(length, int) or isinstance(length, bool)):
        raise ValueError("Read address and length must be integers.")
    if length <= 0:
        raise ValueError("Read length must be positive.")
    if address < FLASH_START_ADDRESS or address + length > FLASH_END_ADDRESS:
        raise ValueError("Read range is outside B300 flash.")
