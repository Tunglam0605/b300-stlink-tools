from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

from b300_core.hex_image import inspect_image
from b300_core.models import ProbeRef
from b300_core.openocd import (
    OpenOcdRunner,
    build_boot_verify_command,
    build_flash_command,
    parse_boot_verification,
)
from b300_core.policy import build_flash_plan
from tests.test_core_hex_policy import write_hex


class OpenOcdCoreTests(unittest.TestCase):
    def make_plan(self, directory: str):
        image = inspect_image(write_hex(directory, 0x08010000, b"\xAA"))
        return build_flash_plan(image, ProbeRef(serial="SAFE123"))

    def test_marker_is_after_program_verify_and_servers_are_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            command = build_flash_command(plan, "openocd")

        rendered = "\n".join(command)
        program_index = rendered.index("program {%s} verify" % plan.image.path)
        marker_index = rendered.index("mww 0x40002860 0x53544C4B")
        self.assertLess(program_index, marker_index)
        self.assertIn("adapter serial SAFE123", command)
        self.assertIn("gdb port disabled", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("tcl port disabled", command)
        self.assertNotIn("mass_erase", rendered)

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


if __name__ == "__main__":
    unittest.main()
