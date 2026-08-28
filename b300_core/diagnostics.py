"""Ordered, injected, read-only B300 target diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Sequence

from .application_vector import ApplicationVector, inspect_application_vector
from .gdb_runtime import GdbRuntimeInfo, gdb_runtime_info
from .metadata import OTA_META_SIZE, decode_ota_metadata
from .models import DiagnosticCheck, DiagnosticReport, ProbeInfo, ProbeRef, TargetInfo
from .offline_setup import verify_openocd_tree
from .policy import APPLICATION_ADDRESS, METADATA_ADDRESS, SUPPORTED_DEVICE_ID, SUPPORTED_FLASH_KIB
from .probe import list_probes
from .probe_selection import ProbeSelectionError, select_probe
from .service import B300Service


def _is_bundled_openocd(executable: str) -> bool:
    normalized = executable.replace("\\", "/").lower()
    return "/vendor/openocd/bin/" in normalized


class DiagnosticsService:
    """Build an operator snapshot without issuing any Flash/Option-Byte command."""

    def __init__(self, service: Optional[B300Service] = None,
                 probe_discovery: Callable[[], Sequence[ProbeInfo]] = list_probes,
                 gdb_info: Callable[[], GdbRuntimeInfo] = gdb_runtime_info,
                 openocd_tree_verifier: Callable[[Path], bool] = verify_openocd_tree) -> None:
        self._service = service or B300Service()
        self._probe_discovery = probe_discovery
        self._gdb_info = gdb_info
        self._openocd_tree_verifier = openocd_tree_verifier

    def run(self, probe_serial: Optional[str] = None) -> DiagnosticReport:
        checks = []
        blocked = []
        limited = []
        target: Optional[TargetInfo] = None
        vector: Optional[ApplicationVector] = None
        metadata = None
        selected_probe: Optional[ProbeInfo] = None

        runtime = self._gdb_info()
        if runtime.available:
            checks.append(DiagnosticCheck(
                "runtime", "PASS", "GDB_AVAILABLE",
                "GDB runtime is available.", "No action is required."
            ))
        else:
            checks.append(DiagnosticCheck(
                "runtime", "LIMITED", "GDB_UNAVAILABLE",
                runtime.reason or "GDB runtime is unavailable.",
                "Install GDB or configure B300_GDB to enable integrated debugging."
            ))

        available, executable = self._service.doctor()
        if not available:
            checks.append(DiagnosticCheck(
                "openocd", "FAIL", "OPENOCD_UNAVAILABLE",
                "OpenOCD is unavailable.",
                "Install the trusted B300 OpenOCD runtime or configure B300_OPENOCD."
            ))
            blocked.append(checks[-1])
            self._append_skipped(checks, ("probes", "target", "protection",
                                          "application_vector", "ota_metadata"), "OPENOCD_UNAVAILABLE")
            return self._report(checks, blocked, limited, target, vector, metadata, selected_probe)
        if _is_bundled_openocd(executable) and not self._openocd_tree_verifier(
                Path(executable).parent.parent):
            checks.append(DiagnosticCheck(
                "openocd", "FAIL", "OPENOCD_UNTRUSTED",
                "Bundled OpenOCD does not match its trusted runtime manifest.",
                "Reinstall the authenticated B300 OpenOCD runtime before connecting a target."
            ))
            blocked.append(checks[-1])
            self._append_skipped(checks, ("probes", "target", "protection",
                                          "application_vector", "ota_metadata"), "OPENOCD_UNTRUSTED")
            return self._report(checks, blocked, limited, target, vector, metadata, selected_probe)
        checks.append(DiagnosticCheck(
            "openocd", "PASS", "OPENOCD_READY", "OpenOCD is available and accepted.",
            "No action is required."
        ))

        try:
            selected_probe, probe = select_probe(self._probe_discovery(), probe_serial)
        except ProbeSelectionError as error:
            checks.append(DiagnosticCheck(
                "probes", "FAIL", error.code, error.message,
                "Connect exactly one ST-Link or select one with --probe-serial."
            ))
            blocked.append(checks[-1])
            self._append_skipped(checks, ("target", "protection", "application_vector",
                                          "ota_metadata"), error.code)
            return self._report(checks, blocked, limited, target, vector, metadata, selected_probe)
        checks.append(DiagnosticCheck(
            "probes", "PASS", "PROBE_SELECTED", "One ST-Link probe was selected.",
            "No action is required."
        ))

        try:
            target = self._service.inspect_target(probe)
        except (RuntimeError, ValueError) as error:
            message = str(error)
            if "libusb_error_access" in message.lower() or "access denied" in message.lower():
                code = "USB_ACCESS_DENIED"
                action = "Install/reload the ST-Link udev rule, reconnect the probe, then retry as the normal user."
            else:
                code = "TARGET_INSPECTION_FAILED"
                action = "Check ST-Link cable, board power, and target connection, then retry."
            checks.append(DiagnosticCheck("target", "FAIL", code, message, action))
            blocked.append(checks[-1])
            self._append_skipped(checks, ("protection", "application_vector", "ota_metadata"), code)
            return self._report(checks, blocked, limited, target, vector, metadata, selected_probe)

        target_check = self._validate_target(target)
        checks.append(target_check)
        if target_check.status == "FAIL":
            blocked.append(target_check)
            self._append_skipped(checks, ("protection", "application_vector", "ota_metadata"), target_check.code)
            return self._report(checks, blocked, limited, target, vector, metadata, selected_probe)

        protection = self._validate_protection(target)
        checks.append(protection)
        if protection.status == "FAIL":
            blocked.append(protection)
        elif protection.status == "LIMITED":
            limited.append(protection)

        try:
            vector = inspect_application_vector(
                self._service.read_memory(probe, APPLICATION_ADDRESS, 8)
            )
            vector_check = DiagnosticCheck(
                "application_vector", "PASS" if vector.valid else "LIMITED",
                "APPLICATION_VECTOR_VALID" if vector.valid else "APPLICATION_VECTOR_INVALID",
                vector.reason,
                "Flash a validated Application image to restore a valid vector." if not vector.valid
                else "No action is required.",
            )
            checks.append(vector_check)
        except (RuntimeError, ValueError) as error:
            vector_check = DiagnosticCheck(
                "application_vector", "LIMITED", "APPLICATION_VECTOR_READ_FAILED", str(error),
                "Resolve target read access before relying on Application diagnostics."
            )
            checks.append(vector_check)
            limited.append(vector_check)

        try:
            metadata = decode_ota_metadata(
                self._service.read_memory(probe, METADATA_ADDRESS, OTA_META_SIZE)
            )
            metadata_check = DiagnosticCheck(
                "ota_metadata", "PASS" if metadata.classification == "VALID" else "LIMITED",
                "OTA_METADATA_%s" % metadata.classification,
                "OTA metadata is %s." % metadata.classification.lower(),
                "No action is required." if metadata.classification == "VALID"
                else "Flash a validated Application image to recreate OTA metadata.",
            )
            checks.append(metadata_check)
        except (RuntimeError, ValueError) as error:
            metadata_check = DiagnosticCheck(
                "ota_metadata", "LIMITED", "OTA_METADATA_READ_FAILED", str(error),
                "Resolve target read access before relying on OTA metadata."
            )
            checks.append(metadata_check)
            limited.append(metadata_check)
        return self._report(checks, blocked, limited, target, vector, metadata, selected_probe)

    @staticmethod
    def _append_skipped(checks, names, code: str) -> None:
        for name in names:
            checks.append(DiagnosticCheck(
                name, "SKIPPED", "SKIPPED_%s" % code,
                "Check was not run because an earlier prerequisite failed.",
                "Resolve the earlier diagnostic failure, then run doctor again."
            ))

    @staticmethod
    def _validate_target(target: TargetInfo) -> DiagnosticCheck:
        if (target.device_id & 0xFFF) != SUPPORTED_DEVICE_ID:
            return DiagnosticCheck(
                "target", "FAIL", "UNSUPPORTED_DEVICE",
                "Expected STM32F407 device ID 0x413; found 0x%03X." % (target.device_id & 0xFFF),
                "Connect the intended B300 STM32F407 target."
            )
        if target.flash_kib != SUPPORTED_FLASH_KIB:
            return DiagnosticCheck(
                "target", "FAIL", "UNSUPPORTED_FLASH_SIZE",
                "Expected 512 KiB flash; found %d KiB." % target.flash_kib,
                "Connect the intended 512 KiB B300 target."
            )
        if target.target_voltage <= 0:
            return DiagnosticCheck(
                "target", "FAIL", "TARGET_VOLTAGE_INVALID",
                "OpenOCD reported an invalid target voltage.",
                "Check target power and ST-Link wiring."
            )
        return DiagnosticCheck(
            "target", "PASS", "TARGET_IDENTIFIED",
            "STM32F407 target identified at %.2f V." % target.target_voltage,
            "No action is required."
        )

    @staticmethod
    def _validate_protection(target: TargetInfo) -> DiagnosticCheck:
        if target.readout_protected:
            return DiagnosticCheck(
                "protection", "FAIL", "RDP_ENABLED",
                "Target readout protection/security is enabled.",
                "Use the approved production or OTA recovery process; diagnostics will not change RDP."
            )
        if not target.protection_reported:
            return DiagnosticCheck(
                "protection", "LIMITED", "WRP_NOT_REPORTED",
                "OpenOCD did not report write-protection state.",
                "Use a supported OpenOCD runtime and confirm Sector 0-2 WRP before Application flash."
            )
        missing = tuple(index for index in (0, 1, 2) if index not in target.protected_sectors)
        if missing:
            return DiagnosticCheck(
                "protection", "LIMITED", "BOOTLOADER_WRP_MISSING",
                "Bootloader write protection is missing for Sector %s." % ",".join(map(str, missing)),
                "Restore Sector 0-2 WRP through the authorized factory flow before Application flash."
            )
        return DiagnosticCheck(
            "protection", "PASS", "BOOTLOADER_WRP_PROTECTED",
            "Bootloader S0-S2 write protection is reported active.", "No action is required."
        )

    @staticmethod
    def _report(checks, blocked, limited, target, vector, metadata, probe) -> DiagnosticReport:
        if blocked:
            conclusion = "BLOCKED"
            primary = blocked[0]
        elif limited:
            conclusion = "LIMITED_READ_ONLY"
            primary = limited[0]
        else:
            conclusion = "READY_FOR_APPLICATION_FLASH"
            primary = DiagnosticCheck(
                "conclusion", "PASS", "READY_FOR_APPLICATION_FLASH",
                "Target is ready for Application flash.", "No action is required."
            )
        return DiagnosticReport(tuple(checks), conclusion, primary.code, primary.next_action,
                                target, vector, metadata, probe)
