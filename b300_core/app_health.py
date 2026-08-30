"""Pure installed-Application lifecycle and bootability evaluation."""

from __future__ import annotations

import zlib
from typing import Optional

from .application_vector import inspect_application_vector
from .metadata import (
    OTA_META_MAGIC_OTA, OTA_META_MAGIC_STLINK, STATE_CONFIRMED, STATE_IN_PROGRESS,
    STATE_VERIFIED,
)
from .models import ApplicationHealth, OtaMetadata


def evaluate_application_health(
    metadata: OtaMetadata, application_data: Optional[bytes], read_error: Optional[str] = None
) -> ApplicationHealth:
    """Classify one bounded read without accessing hardware or mutating target state."""
    data = bytes(application_data) if application_data is not None else None
    vector = inspect_application_vector(data[:8]) if data is not None and len(data) >= 8 else None
    complete_image = bool(
        metadata.valid and data is not None and len(data) == metadata.image_size
    )
    actual_crc = None
    image_crc_valid = None
    if complete_image:
        actual_crc = zlib.crc32(data) & 0xFFFFFFFF
        image_crc_valid = actual_crc == metadata.image_crc32

    source_state_allows_boot = (
        metadata.magic == OTA_META_MAGIC_OTA and
        metadata.state in (STATE_VERIFIED, STATE_CONFIRMED)
    ) or (
        metadata.magic == OTA_META_MAGIC_STLINK and metadata.state == STATE_CONFIRMED
    )
    bootable = bool(
        metadata.valid and source_state_allows_boot and
        vector is not None and vector.valid and image_crc_valid is True
    )

    if metadata.classification == "ERASED":
        lifecycle = "UNMANAGED_RECOVERY"
        reason = "Application Metadata is erased and does not prove bootability."
        next_action = "Provision a validated Application or use the approved OTA recovery path to recreate metadata."
    elif not metadata.valid:
        lifecycle = "INVALID_METADATA"
        reason = "Application Metadata is invalid and cannot prove bootability."
        next_action = "Do not trust the installed Application; reprovision a validated image or recover through OTA."
    elif metadata.magic == OTA_META_MAGIC_OTA and metadata.state == STATE_IN_PROGRESS:
        lifecycle = "OTA_IN_PROGRESS"
        reason = "OTA Application Metadata is IN_PROGRESS and is not bootable."
        next_action = "Complete or recover the OTA transaction; do not mark the image bootable manually."
    elif metadata.magic == OTA_META_MAGIC_STLINK and metadata.state == STATE_VERIFIED:
        lifecycle = "STLINK_VERIFIED_PENDING"
        reason = "ST-Link Application Metadata is VERIFIED and pending one-shot Bootloader consumption."
        next_action = "Reset once and let Bootloader v0.6.5 confirm the ST-Link image; inspect Bootloader if VERIFIED persists."
    elif read_error is not None or not complete_image:
        lifecycle = "IMAGE_READ_INCOMPLETE"
        reason = read_error or (
            "Application image read was short: expected %d bytes, received %d." %
            (metadata.image_size, 0 if data is None else len(data))
        )
        next_action = "Reconnect ST-Link and retry the read-only health check before making a provisioning decision."
    elif vector is None or not vector.valid:
        lifecycle = "INVALID_VECTOR"
        reason = "Application vector is invalid and cannot prove bootability."
        next_action = "Reprovision a validated Application image; do not boot the current image."
    elif image_crc_valid is False:
        lifecycle = "IMAGE_CRC_MISMATCH"
        reason = "Application image CRC does not match metadata and cannot prove bootability."
        next_action = "Reprovision or OTA-recover the Application; do not reset into the mismatched image."
    elif bootable:
        lifecycle = "BOOTABLE"
        reason = "Application Metadata, image CRC, and vector permit bootability."
        next_action = "No action is required."
    else:
        lifecycle = "NOT_BOOTABLE"
        reason = "Application lifecycle state does not permit bootability."
        next_action = "Inspect metadata source/state and use the approved provisioning or OTA recovery flow."

    return ApplicationHealth(
        metadata=metadata,
        application_vector=vector,
        image_crc_valid=image_crc_valid,
        actual_image_crc32=actual_crc,
        bootable=bootable,
        lifecycle=lifecycle,
        reason=reason,
        next_action=next_action,
        bytes_checked=0 if data is None else len(data),
    )
