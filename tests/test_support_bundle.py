from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.application_vector import ApplicationVector
from b300_core.gdb_runtime import GdbRuntimeInfo
from b300_core.models import (
    ApplicationHealth, DiagnosticCheck, DiagnosticReport, OtaMetadata, ProbeInfo, TargetInfo,
)
from b300_core.support_bundle import collect_support_snapshot, write_support_bundle


class FakeService:
    def __init__(self, health):
        self.health = health
        self.health_calls = []

    def doctor(self):
        return True, r"C:\Users\Admin\private\openocd.exe"

    def inspect_application_health(self, probe):
        self.health_calls.append(probe)
        return self.health


def metadata() -> OtaMetadata:
    return OtaMetadata(
        classification="VALID", valid=True, magic=0x53544C4D, format_version=1,
        state=3, state_name="CONFIRMED", image_size=126580, image_crc32=0xC99ED31F,
        board_token="B300_F407ZE", sequence=4, meta_crc32=0x035E56E9,
        calculated_meta_crc32=0x035E56E9,
    )


def health() -> ApplicationHealth:
    vector = ApplicationVector(0x200185C8, 0x08010361, True, "Application vector is valid.")
    return ApplicationHealth(
        metadata=metadata(), application_vector=vector, image_crc_valid=True,
        actual_image_crc32=0xC99ED31F, bootable=True, lifecycle="BOOTABLE",
        reason="Application Metadata, image CRC, and vector permit bootability.",
        next_action="No action is required.", bytes_checked=126580,
    )


def report() -> DiagnosticReport:
    probe = ProbeInfo(
        serial="STLINK-SECRET-123", name="STM32 STLink", source="windows-pnp",
        usb_identity=r"USB\VID_0483&PID_3748\SECRET", status="available",
    )
    target = TargetInfo(
        device_id=0x413, flash_kib=512, target_voltage=3.08,
        protection_summary="Sector 0-2 protected; Sector 3-7 not protected",
        protected_sectors=(0, 1, 2), protection_reported=True, readout_protected=False,
    )
    vector = ApplicationVector(0x200185C8, 0x08010361, True, "Application vector is valid.")
    check = DiagnosticCheck(
        "target", "PASS", "TARGET_IDENTIFIED",
        r"Target log C:\Users\Admin\secret\trace.txt probe STLINK-SECRET-123 USB\VID_0483&PID_3748\SECRET",
        r"Review C:\Users\Admin\secret\next.txt if needed.",
    )
    serial_check = DiagnosticCheck(
        "probe", "PASS", "PROBE_SELECTED",
        "Selected probe STLINK-SECRET-123 is ready.",
        "No action is required.",
    )
    return DiagnosticReport(
        checks=(check, serial_check), conclusion="READY_FOR_APPLICATION_FLASH",
        reason_code="READY_FOR_APPLICATION_FLASH", next_action="No action is required.",
        target=target, application_vector=vector, metadata=metadata(), probe=probe,
    )


class SupportBundleTests(unittest.TestCase):
    def test_snapshot_excludes_identifiers_paths_credentials_and_raw_bytes(self) -> None:
        selected_report = report()
        selected_health = health()
        service = FakeService(selected_health)

        class FakeDiagnostics:
            def __init__(self, **_kwargs):
                pass
            def run(self, probe_serial=None):
                self.probe_serial = probe_serial
                return selected_report

        runtime = GdbRuntimeInfo(
            path=r"C:\Users\Admin\private\arm-none-eabi-gdb.exe",
            version="GNU gdb 13.2", available=True, platform="windows",
        )
        with mock.patch("b300_core.support_bundle.DiagnosticsService", FakeDiagnostics):
            snapshot = collect_support_snapshot(
                version="0.9.0", openocd_version="0.12.0-b300", service=service,
                probe_discovery=lambda: (), gdb_info=lambda: runtime,
                probe_serial="STLINK-SECRET-123",
                now=lambda: datetime(2026, 8, 30, 1, 2, 3, tzinfo=timezone.utc),
            )

        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn("STLINK-SECRET-123", encoded)
        self.assertNotIn("VID_0483", encoded)
        self.assertNotIn(r"C:\Users\Admin", encoded)
        self.assertNotIn("secret\\trace", encoded)
        self.assertNotIn("private", snapshot["runtime"]["gdb_executable"])
        self.assertEqual(snapshot["runtime"]["gdb_executable"], "arm-none-eabi-gdb.exe")
        self.assertEqual(snapshot["runtime"]["openocd_executable"], "openocd.exe")
        self.assertEqual(snapshot["application_health"]["lifecycle"], "BOOTABLE")
        self.assertEqual(snapshot["application_health"]["actual_image_crc32"], "0xC99ED31F")
        self.assertFalse(snapshot["privacy"]["probe_serial_included"])
        self.assertFalse(snapshot["privacy"]["firmware_bytes_included"])
        self.assertEqual(snapshot["generated_at_utc"], "2026-08-30T01:02:03Z")
        self.assertEqual(len(service.health_calls), 1)
        self.assertEqual(service.health_calls[0].serial, "STLINK-SECRET-123")
        self.assertIn("<PATH>", snapshot["diagnostics"]["checks"][0]["message"])
        self.assertIn("<REDACTED>", snapshot["diagnostics"]["checks"][1]["message"])

    def test_health_failure_records_only_exception_class(self) -> None:
        selected_report = report()

        class BrokenService(FakeService):
            def inspect_application_health(self, probe):
                raise RuntimeError(r"C:\Users\Admin\private\raw-openocd-output.txt")

        class FakeDiagnostics:
            def __init__(self, **_kwargs): pass
            def run(self, _probe_serial=None): return selected_report

        with mock.patch("b300_core.support_bundle.DiagnosticsService", FakeDiagnostics):
            snapshot = collect_support_snapshot(
                version="0.9.0", openocd_version="test",
                service=BrokenService(None), probe_discovery=lambda: (),
                gdb_info=lambda: GdbRuntimeInfo.from_path(None, platform_name="windows", reason="none"),
            )
        self.assertIsNone(snapshot["application_health"])
        self.assertEqual(snapshot["application_health_error"], "RuntimeError")
        self.assertNotIn("raw-openocd", json.dumps(snapshot))

    def test_writer_creates_exact_bounded_zip_and_obeys_force_policy(self) -> None:
        snapshot = {
            "schema_version": 1,
            "privacy": {"probe_serial_included": False},
            "diagnostics": {"conclusion": "READY"},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "support.zip"
            result = write_support_bundle(output, snapshot)
            self.assertEqual(result.path, output.resolve())
            self.assertGreater(result.size_bytes, 0)
            self.assertEqual(len(result.sha256), 64)
            with zipfile.ZipFile(output, "r") as archive:
                self.assertEqual(sorted(archive.namelist()), ["README.txt", "support.json"])
                decoded = json.loads(archive.read("support.json").decode("utf-8"))
                readme = archive.read("README.txt").decode("utf-8")
            self.assertEqual(decoded, snapshot)
            self.assertIn("read-only", readme)
            self.assertIn("excludes probe serial", readme)
            with self.assertRaises(FileExistsError):
                write_support_bundle(output, snapshot)
            replacement = write_support_bundle(output, {"schema_version": 1, "changed": True}, force=True)
            self.assertGreater(replacement.size_bytes, 0)
            with zipfile.ZipFile(output, "r") as archive:
                self.assertTrue(json.loads(archive.read("support.json"))["changed"])

    def test_writer_rejects_non_zip_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                write_support_bundle(Path(directory) / "support.json", {"schema_version": 1})


if __name__ == "__main__":
    unittest.main()
