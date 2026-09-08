"""Privacy-bounded read-only diagnostic support bundle generation."""

from __future__ import annotations

import hashlib
import ipaddress
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
from typing import Callable, Mapping, Optional, Sequence

from .diagnostics import DiagnosticsService
from .gdb_runtime import GdbRuntimeInfo, gdb_runtime_info
from .models import ApplicationHealth, DiagnosticReport, ProbeInfo, ProbeRef
from .probe import list_probes
from .service import B300Service


SUPPORT_BUNDLE_SCHEMA_VERSION = 1
SUPPORT_BUNDLE_MAX_BYTES = 2 * 1024 * 1024
SUPPORT_TIMELINE_MAX_ENTRIES = 32
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/][^\r\n\t\"']+")
_UNIX_HOME_PATH = re.compile(r"/(?:home|Users)/[^/\s]+(?:/[^\s\"']*)?")
_SAFE_EVIDENCE_TOKEN = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")
_SAFE_VERSION = re.compile(r"^v?\d+(?:[._+-][A-Za-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SESSION_STATES = frozenset({
    "connecting", "connected", "disconnected", "error", "stopped", "starting", "ready",
    "waiting_probe", "waiting_selection", "failed",
})
_TUNNEL_NAMES = frozenset({"gdb", "tcl", "vscode-gdb"})


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


def _evidence_token(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if _SAFE_EVIDENCE_TOKEN.fullmatch(text) else None


def _loopback_endpoint(value: object) -> Optional[str]:
    text = str(value).strip()
    host, separator, port_text = text.rpartition(":")
    if not separator:
        return None
    try:
        address = ipaddress.ip_address(host)
        port = int(port_text)
    except ValueError:
        return None
    if not address.is_loopback or not 1 <= port <= 65535:
        return None
    return "%s:%d" % (address, port)


def _operational_evidence_record(evidence: Optional[Mapping[str, object]]) -> Optional[dict]:
    """Normalize explicitly supplied operational evidence without retaining identities or logs."""
    if not isinstance(evidence, Mapping):
        return None
    record = {}
    versions = evidence.get("versions")
    if isinstance(versions, Mapping):
        selected = {
            name: token for name in ("gui", "core", "cli")
            if (token := _evidence_token(versions.get(name))) is not None
            and _SAFE_VERSION.fullmatch(token)
        }
        if selected:
            record["versions"] = selected
    gateway = evidence.get("gateway")
    if isinstance(gateway, Mapping):
        selected = {}
        try:
            protocol_version = int(gateway.get("protocol_version"))
        except (TypeError, ValueError):
            protocol_version = -1
        if 0 <= protocol_version <= 9999:
            selected["protocol_version"] = protocol_version
        if (state := _evidence_token(gateway.get("session_state"))) in _SESSION_STATES:
            selected["session_state"] = state
        for name in ("generation", "sequence"):
            try:
                value = int(gateway.get(name))
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 2 ** 31 - 1:
                selected[name] = value
        if (reason_code := _evidence_token(gateway.get("reason_code"))) is not None and reason_code == reason_code.upper():
            selected["reason_code"] = reason_code
        if selected:
            record["gateway"] = selected
    gateway_agent = evidence.get("gateway_agent")
    if isinstance(gateway_agent, Mapping):
        selected = {}
        for name in ("state", "reason_code"):
            token = _evidence_token(gateway_agent.get(name))
            if token is not None and token == token.upper():
                selected[name] = token
        instance_id = _evidence_token(gateway_agent.get("instance_id"))
        if instance_id is not None:
            selected["instance_id"] = instance_id
        try:
            pid = int(gateway_agent.get("pid"))
        except (TypeError, ValueError):
            pid = -1
        if 0 < pid <= 2 ** 31 - 1:
            selected["pid"] = pid
        if selected:
            record["gateway_agent"] = selected
    gateway_lease = evidence.get("gateway_lease")
    if isinstance(gateway_lease, Mapping):
        selected = {}
        if type(gateway_lease.get("active")) is bool:
            selected["active"] = gateway_lease["active"]
        for name in ("client_label", "mode", "state", "reason_code", "gateway_instance_id"):
            token = _evidence_token(gateway_lease.get(name))
            if token is None:
                continue
            if name in {"mode", "state", "reason_code"} and token != token.upper():
                continue
            if name == "mode" and token not in {"LIVE_WATCH", "VSCODE_DEBUG"}:
                continue
            selected[name] = token
        acquired_at = str(gateway_lease.get("acquired_at") or "").strip()
        if acquired_at.endswith("Z"):
            try:
                datetime.fromisoformat(acquired_at.replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                selected["acquired_at"] = acquired_at
        for name in ("generation", "gateway_generation", "heartbeat_age_seconds"):
            try:
                value = int(gateway_lease.get(name))
            except (TypeError, ValueError):
                continue
            upper = 86400 if name == "heartbeat_age_seconds" else 2 ** 31 - 1
            if 0 <= value <= upper:
                selected[name] = value
        # Deliberately omit lease_id, token and probe_serial even when supplied.
        # Keep a stable public shape when the supplied lease record contains
        # no safe fields; callers can distinguish "present but redacted" from
        # an omitted diagnostic source without exposing private material.
        record["gateway_lease"] = selected
    process = evidence.get("process")
    if isinstance(process, Mapping):
        selected = {}
        if (owner := _evidence_token(process.get("owner"))) is not None and owner.casefold().startswith("b300"):
            selected["owner"] = owner
        for name in ("pid", "parent_pid"):
            try:
                identifier = int(process.get(name))
            except (TypeError, ValueError):
                continue
            if 0 <= identifier <= 2 ** 31 - 1:
                selected[name] = identifier
        if selected:
            record["process"] = selected
    tunnels = evidence.get("tunnels")
    if isinstance(tunnels, Sequence) and not isinstance(tunnels, (str, bytes)):
        selected = []
        for tunnel in tunnels[:16]:
            if not isinstance(tunnel, Mapping):
                continue
            name = _evidence_token(tunnel.get("name"))
            local = _loopback_endpoint(tunnel.get("local_endpoint"))
            gateway_endpoint = _loopback_endpoint(tunnel.get("gateway_endpoint"))
            if name in _TUNNEL_NAMES and local is not None and gateway_endpoint is not None:
                selected.append({"name": name, "local_endpoint": local,
                                 "gateway_endpoint": gateway_endpoint})
        record["tunnels"] = selected
    axf = evidence.get("axf")
    if isinstance(axf, Mapping):
        basename = _portable_basename(axf.get("basename") or axf.get("path"))
        digest = str(axf.get("sha256") or "").strip().lower()
        if basename and _SHA256.fullmatch(digest):
            record["axf"] = {"basename": basename, "sha256": digest}
    timeline = evidence.get("timeline")
    if isinstance(timeline, Sequence) and not isinstance(timeline, (str, bytes)):
        selected = []
        for item in timeline[-SUPPORT_TIMELINE_MAX_ENTRIES:]:
            if not isinstance(item, Mapping):
                continue
            at_utc = str(item.get("at_utc") or "").strip()
            if not at_utc.endswith("Z"):
                continue
            try:
                datetime.fromisoformat(at_utc.replace("Z", "+00:00"))
            except ValueError:
                continue
            event = _evidence_token(item.get("event"))
            if event is None or event != event.upper():
                continue
            entry = {"at_utc": at_utc, "event": event}
            for name in ("state", "code"):
                if (token := _evidence_token(item.get(name))) is not None and token == token.upper():
                    entry[name] = token
            selected.append((datetime.fromisoformat(at_utc.replace("Z", "+00:00")), entry))
        record["timeline"] = [entry for _timestamp, entry in sorted(selected, key=lambda item: item[0])]
    return record


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
    operational_evidence: Optional[Mapping[str, object]] = None,
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
    if (evidence := _operational_evidence_record(operational_evidence)) is not None:
        snapshot["operational_evidence"] = evidence
    return snapshot


def _readme_text() -> str:
    return """B300 ST-Link Tools diagnostic support bundle

This ZIP is generated by a read-only workflow for support/triage.
It intentionally excludes probe serial/USB identity, username/hostname, SSH identities,
source/AXF paths, firmware bytes, environment variables, and raw command logs.

support.json contains normalized runtime, target, protection, metadata and Application Health evidence.
When supplied by the caller, it can also contain bounded operational versions, loopback
tunnel state, AXF basename/fingerprint, process identifiers, and timeline event codes.
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
