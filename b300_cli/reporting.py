"""Stable streaming and snapshot output for the command-line interface."""

from __future__ import annotations

import json
from typing import Iterable, Mapping

from b300_core.models import DiagnosticReport, OtaMetadata, ProbeInfo


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
