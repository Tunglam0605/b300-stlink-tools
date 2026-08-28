"""Policy for the separately authorized B300 factory Bootloader workflow."""

from __future__ import annotations

from .models import (
    FactoryPlan,
    FactoryPreview,
    ImageInfo,
    ProbeRef,
    TargetInfo,
)
from .policy import FLASH_START_ADDRESS, METADATA_ADDRESS, validate_target_for_provisioning


FACTORY_ERASE_SECTORS = (0, 1, 2)


def _validate_bootloader_image(image: ImageInfo) -> None:
    if image.start_address != FLASH_START_ADDRESS:
        raise ValueError("Bootloader image must start at 0x08000000.")
    if image.end_address >= METADATA_ADDRESS:
        raise ValueError("Bootloader image must remain within sectors 0..2.")


def build_factory_preview(image: ImageInfo, probe: ProbeRef) -> FactoryPreview:
    _validate_bootloader_image(image)
    return FactoryPreview(image=image, probe=probe, erase_sectors=FACTORY_ERASE_SECTORS)


def build_factory_plan(image: ImageInfo, probe: ProbeRef,
                       target: TargetInfo) -> FactoryPlan:
    _validate_bootloader_image(image)
    validate_target_for_provisioning(target)
    if not target.protection_reported:
        raise ValueError(
            "OpenOCD did not report sector write-protection; factory provisioning is blocked."
        )
    return FactoryPlan(
        image=image,
        probe=probe,
        erase_sectors=FACTORY_ERASE_SECTORS,
        target=target,
    )
