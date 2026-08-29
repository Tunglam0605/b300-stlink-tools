"""Fail-closed loader for the pinned offline B300 Bootloader resource."""

from __future__ import annotations

import json
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .app_metadata import (
    APP_METADATA_ADDRESS,
    APP_METADATA_FORMAT_VERSION,
    APP_METADATA_MAGIC_OTA,
    APP_METADATA_MAGIC_STLINK,
    APP_METADATA_SIZE,
    AppMetadataState,
)
from .hex_image import inspect_bootloader_image
from .models import ImageInfo


TRUSTED_BOOTLOADER_SHA256 = (
    "085E44E8339D21EE2D136D11F86C2103295812CB2438807774B232647D3F75A1"
)
TRUSTED_SOURCE_COMMIT = "88b74f649497a5ea9c64b5394470407678795f42"
TRUSTED_SOURCE_PATH = "firmware/bootloader/BOOTLOAER/bootloader_std.hex"
TRUSTED_SOURCE_GIT_BLOB = "51381f26edf343cee3054d0641dd65f5a2ee6f89"
TRUSTED_SOURCE_GIT_OBJECT_SIZE = 53308
TRUSTED_SOURCE_RAW_SHA256 = (
    "E89FF64430EE1CA4F4CC4D66BA85A9AFFD1F3DB3511860A83191B3FDC07AFA51"
)
TRUSTED_SOURCE_RAW_SIZE = 53308
TRUSTED_ARTIFACT_TRANSFORMATION = {
    "name": "lf_to_crlf",
    "canonical_line_ending": "LF",
    "artifact_line_ending": "CRLF",
}
TRUSTED_ARTIFACT_NAME = "b300_bootloader_f407ze_com3_v00060500.hex"
MANIFEST_NAME = "b300_bootloader_manifest.json"
TRUSTED_IMAGE_START = 0x08000000
TRUSTED_IMAGE_END = 0x08004BA3
TRUSTED_IMAGE_DATA_BYTES = 19364


@dataclass(frozen=True)
class TrustedBootloader:
    image: ImageInfo
    manifest_path: Path
    source_repository: str
    source_path: str
    source_commit: str
    source_git_blob: str
    source_git_object_size: int
    source_raw_sha256: str
    source_raw_size: int
    artifact_transformation: dict[str, str]
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


def _intel_hex_records(data: bytes, separator: bytes) -> tuple[bytes, ...]:
    if not data.endswith(separator):
        raise ValueError("Trusted Bootloader has malformed line endings.")
    lines = data.split(separator)
    if lines[-1] != b"" or any(not line for line in lines[:-1]):
        raise ValueError("Trusted Bootloader has malformed line endings.")
    records = []
    for line in lines[:-1]:
        if not line.startswith(b":"):
            raise ValueError("Trusted Bootloader contains a malformed Intel HEX record.")
        try:
            record = bytes.fromhex(line[1:].decode("ascii"))
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError("Trusted Bootloader contains a malformed Intel HEX record.") from error
        if len(record) < 5 or len(record) != record[0] + 5 or sum(record) & 0xFF:
            raise ValueError("Trusted Bootloader contains a malformed Intel HEX record.")
        records.append(record)
    return tuple(records)


def _canonical_source_from_artifact(data: bytes) -> bytes:
    residue = data.replace(b"\r\n", b"")
    if b"\r" in residue or b"\n" in residue:
        raise ValueError("Trusted Bootloader has malformed line endings; expected CRLF only.")
    artifact_records = _intel_hex_records(data, b"\r\n")
    canonical = data.replace(b"\r\n", b"\n")
    canonical_records = _intel_hex_records(canonical, b"\n")
    if artifact_records != canonical_records:
        raise ValueError("Trusted Bootloader Intel HEX records differ after the declared transformation.")
    return canonical


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
    app_metadata = manifest.get("app_metadata", {})
    provenance_valid = (
        isinstance(manifest, dict) and
        isinstance(source, dict) and
        isinstance(profile, dict) and
        isinstance(observed, dict) and
        isinstance(app_metadata, dict) and
        isinstance(manifest.get("allowed_data_range", {}), dict) and
        isinstance(app_metadata.get("stlink_initial_state", {}), dict) and
        set(manifest) == {
            "schema_version", "artifact", "sha256", "source", "profile",
            "allowed_data_range", "observed_data_range", "app_metadata",
        } and
        set(source) == {
            "repository", "audited_repository_head", "path", "git_blob",
            "git_object_size", "raw_sha256", "raw_size",
            "artifact_transformation", "commit", "project", "target",
            "currentness_evidence",
        } and
        set(profile) == {
            "mcu", "flash_kib", "board_token", "transport",
            "firmware_version", "protocol_version",
        } and
        set(manifest.get("allowed_data_range", {})) == {"start", "end", "sectors"} and
        set(observed) == {"start", "end", "data_bytes"} and
        set(app_metadata) == {
            "address", "size", "format_version", "ota_magic", "stlink_magic",
            "stlink_initial_state",
        } and
        set(app_metadata.get("stlink_initial_state", {})) == {"name", "value"} and
        manifest.get("schema_version") == 1 and
        manifest.get("artifact") == TRUSTED_ARTIFACT_NAME and
        str(manifest.get("sha256", "")).upper() == TRUSTED_BOOTLOADER_SHA256 and
        source.get("repository") == "https://github.com/Tunglam0605/TungLamvsOTA-B300.git" and
        source.get("audited_repository_head") == TRUSTED_SOURCE_COMMIT and
        source.get("path") == TRUSTED_SOURCE_PATH and
        source.get("commit") == TRUSTED_SOURCE_COMMIT and
        source.get("git_blob") == TRUSTED_SOURCE_GIT_BLOB and
        source.get("git_object_size") == TRUSTED_SOURCE_GIT_OBJECT_SIZE and
        source.get("raw_sha256") == TRUSTED_SOURCE_RAW_SHA256 and
        source.get("raw_size") == TRUSTED_SOURCE_RAW_SIZE and
        source.get("artifact_transformation") == TRUSTED_ARTIFACT_TRANSFORMATION and
        source.get("project") == "firmware/bootloader/bootloader_std.uvprojx" and
        source.get("target") == "BOOTLOADER_STD" and
        isinstance(source.get("currentness_evidence"), str) and
        bool(source.get("currentness_evidence")) and
        profile.get("mcu") == "STM32F407ZET6" and
        profile.get("flash_kib") == 512 and
        profile.get("firmware_version") == "0x00060500" and
        profile.get("protocol_version") == "0x00030000" and
        profile.get("board_token") == "B300_F407ZE" and
        profile.get("transport") == "COM3" and
        manifest["allowed_data_range"].get("start") == "0x08000000" and
        manifest["allowed_data_range"].get("end") == "0x0800BFFF" and
        manifest["allowed_data_range"].get("sectors") == [0, 1, 2] and
        observed.get("start") == "0x08000000" and
        observed.get("end") == "0x08004BA3" and
        observed.get("data_bytes") == TRUSTED_IMAGE_DATA_BYTES and
        app_metadata.get("address") == "0x%08X" % APP_METADATA_ADDRESS and
        app_metadata.get("size") == APP_METADATA_SIZE and
        app_metadata.get("format_version") == APP_METADATA_FORMAT_VERSION and
        app_metadata.get("ota_magic") == "0x%08X" % APP_METADATA_MAGIC_OTA and
        app_metadata.get("stlink_magic") == "0x%08X" % APP_METADATA_MAGIC_STLINK and
        app_metadata["stlink_initial_state"].get("name") == AppMetadataState.VERIFIED.name and
        app_metadata["stlink_initial_state"].get("value") == int(AppMetadataState.VERIFIED)
    )
    if not provenance_valid:
        raise ValueError("Trusted Bootloader manifest provenance does not match the pinned release.")

    artifact_path = root / TRUSTED_ARTIFACT_NAME
    try:
        artifact_bytes = artifact_path.read_bytes()
    except OSError as error:
        raise ValueError("Trusted Bootloader artifact is unavailable: %s" % error) from error
    canonical_source = _canonical_source_from_artifact(artifact_bytes)
    canonical_raw_sha256 = hashlib.sha256(canonical_source).hexdigest().upper()
    if (len(canonical_source) != TRUSTED_SOURCE_RAW_SIZE or
            canonical_raw_sha256 != TRUSTED_SOURCE_RAW_SHA256):
        raise ValueError("Trusted Bootloader canonical source size or raw SHA-256 does not match the pin.")
    git_object = b"blob %d\0" % len(canonical_source) + canonical_source
    if hashlib.sha1(git_object).hexdigest() != TRUSTED_SOURCE_GIT_BLOB:
        raise ValueError("Trusted Bootloader canonical source Git blob does not match the pin.")

    image = inspect_bootloader_image(artifact_path)
    if image.sha256 != TRUSTED_BOOTLOADER_SHA256:
        raise ValueError("Trusted Bootloader SHA-256 does not match the pinned artifact.")
    if (image.start_address != TRUSTED_IMAGE_START or
            image.end_address != TRUSTED_IMAGE_END or
            image.size != TRUSTED_IMAGE_DATA_BYTES):
        raise ValueError("Trusted Bootloader observed address range does not match the manifest.")
    return TrustedBootloader(
        image=image,
        manifest_path=manifest_path,
        source_repository=str(source.get("repository", "")),
        source_path=source["path"],
        source_commit=source["commit"],
        source_git_blob=source["git_blob"],
        source_git_object_size=source["git_object_size"],
        source_raw_sha256=source["raw_sha256"],
        source_raw_size=source["raw_size"],
        artifact_transformation=dict(source["artifact_transformation"]),
        firmware_version=profile["firmware_version"],
        protocol_version=profile["protocol_version"],
        board_token=profile["board_token"],
        transport=profile["transport"],
    )
