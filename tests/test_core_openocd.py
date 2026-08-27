from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from b300_core.hex_image import inspect_image
from b300_core.models import ProbeRef, TargetInfo
from b300_core.openocd import (
    OpenOcdRunner,
    build_boot_verify_command,
    build_flash_command,
    build_marker_command,
    build_reset_command,
    parse_boot_verification,
    build_target_inspect_command,
    parse_target_info,
)
from b300_core.policy import build_flash_plan
from tests.test_core_hex_policy import write_hex


class OpenOcdCoreTests(unittest.TestCase):
    def make_plan(self, directory: str):
        image = inspect_image(write_hex(directory, 0x08010000, b"\xAA"))
        return build_flash_plan(
            image,
            ProbeRef(serial="SAFE123"),
            TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected"),
        )

    def test_program_transaction_cannot_write_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            command = build_flash_command(plan, "openocd")

        rendered = "\n".join(command)
        self.assertIn("program {%s} verify" % plan.image.path, rendered)
        self.assertNotIn("mww 0x40002860", rendered)
        self.assertIn("adapter serial SAFE123", command)
        self.assertIn("gdb port disabled", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("tcl port disabled", command)
        self.assertNotIn("mass_erase", rendered)

    def test_marker_and_reset_are_separate_transactions(self) -> None:
        marker = build_marker_command(ProbeRef("SAFE123"), "openocd")
        reset = build_reset_command(ProbeRef("SAFE123"), "openocd")
        rendered_marker = "\n".join(marker)
        rendered_reset = "\n".join(reset)
        self.assertIn("mww 0x40002860 0x53544C4B", rendered_marker)
        self.assertNotIn("reset run", marker)
        self.assertIn("reset run", reset)
        self.assertNotIn("mww 0x40002860", rendered_reset)
        self.assertNotIn("erase_sector", rendered_marker + rendered_reset)
        self.assertNotIn("program {", rendered_marker + rendered_reset)

    def test_boot_verify_command_resumes_before_shutdown(self) -> None:
        command = build_boot_verify_command(ProbeRef(), "openocd")
        self.assertLess(command.index("resume"), command.index("shutdown"))
        self.assertIn("sleep 1000", command)

    def test_boot_verification_requires_application_pc_and_cleared_markers(self) -> None:
        output = (
            "pc (/32): 0x0802496e\n"
            "0x40002854: 00000000 00000000 00000000 00000000\n"
        )
        result = parse_boot_verification(output)
        self.assertTrue(result.passed)
        self.assertEqual(result.pc, 0x0802496E)
        self.assertEqual(result.bkp1r, 0)
        self.assertEqual(result.bkp4r, 0)

    def test_boot_verification_rejects_bootloader_pc(self) -> None:
        output = (
            "pc (/32): 0x08002138\n"
            "0x40002854: 00000000 00000000 00000000 00000000\n"
        )
        result = parse_boot_verification(output)
        self.assertFalse(result.passed)
        self.assertIn("Bootloader", result.reason)

    def test_runner_streams_output_and_returns_combined_result(self) -> None:
        events = []
        result = OpenOcdRunner().run(
            [sys.executable, "-c", "print('Verified OK')"],
            event_sink=events.append,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Verified OK", result.output)
        self.assertEqual(events, ["Verified OK"])

    def test_runner_reports_missing_executable_without_shell(self) -> None:
        result = OpenOcdRunner().run(["definitely-missing-b300-openocd"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.output.lower())

    def test_runner_timeout_terminates_process_and_preserves_output(self) -> None:
        started = time.monotonic()
        result = OpenOcdRunner().run(
            [sys.executable, "-c",
             "import time; print('started', flush=True); time.sleep(10)"],
            timeout_seconds=0.15,
        )
        elapsed = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertFalse(result.cancelled)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("started", result.output)
        self.assertLess(elapsed, 3.0)

    def test_runner_cancel_terminates_process_without_waiting_for_timeout(self) -> None:
        cancel = threading.Event()
        cancel.set()
        started = time.monotonic()
        result = OpenOcdRunner().run(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=30,
            cancel_event=cancel,
        )
        self.assertTrue(result.cancelled)
        self.assertFalse(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_frozen_app_resolves_bundled_openocd(self) -> None:
        from b300_core.openocd import resolve_openocd

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "b300-stlink-gui.exe"
            bundled = root / "vendor" / "openocd" / "bin" / "openocd.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"")
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", str(executable)):
                self.assertEqual(resolve_openocd(), str(bundled))

    def test_target_inspection_is_read_only_and_parses_f407(self) -> None:
        command = build_target_inspect_command(ProbeRef("SAFE123"), "openocd")
        rendered = " ".join(command)
        self.assertIn("flash info 0", command)
        self.assertNotIn("halt", rendered)
        self.assertNotIn("reset", rendered)
        self.assertNotIn("mww", rendered)
        info = parse_target_info(
            "Info : Target voltage: 3.091421\n"
            "Info : device id = 0x101f6413\n"
            "Info : flash size = 512 KiB\n"
            "#  0: 0x00000000 (0x4000 16kB) protected\n"
            "#  1: 0x00004000 (0x4000 16kB) protected\n"
            "#  2: 0x00008000 (0x4000 16kB) protected\n"
            "#  3: 0x0000c000 (0x4000 16kB) not protected\n"
            "#  4: 0x00010000 (0x10000 64kB) not protected\n"
            "#  5: 0x00020000 (0x20000 128kB) not protected\n"
            "#  6: 0x00040000 (0x20000 128kB) not protected\n"
            "#  7: 0x00060000 (0x20000 128kB) not protected\n"
        )
        self.assertEqual(info.device_id, 0x101F6413)
        self.assertEqual(info.flash_kib, 512)
        self.assertAlmostEqual(info.target_voltage, 3.091421)
        self.assertEqual(
            info.protection_summary,
            "Sector 0–2 protected; Sector 3–7 not protected",
        )


if __name__ == "__main__":
    unittest.main()
