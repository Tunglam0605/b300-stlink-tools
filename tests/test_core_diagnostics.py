from __future__ import annotations

import struct
import unittest
from unittest import mock
import zlib

from b300_core.application_vector import inspect_application_vector
from b300_core.diagnostics import DiagnosticsService
from b300_core.gdb_runtime import GdbRuntimeInfo
from b300_core.hardware_session import HardwareMode, HardwareSessionManager
from b300_core.models import ProbeInfo, ProbeRef, TargetInfo
from b300_core.policy import APPLICATION_ADDRESS, METADATA_ADDRESS
from b300_core.service import B300Service


class ApplicationVectorTests(unittest.TestCase):
    def test_valid_application_vector_requires_sram_msp_and_thumb_reset_in_app(self) -> None:
        data = struct.pack("<II", 0x20020000, 0x08010101)
        self.assertTrue(inspect_application_vector(data).valid)

    def test_reset_vector_outside_application_is_invalid(self) -> None:
        data = struct.pack("<II", 0x20020000, 0x08000101)
        self.assertFalse(inspect_application_vector(data).valid)


class ReadMemoryServiceTests(unittest.TestCase):
    def test_read_memory_uses_one_reading_session_and_the_bounded_reader(self) -> None:
        class CountingManager(HardwareSessionManager):
            def __init__(self) -> None:
                super().__init__()
                self.modes = []

            def acquire(self, mode, probe):
                self.modes.append((mode, probe))
                return super().acquire(mode, probe)

        manager = CountingManager()
        probe = ProbeRef("SAFE123")
        with mock.patch("b300_core.service.read_memory", return_value=b"\x01\x02") as reader:
            data = B300Service(
                executable="openocd", session_manager=manager
            ).read_memory(probe, 0x08010000, 2)
        self.assertEqual(data, b"\x01\x02")
        self.assertEqual(manager.modes, [(HardwareMode.READING, probe)])
        reader.assert_called_once()


def make_metadata() -> bytes:
    head = struct.pack(
        "<IIIII16sI", 0x4F54414D, 1, 3, 42, 0x12345678,
        b"B300_F407ZE\0".ljust(16, b"\0"), 1,
    )
    return head + struct.pack("<I", zlib.crc32(head) & 0xFFFFFFFF)


class FakeDiagnosticService:
    def __init__(self, *, available: bool = True, executable: str = "openocd",
                 target=None, vector: bytes | None = None,
                 metadata: bytes | None = None) -> None:
        self.available = available
        self.executable = executable
        self.target = target or TargetInfo(0x101F6413, 512, 3.1, "S0-S2 protected",
                                            (0, 1, 2), True, False)
        self.vector = vector or struct.pack("<II", 0x20020000, 0x08010101)
        self.metadata = metadata or make_metadata()
        self.calls = []

    def doctor(self):
        return self.available, self.executable

    def inspect_target(self, probe):
        self.calls.append(("inspect_target", probe))
        if isinstance(self.target, BaseException):
            raise self.target
        return self.target

    def read_memory(self, probe, address, length):
        self.calls.append(("read_memory", probe, address, length))
        if address == APPLICATION_ADDRESS:
            return self.vector
        if address == METADATA_ADDRESS:
            return self.metadata
        raise AssertionError("unexpected diagnostic read")


class DiagnosticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = ProbeInfo("SAFE123", "ST-Link", "test", "usb:1")
        self.runtime = GdbRuntimeInfo.from_path("/opt/gdb", platform_name="linux")

    def run_diagnostics(self, service=None, probes=None, trust_checker=lambda _path: True):
        return DiagnosticsService(
            service=service or FakeDiagnosticService(),
            probe_discovery=lambda: (self.probe,) if probes is None else probes,
            gdb_info=lambda: self.runtime,
            openocd_tree_verifier=trust_checker,
        ).run()

    def test_ready_report_runs_ordered_read_only_checks(self) -> None:
        service = FakeDiagnosticService()
        report = self.run_diagnostics(service)
        self.assertEqual([check.name for check in report.checks], [
            "runtime", "openocd", "probes", "target", "protection",
            "application_vector", "ota_metadata",
        ])
        self.assertEqual(report.conclusion, "READY_FOR_APPLICATION_FLASH")
        self.assertTrue(report.application_vector.valid)
        self.assertEqual(report.metadata.classification, "VALID")
        self.assertEqual([call[0] for call in service.calls], [
            "inspect_target", "read_memory", "read_memory",
        ])

    def test_missing_openocd_blocks_without_hardware_access(self) -> None:
        service = FakeDiagnosticService(available=False)
        report = self.run_diagnostics(service)
        self.assertEqual(report.conclusion, "BLOCKED")
        self.assertEqual(report.reason_code, "OPENOCD_UNAVAILABLE")
        self.assertEqual(service.calls, [])

    def test_untrusted_bundled_openocd_blocks_without_hardware_access(self) -> None:
        service = FakeDiagnosticService(executable="C:/app/vendor/openocd/bin/openocd.exe")
        report = self.run_diagnostics(service, trust_checker=lambda _path: False)
        self.assertEqual(report.reason_code, "OPENOCD_UNTRUSTED")
        self.assertEqual(service.calls, [])

    def test_missing_or_ambiguous_probe_has_stable_reason_and_no_target_access(self) -> None:
        for probes, code in (((), "NO_PROBE"), (
            (self.probe, ProbeInfo("OTHER", "ST-Link", "test", "usb:2")),
            "MULTIPLE_PROBES",
        )):
            with self.subTest(code=code):
                service = FakeDiagnosticService()
                report = self.run_diagnostics(service, probes=probes)
                self.assertEqual(report.reason_code, code)
                self.assertEqual(service.calls, [])

    def test_libusb_access_error_blocks_with_concrete_recovery(self) -> None:
        service = FakeDiagnosticService(target=RuntimeError("LIBUSB_ERROR_ACCESS"))
        report = self.run_diagnostics(service)
        self.assertEqual(report.reason_code, "USB_ACCESS_DENIED")
        self.assertIn("udev", report.next_action.lower())

    def test_target_identity_rdp_and_write_protection_failures_are_classified(self) -> None:
        cases = (
            (TargetInfo(0x419, 512, 3.1, "", (0, 1, 2), True), "UNSUPPORTED_DEVICE"),
            (TargetInfo(0x413, 1024, 3.1, "", (0, 1, 2), True), "UNSUPPORTED_FLASH_SIZE"),
            (TargetInfo(0x413, 512, 3.1, "", (0, 1, 2), True, True), "RDP_ENABLED"),
            (TargetInfo(0x413, 512, 3.1, "", (), False), "WRP_NOT_REPORTED"),
            (TargetInfo(0x413, 512, 3.1, "", (0, 1), True), "BOOTLOADER_WRP_MISSING"),
        )
        for target, code in cases:
            with self.subTest(code=code):
                report = self.run_diagnostics(FakeDiagnosticService(target=target))
                self.assertEqual(report.reason_code, code)
                self.assertIn(report.conclusion, ("BLOCKED", "LIMITED_READ_ONLY"))

    def test_invalid_vector_and_each_metadata_classification_are_reported(self) -> None:
        invalid_vector = struct.pack("<II", 0x00000000, 0x08010101)
        vector_report = self.run_diagnostics(FakeDiagnosticService(vector=invalid_vector))
        self.assertFalse(vector_report.application_vector.valid)
        self.assertEqual(vector_report.reason_code, "APPLICATION_VECTOR_INVALID")

        for metadata, classification in ((b"\xFF" * 44, "ERASED"),
                                         (make_metadata(), "VALID"),
                                         (b"\0" * 44, "CORRUPT")):
            with self.subTest(classification=classification):
                report = self.run_diagnostics(FakeDiagnosticService(metadata=metadata))
                self.assertEqual(report.metadata.classification, classification)


if __name__ == "__main__":
    unittest.main()
