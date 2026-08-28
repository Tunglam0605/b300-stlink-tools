from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.models import BootVerification, FlashPhaseEvent, ProbeInfo, TargetInfo


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"
VALID_HEX = ":020000040801F1\n:080000000000022001010108CB\n:00000001FF\n"
BOOTLOADER_HEX = ":020000040800F2\n:0100000000FF\n:00000001FF\n"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class B300StlinkTests(unittest.TestCase):
    def test_flash_erases_only_metadata_and_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "application.hex"
            image.write_text(VALID_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        transactions = [record for record in records if record["event"] == "openocd"]
        self.assertEqual([item["phase"] for item in transactions], [
            "program_verify", "reset",
        ])
        program = " ".join(transactions[0]["command"])
        reset = " ".join(transactions[1]["command"])
        self.assertIn("flash erase_sector 0 3 7", program)
        self.assertIn("program", program)
        self.assertIn("verify", program)
        self.assertNotIn("mww 0x40002860", program)
        self.assertIn("reset run", reset)
        self.assertEqual(transactions[0]["condition"], "always")
        self.assertEqual(transactions[1]["condition"], "after_verified_ok")
        self.assertIn("gdb port disabled", program)
        self.assertIn("telnet port disabled", program)
        self.assertIn("tcl port disabled", program)
        self.assertNotIn("mass_erase", program + reset)
        self.assertNotIn("flash protect", program + reset)
        self.assertNotIn("53544C4B", program + reset)

    def test_flash_rejects_bootloader_hex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "bad.hex"
            image.write_text(BOOTLOADER_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 1)
        error = json.loads(output.getvalue())
        self.assertIn("protected range", error["message"])
        self.assertEqual(error["phase"], "validating")
        self.assertIn("Application HEX", error["next_action"])

    def test_debug_has_no_flash_write_command(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["debug", "--dry-run", "--json"])
        self.assertEqual(result, 0)
        rendered = output.getvalue().lower()
        self.assertNotIn("erase_sector", rendered)
        self.assertNotIn("program {", rendered)
        self.assertNotIn("mww ", rendered)

    def test_debug_binds_to_loopback_by_default(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["debug", "--dry-run", "--json"])
        self.assertEqual(result, 0)
        command = json.loads(output.getvalue())["command"]
        self.assertIn("bindto 127.0.0.1", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("tcl port disabled", command)

    def test_real_debug_uses_debug_service_lifecycle(self) -> None:
        module = tool()
        created = []

        class FakeDebugService:
            def __init__(self, executable=None):
                self.executable = executable
                self.states = [module.DebugState.READY, module.DebugState.FAILED]
                self.stopped = False
                created.append(self)

            @property
            def state(self):
                return self.states.pop(0) if self.states else module.DebugState.FAILED

            def start(self, config, **kwargs):
                self.config = config
                return module.DebugState.READY

            def stop(self):
                self.stopped = True

        output = io.StringIO()
        with mock.patch.object(module, "DebugService", FakeDebugService), \
                mock.patch.object(module.time, "sleep"), redirect_stdout(output):
            result = module.main(["debug", "--probe-serial", "DEBUG123", "--json"])

        self.assertEqual(result, 1)
        self.assertEqual(created[0].config.probe.serial, "DEBUG123")
        self.assertTrue(created[0].stopped)

    def test_debug_can_explicitly_listen_for_remote_gdb(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "--bind-address", "0.0.0.0",
                "--gdb-port", "4333",
                "--probe-serial", "TEST-PROBE", "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        command = next(record["command"] for record in records if record["event"] == "openocd")
        self.assertIn("bindto 0.0.0.0", command)
        self.assertIn("gdb port 4333", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("tcl port disabled", command)
        self.assertIn("adapter serial TEST-PROBE", command)

    def test_debug_allows_explicit_telnet_on_loopback(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "--telnet-port", "4444", "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        command = json.loads(output.getvalue())["command"]
        self.assertIn("telnet port 4444", command)

    def test_remote_debug_rejects_telnet_listener(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "--bind-address", "0.0.0.0", "--telnet-port", "4444",
                "--dry-run", "--json",
            ])
        self.assertEqual(result, 1)
        self.assertIn("Telnet", output.getvalue())

    def test_debug_rejects_command_in_bind_address(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool().main([
                "debug", "--bind-address", "127.0.0.1; flash erase_sector 0 0 7",
                "--dry-run",
            ])

    def test_debug_rejects_port_outside_tcp_range(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool().main(["debug", "--gdb-port", "70000", "--dry-run"])

    def test_debug_rejects_command_in_probe_serial(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool().main([
                "debug", "--probe-serial", "SAFE; flash erase_sector 0 0 7",
                "--dry-run",
            ])

    def test_flash_rejects_path_that_can_break_openocd_braces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "bad}name.hex"
            image.write_text(VALID_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 1)
        self.assertIn("unsafe character", output.getvalue())

    def test_cli_json_reports_structured_flash_phases(self) -> None:
        module = tool()

        class FakeService:
            def __init__(self, executable=None):
                pass

            def inspect_image(self, path):
                return module.inspect_image(path)

            def inspect_target(self, probe, event_sink=None):
                return TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True)

            def plan(self, image, probe, target):
                return module.build_flash_plan(image, probe, target)

            def flash_command(self, plan):
                return ["openocd", "program"]

            def reset_command(self, probe):
                return ["openocd", "reset"]

            def flash(self, plan, event_sink=None, phase_sink=None):
                phase_sink(FlashPhaseEvent("erasing", 20, "Erasing Sector 3 through 7"))
                phase_sink(FlashPhaseEvent("succeeded", 100, "Application is running"))
                return SimpleNamespace(
                    status="succeeded",
                    succeeded=True,
                    boot_verification=BootVerification(
                        0x08010001, 0, True, "Application is running."
                    ),
                    failure_phase=None,
                    reason="",
                    next_action="",
                )

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "application.hex"
            image.write_text(VALID_HEX, encoding="ascii")
            output = io.StringIO()
            with mock.patch.object(module, "B300Service", FakeService), \
                    mock.patch.object(
                        module, "list_probes",
                        return_value=(ProbeInfo("FLASH123", "ST-Link", "test"),),
                    ), redirect_stdout(output):
                result = module.main(["flash", str(image), "--json"])

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        phases = [record["phase"] for record in records
                  if record["event"] == "flash_phase"]
        self.assertEqual(result, 0)
        self.assertEqual(phases, ["erasing", "succeeded"])


    def test_factory_dry_run_uses_trusted_bundle_and_safe_s0_s2_sequence(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "provision-bootloader", "--dry-run", "--json",
                "--probe-serial", "FACTORY123",
            ])
        self.assertEqual(result, 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        artifact = next(item for item in records if item["event"] == "factory_artifact")
        self.assertEqual(
            artifact["sha256"],
            "657F71605E00795BEA3C5601AAF569104E74D9DEE8D5B6E602514C4D72264F05",
        )
        transactions = [item for item in records if item["event"] == "openocd"]
        self.assertEqual([item["phase"] for item in transactions], [
            "unprotect", "program_verify", "reprotect", "reset",
        ])
        rendered = " ".join(" ".join(item["command"]) for item in transactions)
        self.assertIn("flash protect 0 0 2 off", rendered)
        self.assertIn("flash erase_sector 0 0 2", rendered)
        self.assertIn("flash protect 0 0 2 on", rendered)
        self.assertGreaterEqual(rendered.count("reset halt"), 2)
        self.assertIn("reset run", rendered)
        self.assertNotIn("mass_erase", rendered)
        self.assertNotIn("stm32f2x lock", rendered)
        self.assertNotIn("stm32f2x unlock", rendered)

    def test_factory_real_run_requires_explicit_confirmation_before_hardware(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["provision-bootloader", "--json"])
        self.assertEqual(result, 1)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        error = next(item for item in records if item["event"] == "error")
        self.assertEqual(error["phase"], "authorization")
        self.assertIn("--confirm-factory-provision", error["reason"])

if __name__ == "__main__":
    unittest.main()
