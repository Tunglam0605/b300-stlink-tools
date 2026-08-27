from __future__ import annotations

import tempfile
import unittest

from b300_core.hex_image import inspect_image
from b300_core.models import CommandResult, ProbeRef
from b300_core.policy import build_flash_plan
from b300_core.service import B300Service
from tests.test_core_hex_policy import write_hex


class ScriptedRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.commands = []

    def run(self, command, event_sink=None):
        self.commands.append(tuple(command))
        result = self.outputs.pop(0)
        if event_sink:
            for line in result.output.splitlines():
                event_sink(line)
        return CommandResult(tuple(command), result.returncode, result.output)


def result(output: str, returncode: int = 0) -> CommandResult:
    return CommandResult(("openocd",), returncode, output)


class FlashServiceTests(unittest.TestCase):
    def make_plan(self, directory: str):
        image = inspect_image(write_hex(directory, 0x08010000, b"\x01"))
        return build_flash_plan(image, ProbeRef("SAFE123"))

    def test_flash_runs_once_then_verifies_boot(self) -> None:
        runner = ScriptedRunner([
            result("** Verified OK **"),
            result("pc (/32): 0x0802496e\n"
                   "0x40002854: 00000000 00000000 00000000 00000000"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(len(runner.commands), 2)
        self.assertTrue(outcome.boot_verification.passed)

    def test_verify_failure_does_not_retry_flash(self) -> None:
        runner = ScriptedRunner([
            result("** Verified OK **"),
            result("pc (/32): 0x08002138\n"
                   "0x40002854: 00000000 00000000 00000000 00000000"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "programmed_boot_failed")
        self.assertEqual(len(runner.commands), 2)

    def test_program_failure_never_writes_a_second_transaction(self) -> None:
        runner = ScriptedRunner([result("verify failed", returncode=1)])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "flash_failed")
        self.assertEqual(len(runner.commands), 1)
        self.assertIsNone(outcome.boot_verification)

    def test_exit_zero_without_verified_ok_is_failure(self) -> None:
        runner = ScriptedRunner([result("Programming Finished")])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "flash_failed")
        self.assertEqual(len(runner.commands), 1)


if __name__ == "__main__":
    unittest.main()
