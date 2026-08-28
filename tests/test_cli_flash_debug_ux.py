from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.models import BootVerification, ProbeInfo, TargetInfo
from b300_core.policy import build_flash_plan, build_flash_preview
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink_flash_debug_ux", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeFlashService:
    created = []
    target = TargetInfo(
        0x101F6413, 512, 3.09,
        "Sector 0-2 protected; Sector 3-7 not protected",
        (0, 1, 2), True, False,
    )
    verification = BootVerification(0x08010101, 0, True, "Application is running.")

    def __init__(self, executable=None) -> None:
        self.calls = []
        type(self).created.append(self)

    def inspect_image(self, path):
        from b300_core.hex_image import inspect_image
        return inspect_image(path)

    def preview_plan(self, image, selected_probe):
        return build_flash_preview(image, selected_probe)

    def inspect_target(self, selected_probe, event_sink=None):
        self.calls.append(("inspect", selected_probe))
        return self.target

    def plan(self, image, selected_probe, target):
        self.calls.append(("plan", selected_probe))
        return build_flash_plan(image, selected_probe, target)

    def flash_command(self, plan):
        return ["openocd", "flash erase_sector 0 3 7", "program", "verify"]

    def reset_command(self, selected_probe):
        return ["openocd", "reset run"]

    def flash(self, plan, event_sink=None, phase_sink=None):
        self.calls.append(("flash", plan.probe))
        verification = self.verification
        return SimpleNamespace(
            status="succeeded" if verification.passed else "programmed_boot_failed",
            succeeded=verification.passed,
            boot_verification=verification,
            failure_phase=None if verification.passed else "post_verifying",
            reason="" if verification.passed else verification.reason,
            next_action="" if verification.passed else "Inspect boot state.",
        )


class FlashCliUxTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeFlashService.created = []
        FakeFlashService.verification = BootVerification(
            0x08010101, 0, True, "Application is running."
        )

    def make_image(self, directory: str) -> Path:
        return write_hex(directory, 0x08010000, APPLICATION_VECTOR + b"\xAA\x55")

    def test_flash_dry_run_is_hardware_free_and_reports_uninspected_preflight(self) -> None:
        module = tool()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(module, "B300Service", FakeFlashService), \
                mock.patch.object(
                    module, "list_probes", side_effect=AssertionError("must stay hardware-free")
                ) as discovery, redirect_stdout(output):
            result = module.main(["flash", str(self.make_image(directory)), "--dry-run", "--json"])

        self.assertEqual(result, 0)
        discovery.assert_not_called()
        service = FakeFlashService.created[-1]
        self.assertFalse(any(call[0] == "inspect" for call in service.calls))
        start = next(json.loads(line) for line in output.getvalue().splitlines()
                     if json.loads(line)["event"] == "flash_start")
        self.assertIsNone(start["target"])
        self.assertIsNone(start["selected_probe"])
        self.assertFalse(start["hardware_inspected"])

    def test_real_flash_preflight_and_result_report_core_plan_details(self) -> None:
        module = tool()
        output = io.StringIO()
        discovered = ProbeInfo("FLASH123", "ST-Link", "test", usb_identity="usb-1")
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(module, "B300Service", FakeFlashService), \
                mock.patch.object(module, "list_probes", return_value=(discovered,)), \
                redirect_stdout(output):
            image_path = self.make_image(directory)
            result = module.main(["flash", str(image_path), "--json"])

        self.assertEqual(result, 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        start = next(record for record in records if record["event"] == "flash_start")
        final = next(record for record in records if record["event"] == "flash_result")
        service = FakeFlashService.created[-1]
        self.assertEqual(service.calls[0][0], "inspect")
        self.assertEqual(service.calls[0][1].serial, "FLASH123")
        self.assertEqual(start["application"], str(image_path.resolve()))
        self.assertEqual(len(start["sha256"]), 64)
        self.assertEqual(start["start"], "0x08010000")
        self.assertEqual(start["end"], "0x08010009")
        self.assertEqual(start["size"], 10)
        self.assertEqual(start["initial_msp"], "0x20020000")
        self.assertEqual(start["reset_vector"], "0x08010101")
        self.assertEqual(start["selected_probe"], {"serial": "FLASH123"})
        self.assertTrue(start["hardware_inspected"])
        self.assertEqual(start["target"]["device_id"], "0x101F6413")
        self.assertEqual(start["target"]["flash_kib"], 512)
        self.assertEqual(start["erase_sectors"], [3, 4, 5, 6, 7])
        self.assertEqual(start["sector_plan"], [
            "S0-S2 untouched",
            "Sector 3 metadata erase",
            "Sector 4-7 Application",
        ])
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["pc"], "0x08010101")
        self.assertEqual(final["bkp1r"], 0)
        self.assertEqual(final["wrp_summary"], FakeFlashService.target.protection_summary)
        self.assertTrue(final["application_running"])

    def test_application_running_is_false_when_boot_verification_fails(self) -> None:
        FakeFlashService.verification = BootVerification(
            0x08002138, 0, False, "PC remains in Bootloader."
        )
        module = tool()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(module, "B300Service", FakeFlashService), \
                mock.patch.object(
                    module, "list_probes",
                    return_value=(ProbeInfo(None, "ST-Link", "test", usb_identity="usb-1"),),
                ), redirect_stdout(output):
            result = module.main(["flash", str(self.make_image(directory)), "--json"])

        self.assertEqual(result, 1)
        final = next(json.loads(line) for line in output.getvalue().splitlines()
                     if json.loads(line)["event"] == "flash_result")
        self.assertFalse(final["application_running"])
        self.assertIsNone(FakeFlashService.created[-1].calls[0][1].serial)

    def test_probe_selection_failures_preserve_stable_reason_codes(self) -> None:
        cases = (
            ((), (), "NO_PROBE"),
            ((ProbeInfo("ONE", "ST-Link", "test"), ProbeInfo("TWO", "ST-Link", "test")),
             (), "MULTIPLE_PROBES"),
            ((ProbeInfo("ONE", "ST-Link", "test"),),
             ("--probe-serial", "MISSING"), "PROBE_NOT_FOUND"),
        )
        for probes, arguments, expected_code in cases:
            with self.subTest(reason_code=expected_code):
                FakeFlashService.created = []
                module = tool()
                output = io.StringIO()
                with tempfile.TemporaryDirectory() as directory, \
                        mock.patch.object(module, "B300Service", FakeFlashService), \
                        mock.patch.object(module, "list_probes", return_value=probes), \
                        redirect_stdout(output):
                    result = module.main([
                        "flash", str(self.make_image(directory)), "--json", *arguments,
                    ])
                self.assertEqual(result, 1)
                error = next(json.loads(line) for line in output.getvalue().splitlines()
                             if json.loads(line)["event"] == "error")
                self.assertEqual(error["reason_code"], expected_code)
                self.assertFalse(any(call[0] == "inspect"
                                     for call in FakeFlashService.created[-1].calls))


class DebugCliCompatibilityTests(unittest.TestCase):
    def debug_records(self, *arguments):
        module = tool()
        output = io.StringIO()
        with mock.patch.object(module, "resolve_openocd", return_value="openocd"), \
                redirect_stdout(output):
            result = module.main(["debug", *arguments, "--dry-run", "--json"])
        return result, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_debug_and_debug_server_build_the_same_read_only_command(self) -> None:
        direct_result, direct = self.debug_records()
        server_result, server = self.debug_records("server")

        self.assertEqual((direct_result, server_result), (0, 0))
        direct_command = next(record["command"] for record in direct if record["event"] == "openocd")
        server_command = next(record["command"] for record in server if record["event"] == "openocd")
        self.assertEqual(direct_command, server_command)
        rendered = " ".join(direct_command).lower()
        self.assertIn("bindto 127.0.0.1", direct_command)
        self.assertIn("telnet port disabled", direct_command)
        self.assertIn("tcl port disabled", direct_command)
        self.assertNotIn("erase", rendered)
        self.assertNotIn("program", rendered)
        self.assertNotIn("mww", rendered)

    def test_remote_debug_emits_stable_gdb_security_warning(self) -> None:
        result, records = self.debug_records("server", "--bind-address", "0.0.0.0")

        self.assertEqual(result, 0)
        warning = next(record for record in records if record["event"] == "warning")
        self.assertEqual(warning["reason_code"], "REMOTE_GDB_INSECURE")
        self.assertIn("unauthenticated", warning["message"].lower())
        self.assertIn("unencrypted", warning["message"].lower())
        self.assertIn("SSH tunnel", warning["next_action"])


if __name__ == "__main__":
    unittest.main()
