"""Stable streaming and snapshot output for the command-line interface."""

from __future__ import annotations

import json
from typing import Iterable, Mapping, Optional

from b300_core.metadata import OTA_META_SIZE
from b300_core.models import DiagnosticReport, OtaMetadata, ProbeInfo, TargetInfo
from b300_core.policy import METADATA_ADDRESS, SECTORS


class Reporter:
    """Legacy event reporter; JSON output remains one line per event."""

    def __init__(self, as_json: bool) -> None:
        self.as_json = as_json

    def emit(self, event: str, **fields: object) -> None:
        record = {"event": event, **fields}
        if self.as_json:
            print(json.dumps(record, sort_keys=True), flush=True)
        else:
            print("[%s] %s" % (event, " ".join(
                "%s=%s" % (key, value) for key, value in fields.items())), flush=True)


def emit_snapshot(record: Mapping[str, object], as_json: bool, text: str) -> None:
    """Emit exactly one snapshot record, or its stable human-readable form."""
    if as_json:
        print(json.dumps(record, sort_keys=True), flush=True)
    else:
        print(text, flush=True)


def probe_record(index: int, probe: ProbeInfo) -> dict:
    return {
        "index": index,
        "name": probe.name,
        "serial": probe.serial,
        "serial_available": probe.serial_available,
        "usb_identity": probe.usb_identity,
        "source": probe.source,
        "status": probe.status,
    }


def format_probes_text(probes: Iterable[ProbeInfo]) -> str:
    lines = []
    for index, probe in enumerate(probes, start=1):
        lines.append(
            "index=%s type/name=%s serial=%s serial_available=%s usb_identity=%s source=%s status=%s" % (
                index,
                probe.name,
                probe.serial,
                probe.serial_available,
                probe.usb_identity,
                probe.source,
                probe.status,
            )
        )
    return "\n".join(lines)


def _sector_span(indices: Iterable[int], prefix: str = "Sector ") -> str:
    values = tuple(indices)
    if len(values) == 1:
        return "%s%d" % (prefix, values[0])
    if prefix == "S":
        return "S%d-S%d" % (values[0], values[-1])
    return "%s%d-%d" % (prefix, values[0], values[-1])


def _flash_sector_plan(erase_sectors: Iterable[int]) -> list[str]:
    erased = frozenset(erase_sectors)
    untouched_bootloader = tuple(
        sector.index for sector in SECTORS
        if sector.role == "Bootloader" and sector.index not in erased
    )
    erased_metadata = tuple(
        sector.index for sector in SECTORS
        if sector.role == "OTA metadata" and sector.index in erased
    )
    erased_application = tuple(
        sector.index for sector in SECTORS
        if sector.role == "Application" and sector.index in erased
    )
    labels = []
    if untouched_bootloader:
        labels.append("%s untouched" % _sector_span(untouched_bootloader, "S"))
    if erased_metadata:
        labels.append("%s metadata erase" % _sector_span(erased_metadata))
    if erased_application:
        labels.append("%s Application" % _sector_span(erased_application))
    return labels


def flash_target_record(target: TargetInfo) -> dict:
    return {
        "device_id": "0x%08X" % target.device_id,
        "flash_kib": target.flash_kib,
        "voltage": target.target_voltage,
        "rdp_enabled": target.readout_protected,
        "wrp_reported": target.protection_reported,
        "protected_sectors": list(target.protected_sectors),
        "protection": target.protection_summary,
    }


def flash_start_fields(plan, target: Optional[TargetInfo], *, dry_run: bool) -> dict:
    """Build append-only preflight fields from core image/plan/target models."""
    image = plan.image
    hardware_inspected = target is not None
    return {
        "application": str(image.path),
        "sha256": image.sha256,
        "start": "0x%08X" % image.start_address,
        "end": "0x%08X" % image.end_address,
        "dry_run": dry_run,
        "size": image.size,
        "flash_span_size": image.flash_span_size,
        "flash_crc32": ("0x%08X" % image.flash_crc32
                        if image.flash_crc32 is not None else None),
        "metadata_contract": {
            "address": "0x%08X" % METADATA_ADDRESS,
            "size": OTA_META_SIZE,
            "magic": "STLM",
            "state": "VERIFIED",
            "condition": "after_application_verified",
        },
        "initial_msp": ("0x%08X" % image.initial_msp
                        if image.initial_msp is not None else None),
        "reset_vector": ("0x%08X" % image.reset_vector
                         if image.reset_vector is not None else None),
        "selected_probe": ({"serial": plan.probe.serial} if hardware_inspected else None),
        "target": flash_target_record(target) if target is not None else None,
        "hardware_inspected": hardware_inspected,
        "erase_sectors": list(plan.erase_sectors),
        "sector_plan": _flash_sector_plan(plan.erase_sectors),
    }


def flash_result_fields(outcome, preflight_target: TargetInfo) -> dict:
    """Render final state without inferring target writes outside FlashResult."""
    fields = {
        "status": outcome.status,
        "failure_phase": outcome.failure_phase,
        "reason": outcome.reason,
        "next_action": outcome.next_action,
        "wrp_summary": preflight_target.protection_summary,
        "pc": None,
        "bkp1r": None,
        "application_running": bool(
            outcome.boot_verification is not None and outcome.boot_verification.passed
        ),
        "metadata_written": (
            metadata_record(outcome.written_metadata)
            if getattr(outcome, "written_metadata", None) is not None else None
        ),
        "metadata_readback_size": (
            len(outcome.verified_metadata_bytes)
            if getattr(outcome, "verified_metadata_bytes", None) is not None else None
        ),
        "metadata_confirmed": (
            metadata_record(outcome.confirmed_metadata)
            if getattr(outcome, "confirmed_metadata", None) is not None else None
        ),
    }
    if outcome.boot_verification is not None:
        fields.update({
            "pc": ("0x%08X" % outcome.boot_verification.pc
                   if outcome.boot_verification.pc is not None else None),
            "bkp1r": outcome.boot_verification.bkp1r,
            "reason": outcome.boot_verification.reason,
        })
    return fields


def memory_snapshot(command: str, address: int, data: bytes) -> dict:
    """Render a read-only memory snapshot with absolute, inclusive bounds."""
    return {
        "schema_version": 1,
        "command": command,
        "status": "ok",
        "address": "0x%08X" % address,
        "end_address": "0x%08X" % (address + len(data) - 1),
        "size": len(data),
        "data": data.hex(),
    }


def format_memory_rows(address: int, data: bytes) -> str:
    """Format a compact 16-byte hex dump without implying target write access."""
    return "\n".join(
        "%08X  %s" % (address + offset, " ".join("%02X" % value for value in data[offset:offset + 16]))
        for offset in range(0, len(data), 16)
    )


def metadata_record(metadata: OtaMetadata) -> dict:
    """Normalize decoded metadata for presentation without parsing it again."""
    record = {
        "classification": metadata.classification,
        "valid": metadata.valid,
        "magic": "0x%08X" % metadata.magic,
    }
    if metadata.classification == "ERASED":
        record.update({
            "format_version": None,
            "state": None,
            "state_name": None,
            "image_size": None,
            "image_crc32": None,
            "board_token": None,
            "sequence": None,
            "meta_crc32": None,
            "calculated_meta_crc32": None,
        })
        return record
    record.update({
        "format_version": metadata.format_version,
        "state": metadata.state,
        "state_name": metadata.state_name,
        "image_size": metadata.image_size,
        "image_crc32": "0x%08X" % metadata.image_crc32,
        "board_token": metadata.board_token,
        "sequence": metadata.sequence,
        "meta_crc32": "0x%08X" % metadata.meta_crc32,
        "calculated_meta_crc32": "0x%08X" % metadata.calculated_meta_crc32,
    })
    return record


def metadata_snapshot(metadata: OtaMetadata) -> dict:
    return {
        "schema_version": 1,
        "command": "metadata show",
        "status": "ok",
        "metadata": metadata_record(metadata),
    }


def format_metadata_text(metadata: OtaMetadata) -> str:
    record = metadata_record(metadata)
    return "\n".join("%s=%s" % (key, "-" if value is None else value)
                     for key, value in record.items())


def diagnostic_snapshot(command: str, report: DiagnosticReport) -> dict:
    """Render a core diagnostic report without reimplementing its decisions."""
    record = {
        "schema_version": 1,
        "command": command,
        "status": "ok" if report.conclusion == "READY_FOR_APPLICATION_FLASH" else "error",
        "checks": [{
            "name": check.name,
            "status": check.status,
            "code": check.code,
            "message": check.message,
            "next_action": check.next_action,
        } for check in report.checks],
        "conclusion": report.conclusion,
        "classification": report.conclusion,
        "reason_code": report.reason_code,
        "next_action": report.next_action,
    }
    if report.probe is not None:
        record["probe"] = probe_record(1, report.probe)
    if report.target is not None:
        target = report.target
        record["target"] = {
            "device_id": "0x%08X" % (target.device_id & 0xFFF),
            "mcu_family": "STM32F407" if (target.device_id & 0xFFF) == 0x413 else None,
            "flash_kib": target.flash_kib,
            "voltage": target.target_voltage,
            "rdp_enabled": target.readout_protected,
            "wrp_reported": target.protection_reported,
            "protected_sectors": list(target.protected_sectors),
            "protection": target.protection_summary,
        }
    if report.application_vector is not None:
        vector = report.application_vector
        record["application_vector"] = {
            "initial_msp": "0x%08X" % vector.initial_msp if vector.initial_msp is not None else None,
            "reset_vector": "0x%08X" % vector.reset_vector if vector.reset_vector is not None else None,
            "valid": vector.valid,
            "reason": vector.reason,
        }
    if report.metadata is not None:
        record["ota_metadata"] = {
            "classification": report.metadata.classification,
            "valid": report.metadata.valid,
            "state": report.metadata.state_name,
        }
    return record
