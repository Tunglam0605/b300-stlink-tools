from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from b300_core.application_vector import ApplicationVector
from b300_core.metadata import decode_ota_metadata
from b300_core.models import DiagnosticCheck, DiagnosticReport, ProbeInfo, TargetInfo


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ready_report(probe: ProbeInfo) -> DiagnosticReport:
    target = TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True)
    vector = ApplicationVector(0x20020000, 0x08010101, True, "Application vector is valid.")
    metadata = decode_ota_metadata(b"\xff" * 44)
    checks = tuple(DiagnosticCheck(name, "PASS", "OK", "ok", "none") for name in (
        "runtime", "openocd", "probes", "target", "protection",
        "application_vector", "ota_metadata",
    ))
    return DiagnosticReport(checks, "READY_FOR_APPLICATION_FLASH", "READY_FOR_APPLICATION_FLASH",
                            "No action is required.", target, vector, metadata, probe)


def repairable_application_report(probe: ProbeInfo) -> DiagnosticReport:
    target = TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True)
    vector = ApplicationVector(0, 0x08010101, False, "Initial MSP is outside STM32F407 SRAM.")
    metadata = decode_ota_metadata(b"\xff" * 44)
    checks = (
        DiagnosticCheck("runtime", "PASS", "GDB_AVAILABLE", "ok", "none"),
        DiagnosticCheck("openocd", "PASS", "OPENOCD_READY", "ok", "none"),
        DiagnosticCheck("probes", "PASS", "PROBE_SELECTED", "ok", "none"),
        DiagnosticCheck("target", "PASS", "TARGET_IDENTIFIED", "ok", "none"),
        DiagnosticCheck("protection", "PASS", "BOOTLOADER_WRP_PROTECTED", "ok", "none"),
        DiagnosticCheck("application_vector", "LIMITED", "APPLICATION_VECTOR_INVALID",
                        vector.reason, "Flash a validated Application image."),
        DiagnosticCheck("ota_metadata", "LIMITED", "OTA_METADATA_ERASED",
                        "OTA metadata is erased.", "Flash a validated Application image."),
    )
    return DiagnosticReport(checks, "READY_FOR_APPLICATION_FLASH", "READY_FOR_APPLICATION_FLASH",
                            "No action is required.", target, vector, metadata, probe)


def run_snapshot(argv, probes):
    module = tool()
    output = io.StringIO()
    selected = []

    class FakeDiagnostics:
        def __init__(self, service=None, probe_discovery=None):
            self.probe_discovery = probe_discovery

        def run(self, probe_serial=None):
            selected.append(probe_serial)
            matching = (probes[0] if probe_serial is None
                        else next(item for item in probes if item.serial == probe_serial))
            return ready_report(matching)

    class FakeB300Service:
        def __init__(self, executable=None):
            self.executable = executable

    with mock.patch.object(module, "list_probes", create=True, return_value=probes), \
            mock.patch.object(module, "DiagnosticsService", FakeDiagnostics, create=True), \
            mock.patch.object(module, "B300Service", FakeB300Service), \
            redirect_stdout(output):
        code = module.main(argv)
    return code, json.loads(output.getvalue()), selected


class CliDoctorTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = ProbeInfo("SAFE123", "ST-Link", "test", "usb:1")
        self.two = (self.probe, ProbeInfo("OTHER456", "ST-Link", "test", "usb:2"))

    def test_target_inspect_auto_selects_exactly_one_probe(self) -> None:
        code, value, selected = run_snapshot(["target", "inspect", "--json"], (self.probe,))
        self.assertEqual(code, 0)
        self.assertEqual(selected, ["SAFE123"])
        self.assertEqual(value["target"]["device_id"], "0x00000413")
        self.assertEqual(value["target"]["mcu_family"], "STM32F407")
        self.assertEqual(value["target"]["flash_kib"], 512)
        self.assertEqual(value["target"]["voltage"], 3.09)
        self.assertFalse(value["target"]["rdp_enabled"])
        self.assertTrue(value["target"]["wrp_reported"])
        self.assertEqual(value["target"]["protected_sectors"], [0, 1, 2])
        self.assertTrue(value["application_vector"]["valid"])
        self.assertEqual(value["classification"], "READY_FOR_APPLICATION_FLASH")

    def test_target_inspect_blocks_ambiguous_probes(self) -> None:
        module = tool()
        output = io.StringIO()
        with mock.patch.object(module, "list_probes", create=True, return_value=self.two), \
                redirect_stdout(output):
            code = module.main(["target", "inspect", "--json"])
        value = json.loads(output.getvalue())
        self.assertNotEqual(code, 0)
        self.assertEqual(value["reason_code"], "MULTIPLE_PROBES")

    def test_target_without_subcommand_returns_stable_json_error(self) -> None:
        module = tool()
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.main(["target", "--json"])
        value = json.loads(output.getvalue())
        self.assertNotEqual(code, 0)
        self.assertEqual(value["reason_code"], "TARGET_SUBCOMMAND_REQUIRED")
        self.assertNotIn("Traceback", output.getvalue())

    def test_target_without_subcommand_returns_stable_text_error(self) -> None:
        module = tool()
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.main(["target"])
        self.assertNotEqual(code, 0)
        self.assertIn("TARGET_SUBCOMMAND_REQUIRED", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_repairable_vector_and_metadata_keep_target_inspect_successful(self) -> None:
        module = tool()
        output = io.StringIO()
        probe = self.probe

        class FakeDiagnostics:
            def __init__(self, service=None, probe_discovery=None):
                pass

            def run(self, probe_serial=None):
                if probe_serial != "SAFE123":
                    raise AssertionError("target inspect must pin the selected probe")
                return repairable_application_report(probe)

        class FakeB300Service:
            def __init__(self, executable=None):
                pass

        with mock.patch.object(module, "list_probes", return_value=(self.probe,)), \
                mock.patch.object(module, "DiagnosticsService", FakeDiagnostics), \
                mock.patch.object(module, "B300Service", FakeB300Service), \
                redirect_stdout(output):
            code = module.main(["target", "inspect", "--json"])
        value = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["conclusion"], "READY_FOR_APPLICATION_FLASH")
        self.assertFalse(value["application_vector"]["valid"])
        self.assertEqual(value["ota_metadata"]["classification"], "ERASED")

    def test_doctor_snapshot_contains_all_ordered_checks_and_conclusion(self) -> None:
        code, value, selected = run_snapshot(["doctor", "--json"], (self.probe,))
        self.assertEqual(code, 0)
        self.assertEqual(selected, [None])
        self.assertEqual([item["name"] for item in value["checks"]], [
            "runtime", "openocd", "probes", "target", "protection",
            "application_vector", "ota_metadata",
        ])
        self.assertEqual(value["conclusion"], "READY_FOR_APPLICATION_FLASH")


if __name__ == "__main__":
    unittest.main()
