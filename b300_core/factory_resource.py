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
CATALOG_NAME = "b300_bootloader_catalog.json"
TRUSTED_CATALOG_SHA256 = "1A053672005A19D3B9A08CAC37012B32CE2A79CC26C194DEAFDE3BAF99FF5306"
TRUSTED_IMAGE_START = 0x08000000
TRUSTED_IMAGE_END = 0x08004BA3
TRUSTED_IMAGE_DATA_BYTES = 19364


@dataclass(frozen=True)
class BootloaderProfile:
    profile_id: str
    display_name: str
    manifest_name: str
    selectable: bool
    support_status: str
    factory_backend: str
    board_family: str
    mcu: str
    flash_kib: int
    board_token: str
    logical_port: str
    physical_interface: str
    peripheral: str
    baudrate: int
    tx_pin: str
    rx_pin: str
    direction_pin: str
    direction_tx_level: str
    direction_rx_level: str
    dma_rx: str
    protocol_version: str
    bootloader_memory: str
    metadata_memory: str
    application_memory: str
    capabilities: tuple[str, ...]
    operator_notes: tuple[str, ...]


@dataclass(frozen=True)
class TrustedBootloader:
    image: ImageInfo
    manifest_path: Path
    catalog_path: Path
    profile: BootloaderProfile
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


def _load_profile_catalog(root: Path) -> tuple[Path, str, tuple[BootloaderProfile, ...]]:
    catalog_path = root / CATALOG_NAME
    try:
        catalog_bytes = catalog_path.read_bytes()
    except OSError as error:
        raise ValueError("Trusted Bootloader catalog is unavailable: %s" % error) from error
    digest = hashlib.sha256(catalog_bytes).hexdigest().upper()
    if digest != TRUSTED_CATALOG_SHA256:
        raise ValueError("Trusted Bootloader catalog SHA-256 does not match this release.")
    try:
        catalog = json.loads(catalog_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Trusted Bootloader catalog is invalid: %s" % error) from error
    if (not isinstance(catalog, dict) or
            set(catalog) != {"schema_version", "publisher", "policy", "default_profile_id", "profiles"} or
            catalog.get("schema_version") != 1 or
            catalog.get("publisher") != "TungLamAutomation" or
            catalog.get("policy") != "publisher-controlled" or
            not isinstance(catalog.get("default_profile_id"), str) or
            not isinstance(catalog.get("profiles"), list) or
            not catalog["profiles"]):
        raise ValueError("Trusted Bootloader catalog schema/publisher policy is invalid.")

    profiles = []
    seen = set()
    for raw in catalog["profiles"]:
        if not isinstance(raw, dict) or set(raw) != {
            "profile_id", "display_name", "manifest", "selectable", "support_status",
            "factory_backend", "target", "ota", "memory", "capabilities", "operator_notes",
        }:
            raise ValueError("Trusted Bootloader profile schema is invalid.")
        target = raw.get("target")
        ota = raw.get("ota")
        memory = raw.get("memory")
        if (not isinstance(target, dict) or
                set(target) != {"board_family", "mcu", "flash_kib", "board_token"} or
                not isinstance(ota, dict) or
                set(ota) != {
                    "logical_port", "physical_interface", "peripheral", "baudrate",
                    "tx_pin", "rx_pin", "direction_pin", "direction_tx_level",
                    "direction_rx_level", "dma_rx", "protocol_version",
                } or
                not isinstance(memory, dict) or
                set(memory) != {"bootloader", "metadata", "application"} or
                not isinstance(raw.get("capabilities"), list) or
                not all(isinstance(item, str) and item for item in raw["capabilities"]) or
                not isinstance(raw.get("operator_notes"), list) or
                not all(isinstance(item, str) and item for item in raw["operator_notes"])):
            raise ValueError("Trusted Bootloader profile details are invalid.")
        profile_id = raw.get("profile_id")
        manifest_name = raw.get("manifest")
        if (not isinstance(profile_id, str) or not profile_id or profile_id in seen or
                not isinstance(manifest_name, str) or not manifest_name or
                Path(manifest_name).name != manifest_name):
            raise ValueError("Trusted Bootloader profile identity/path is invalid.")
        seen.add(profile_id)
        profiles.append(BootloaderProfile(
            profile_id=profile_id,
            display_name=str(raw["display_name"]),
            manifest_name=manifest_name,
            selectable=bool(raw["selectable"]),
            support_status=str(raw["support_status"]),
            factory_backend=str(raw["factory_backend"]),
            board_family=str(target["board_family"]),
            mcu=str(target["mcu"]),
            flash_kib=int(target["flash_kib"]),
            board_token=str(target["board_token"]),
            logical_port=str(ota["logical_port"]),
            physical_interface=str(ota["physical_interface"]),
            peripheral=str(ota["peripheral"]),
            baudrate=int(ota["baudrate"]),
            tx_pin=str(ota["tx_pin"]),
            rx_pin=str(ota["rx_pin"]),
            direction_pin=str(ota["direction_pin"]),
            direction_tx_level=str(ota["direction_tx_level"]),
            direction_rx_level=str(ota["direction_rx_level"]),
            dma_rx=str(ota["dma_rx"]),
            protocol_version=str(ota["protocol_version"]),
            bootloader_memory=str(memory["bootloader"]),
            metadata_memory=str(memory["metadata"]),
            application_memory=str(memory["application"]),
            capabilities=tuple(raw["capabilities"]),
            operator_notes=tuple(raw["operator_notes"]),
        ))
    default_id = catalog["default_profile_id"]
    if default_id not in seen:
        raise ValueError("Trusted Bootloader catalog default profile does not exist.")
    return catalog_path, default_id, tuple(profiles)


def list_trusted_bootloader_profiles(resource_directory: Optional[Path] = None) -> tuple[BootloaderProfile, ...]:
    root = Path(resource_directory or _default_resource_directory()).resolve()
    _, _, profiles = _load_profile_catalog(root)
    return tuple(profile for profile in profiles if profile.selectable)


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
        resource_directory: Optional[Path] = None,
        profile_id: Optional[str] = None) -> TrustedBootloader:
    root = Path(resource_directory or _default_resource_directory()).resolve()
    catalog_path, default_profile_id, profiles = _load_profile_catalog(root)
    selected_id = profile_id or default_profile_id
    profile = next((item for item in profiles if item.profile_id == selected_id), None)
    if profile is None or not profile.selectable:
        raise ValueError("Trusted Bootloader profile is unavailable in this publisher release.")
    # v0.9.0 currently ships one production backend. Future F4/H7/alternate-COM
    # profiles are added only by a publisher release together with matching core support.
    if (profile.factory_backend != "stm32f4x" or
            profile.mcu != "STM32F407ZET6" or profile.flash_kib != 512 or
            profile.board_token != "B300_F407ZE" or profile.logical_port != "COM3" or
            profile.peripheral != "USART1" or profile.baudrate != 230400 or
            profile.tx_pin != "PB6" or profile.rx_pin != "PB7" or
            profile.direction_pin != "PC13" or profile.direction_tx_level != "LOW" or
            profile.direction_rx_level != "HIGH" or
            profile.dma_rx != "DMA2 Stream5 Channel 4" or
            profile.protocol_version != "0x00030000"):
        raise ValueError("Trusted Bootloader profile is not supported by this release backend.")
    manifest_path = root / profile.manifest_name
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Trusted Bootloader manifest is unavailable or invalid: %s" % error) from error

    source = manifest.get("source", {})
    manifest_profile = manifest.get("profile", {})
    observed = manifest.get("observed_data_range", {})
    app_metadata = manifest.get("app_metadata", {})
    provenance_valid = (
        isinstance(manifest, dict) and
        isinstance(source, dict) and
        isinstance(manifest_profile, dict) and
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
        set(manifest_profile) == {
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
        profile.manifest_name == MANIFEST_NAME and
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
        manifest_profile.get("mcu") == "STM32F407ZET6" and
        manifest_profile.get("flash_kib") == 512 and
        manifest_profile.get("firmware_version") == "0x00060500" and
        manifest_profile.get("protocol_version") == "0x00030000" and
        manifest_profile.get("board_token") == "B300_F407ZE" and
        manifest_profile.get("transport") == "COM3" and
        manifest_profile.get("mcu") == profile.mcu and
        manifest_profile.get("flash_kib") == profile.flash_kib and
        manifest_profile.get("board_token") == profile.board_token and
        manifest_profile.get("transport") == profile.logical_port and
        manifest_profile.get("protocol_version") == profile.protocol_version and
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
        catalog_path=catalog_path,
        profile=profile,
        source_repository=str(source.get("repository", "")),
        source_path=source["path"],
        source_commit=source["commit"],
        source_git_blob=source["git_blob"],
        source_git_object_size=source["git_object_size"],
        source_raw_sha256=source["raw_sha256"],
        source_raw_size=source["raw_size"],
        artifact_transformation=dict(source["artifact_transformation"]),
        firmware_version=manifest_profile["firmware_version"],
        protocol_version=manifest_profile["protocol_version"],
        board_token=manifest_profile["board_token"],
        transport=manifest_profile["transport"],
    )


def list_trusted_bootloaders(resource_directory: Optional[Path] = None) -> tuple[TrustedBootloader, ...]:
    root = Path(resource_directory or _default_resource_directory()).resolve()
    _, _, profiles = _load_profile_catalog(root)
    return tuple(
        load_trusted_bootloader(root, profile.profile_id)
        for profile in profiles
        if profile.selectable
    )
