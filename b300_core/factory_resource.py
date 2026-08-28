"""Fail-closed loader for the pinned offline B300 Bootloader resource."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .hex_image import inspect_bootloader_image
from .models import ImageInfo


TRUSTED_BOOTLOADER_SHA256 = (
    "657F71605E00795BEA3C5601AAF569104E74D9DEE8D5B6E602514C4D72264F05"
)
TRUSTED_SOURCE_COMMIT = "92e70f8e1cc94c17be39034fcc9a20e385325a2f"
TRUSTED_SOURCE_PATH = "firmware/bootloader/BOOTLOAER/bootloader_std.hex"
TRUSTED_ARTIFACT_NAME = "b300_bootloader_f407ze_com3_v00050001.hex"
MANIFEST_NAME = "b300_bootloader_manifest.json"


@dataclass(frozen=True)
class TrustedBootloader:
    image: ImageInfo
    manifest_path: Path
    source_repository: str
    source_path: str
    source_commit: str
    firmware_version: str
    protocol_version: str
    board_token: str
    transport: str


def _default_resource_directory() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "resources" / "firmware"
    adjacent = Path(sys.executable).resolve().parent / "resources" / "firmware"
    if getattr(sys, "frozen", False) and adjacent.is_dir():
        return adjacent
    return Path(__file__).resolve().parents[1] / "resources" / "firmware"


def load_trusted_bootloader(
        resource_directory: Optional[Path] = None) -> TrustedBootloader:
    root = Path(resource_directory or _default_resource_directory()).resolve()
    manifest_path = root / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Trusted Bootloader manifest is unavailable or invalid: %s" % error) from error

    source = manifest.get("source", {})
    profile = manifest.get("profile", {})
    observed = manifest.get("observed_data_range", {})
    provenance_valid = (
        manifest.get("schema_version") == 1 and
        manifest.get("artifact") == TRUSTED_ARTIFACT_NAME and
        str(manifest.get("sha256", "")).upper() == TRUSTED_BOOTLOADER_SHA256 and
        source.get("path") == TRUSTED_SOURCE_PATH and
        source.get("commit") == TRUSTED_SOURCE_COMMIT and
        profile.get("firmware_version") == "0x00050001" and
        profile.get("protocol_version") == "0x00030000" and
        profile.get("board_token") == "B300_F407ZE" and
        profile.get("transport") == "COM3" and
        observed.get("start") == "0x08000000" and
        observed.get("end") == "0x08004B4F"
    )
    if not provenance_valid:
        raise ValueError("Trusted Bootloader manifest provenance does not match the pinned release.")

    image = inspect_bootloader_image(root / TRUSTED_ARTIFACT_NAME)
    if image.sha256 != TRUSTED_BOOTLOADER_SHA256:
        raise ValueError("Trusted Bootloader SHA-256 does not match the pinned artifact.")
    if image.start_address != 0x08000000 or image.end_address != 0x08004B4F:
        raise ValueError("Trusted Bootloader observed address range does not match the manifest.")
    return TrustedBootloader(
        image=image,
        manifest_path=manifest_path,
        source_repository=str(source.get("repository", "")),
        source_path=source["path"],
        source_commit=source["commit"],
        firmware_version=profile["firmware_version"],
        protocol_version=profile["protocol_version"],
        board_token=profile["board_token"],
        transport=profile["transport"],
    )
