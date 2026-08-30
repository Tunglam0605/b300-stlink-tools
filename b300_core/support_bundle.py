"""Privacy-bounded read-only diagnostic support bundle generation."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from .diagnostics import DiagnosticsService
from .gdb_runtime import GdbRuntimeInfo, gdb_runtime_info
from .models import ApplicationHealth, DiagnosticReport, ProbeInfo, ProbeRef
from .probe import list_probes
from .service import B300Service


SUPPORT_BUNDLE_SCHEMA_VERSION = 1
SUPPORT_BUNDLE_MAX_BYTES = 2 * 1024 * 1024
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/][^\r\n\t\"']+")
_UNIX_HOME_PATH = re.compile(r"/(?:home|Users)/[^/\s]+(?:/[^\s\"']*)?")


def _portable_basename(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return str(value).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _safe_text(value: object, secrets=()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "<REDACTED>")
    # Redact complete absolute paths before HOME fallback so child path names are not retained.
    text = _WINDOWS_ABSOLUTE_PATH.sub("<PATH>", text)
    text = _UNIX_HOME_PATH.sub("<PATH>", text)
    home = str(Path.home())
    if home:
        text = text.replace(home, "<HOME>").replace(home.replace("\\", "/"), "<HOME>")
    return text


@dataclass(frozen=True)
class SupportBundleResult:
    path: Path
    sha256: str
    size_bytes: int
    snapshot: dict


def _metadata_record(metadata) -> Optional[dict]:
    if metadata is None:
        return None
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


def _vector_record(vector) -> Optional[dict]:
    if vector is None:
        return None
    return {
        "initial_msp": "0x%08X" % vector.initial_msp if vector.initial_msp is not None else None,
        "reset_vector": "0x%08X" % vector.reset_vector if vector.reset_vector is not None else None,
        "valid": vector.valid,
        "reason": vector.reason,
    }


def _target_record(target) -> Optional[dict]:
    if target is None:
        return None
    return {
        "device_id": "0x%03X" % (target.device_id & 0xFFF),
        "flash_kib": target.flash_kib,
        "voltage": round(float(target.target_voltage), 4),
        "rdp_enabled": target.readout_protected,
        "wrp_reported": target.protection_reported,
        "protected_sectors": list(target.protected_sectors),
        "protection_summary": target.protection_summary,
    }


def _probe_record(probe: Optional[ProbeInfo]) -> Optional[dict]:
    if probe is None:
        return None
    # Deliberately excludes serial and USB identity: these are not needed for support triage.
    return {
        "name": probe.name,
        "source": probe.source,
        "serial_available": probe.serial_available,
        "status": probe.status,
    }


def _health_record(health: Optional[ApplicationHealth]) -> Optional[dict]:
    if health is None:
        return None
    return {
        "lifecycle": health.lifecycle,
        "bootable": health.bootable,
        "reason": health.reason,
        "next_action": health.next_action,
        "bytes_checked": health.bytes_checked,
        "image_crc_valid": health.image_crc_valid,
        "expected_image_crc32": (
            "0x%08X" % health.metadata.image_crc32 if health.metadata.valid else None
        ),
        "actual_image_crc32": (
            "0x%08X" % health.actual_image_crc32
            if health.actual_image_crc32 is not None else None
        ),
        "application_vector": _vector_record(health.application_vector),
        "metadata": _metadata_record(health.metadata),
    }


def _diagnostic_record(report: DiagnosticReport) -> dict:
    secrets = ()
    if report.probe is not None:
        secrets = (report.probe.serial, report.probe.usb_identity)
    return {
        "conclusion": _safe_text(report.conclusion, secrets),
        "reason_code": _safe_text(report.reason_code, secrets),
        "next_action": _safe_text(report.next_action, secrets),
        "checks": [
            {
                "name": _safe_text(check.name, secrets),
                "status": _safe_text(check.status, secrets),
                "code": _safe_text(check.code, secrets),
                "message": _safe_text(check.message, secrets),
                "next_action": _safe_text(check.next_action, secrets),
            }
            for check in report.checks
        ],
        "probe": _probe_record(report.probe),
        "target": _target_record(report.target),
        "application_vector": _vector_record(report.application_vector),
        "metadata": _metadata_record(report.metadata),
    }


def _runtime_record(runtime: GdbRuntimeInfo, openocd_available: bool, openocd_executable: str,
                    openocd_version: str) -> dict:
    return {
        "gdb_available": runtime.available,
        "gdb_version": runtime.version,
        "gdb_platform": runtime.platform,
        # Basenames are enough to identify runtime type without leaking host paths.
        "gdb_executable": _portable_basename(runtime.path),
        "openocd_available": bool(openocd_available),
        "openocd_executable": _portable_basename(openocd_executable),
        "openocd_version": openocd_version,
    }


def collect_support_snapshot(
    *,
    version: str,
    openocd_version: str,
    service: Optional[B300Service] = None,
    probe_discovery: Callable[[], Sequence[ProbeInfo]] = list_probes,
    gdb_info: Callable[[], GdbRuntimeInfo] = gdb_runtime_info,
    probe_serial: Optional[str] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> dict:
    """Collect bounded read-only support evidence; subsystem failures remain data, not exceptions."""
    selected_service = service or B300Service()
    runtime = gdb_info()
    openocd_available, openocd_executable = selected_service.doctor()
    report = DiagnosticsService(
        service=selected_service,
        probe_discovery=probe_discovery,
        gdb_info=lambda: runtime,
    ).run(probe_serial)

    health = None
    health_error = None
    if report.probe is not None and report.target is not None:
        try:
            health = selected_service.inspect_application_health(ProbeRef(report.probe.serial))
        except (OSError, RuntimeError, ValueError) as error:
            # Keep only the exception class. Raw transport output can contain local paths/device IDs.
            health_error = error.__class__.__name__

    timestamp = (now or (lambda: datetime.now(timezone.utc)))()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    snapshot = {
        "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
        "generated_at_utc": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tool": {
            "version": str(version),
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
            "python": "%d.%d.%d" % sys.version_info[:3],
        },
        "privacy": {
            "probe_serial_included": False,
            "usb_identity_included": False,
            "hostname_included": False,
            "username_included": False,
            "ssh_identity_included": False,
            "source_paths_included": False,
            "firmware_bytes_included": False,
            "raw_command_logs_included": False,
        },
        "runtime": _runtime_record(
            runtime, openocd_available, openocd_executable, openocd_version
        ),
        "diagnostics": _diagnostic_record(report),
        "application_health": _health_record(health),
        "application_health_error": health_error,
    }
    return snapshot


def _readme_text() -> str:
    return """B300 ST-Link Tools diagnostic support bundle

This ZIP is generated by a read-only workflow for support/triage.
It intentionally excludes probe serial/USB identity, username/hostname, SSH identities,
source/AXF paths, firmware bytes, environment variables, and raw command logs.

support.json contains normalized runtime, target, protection, metadata and Application Health evidence.
No file in this bundle is executable.
"""


def write_support_bundle(path: Path, snapshot: dict, *, force: bool = False) -> SupportBundleResult:
    destination = Path(path).expanduser().resolve()
    if destination.suffix.lower() != ".zip":
        raise ValueError("Support bundle output must use .zip.")
    if destination.exists() and not force:
        raise FileExistsError("Support bundle output already exists; use --force to replace it.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if len(payload) > SUPPORT_BUNDLE_MAX_BYTES:
        raise ValueError("Support bundle JSON exceeds the bounded size limit.")

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent), delete=False
        ) as stream:
            temporary_path = Path(stream.name)
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("support.json", payload)
            archive.writestr("README.txt", _readme_text().encode("utf-8"))
        if temporary_path.stat().st_size > SUPPORT_BUNDLE_MAX_BYTES:
            raise ValueError("Support bundle ZIP exceeds the bounded size limit.")
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass

    bundle_bytes = destination.read_bytes()
    return SupportBundleResult(
        path=destination,
        sha256=hashlib.sha256(bundle_bytes).hexdigest().upper(),
        size_bytes=len(bundle_bytes),
        snapshot=snapshot,
    )
