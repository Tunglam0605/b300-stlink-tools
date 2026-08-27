from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import replace

from b300_core.hex_image import inspect_image
from b300_core.models import CommandResult, ProbeRef, TargetInfo
from b300_core.policy import build_flash_plan
from b300_core.service import B300Service, ProvisioningError
from tests.test_core_hex_policy import write_hex


class ScriptedRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.commands = []
        self.options = []

    def run(self, command, event_sink=None, **options):
        self.commands.append(tuple(command))
        self.options.append(options)
        result = self.outputs.pop(0)
        if event_sink:
            for line in result.output.splitlines():
                event_sink(line)
        return CommandResult(
            tuple(command),
            result.returncode,
            result.output,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
        )


def result(output: str, returncode: int = 0) -> CommandResult:
    return CommandResult(("openocd",), returncode, output)


def supported_target() -> CommandResult:
    return result(
        "Info : Target voltage: 3.09\n"
        "Info : device id = 0x101f6413\n"
        "Info : flash size = 512 KiB"
    )


class FlashServiceTests(unittest.TestCase):
    def make_plan(self, directory: str):
        image = inspect_image(write_hex(directory, 0x08010000, b"\x01"))
        return build_flash_plan(
            image,
            ProbeRef("SAFE123"),
            TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected"),
        )

    def test_flash_runs_once_then_verifies_boot(self) -> None:
        phases = []
        runner = ScriptedRunner([
            supported_target(),
            result("** Programming Started **\n** Programming Finished **\n"
                   "** Verify Started **\n** Verified OK **"),
            result("marker written"),
            result("reset complete"),
            result("pc (/32): 0x0802496e\n"
                   "0x40002854: 00000000 00000000 00000000 00000000"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory), phase_sink=phases.append
            )
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(len(runner.commands), 5)
        self.assertIn("flash info 0", runner.commands[0])
        self.assertNotIn("mww 0x40002860", " ".join(runner.commands[1]))
        self.assertIn("mww 0x40002860 0x53544C4B", " ".join(runner.commands[2]))
        self.assertIn("reset run", runner.commands[3])
        self.assertEqual([event.phase for event in phases], [
            "validating", "target_check", "erasing", "programming", "verifying",
            "marking", "resetting", "post_verifying", "succeeded",
        ])
        self.assertEqual(
            [event.cancellable for event in phases],
            [True, True, False, False, False, False, False, False, False],
        )
        self.assertTrue(outcome.boot_verification.passed)
        self.assertEqual(
            [item["timeout_seconds"] for item in runner.options],
            [20.0, 180.0, 20.0, 20.0, 20.0],
        )

    def test_verify_failure_does_not_retry_flash(self) -> None:
        runner = ScriptedRunner([
            supported_target(),
            result("** Verified OK **"),
            result("marker written"),
            result("reset complete"),
            result("pc (/32): 0x08002138\n"
                   "0x40002854: 00000000 00000000 00000000 00000000"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "programmed_boot_failed")
        self.assertEqual(len(runner.commands), 5)

    def test_program_failure_never_writes_a_second_transaction(self) -> None:
        runner = ScriptedRunner([
            supported_target(),
            result("** Verify Started **\nverify failed", returncode=1),
        ])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "flash_failed")
        self.assertEqual(len(runner.commands), 2)
        self.assertNotIn("mww 0x40002860", " ".join(runner.commands[1]))
        self.assertIsNone(outcome.boot_verification)
        self.assertEqual(outcome.failure_phase, "verifying")
        self.assertIn("verify", outcome.reason.lower())
        self.assertIn("log", outcome.next_action.lower())

    def test_exit_zero_without_verified_ok_is_failure(self) -> None:
        runner = ScriptedRunner([supported_target(), result("Programming Finished")])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "flash_failed")
        self.assertEqual(len(runner.commands), 2)
        self.assertNotIn("mww 0x40002860", " ".join(runner.commands[1]))

    def test_misleading_verified_text_does_not_write_marker(self) -> None:
        runner = ScriptedRunner([
            supported_target(), result("Error: image is not Verified OK")
        ])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "flash_failed")
        self.assertEqual(len(runner.commands), 2)
        self.assertNotIn("mww 0x40002860", " ".join(runner.commands[1]))

    def test_wrong_target_is_rejected_before_destructive_command(self) -> None:
        runner = ScriptedRunner([result(
            "Info : Target voltage: 3.09\n"
            "Info : device id = 0x10006419\n"
            "Info : flash size = 2048 KiB"
        )])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ProvisioningError, "(?i)unsupported target"
            ) as captured:
                B300Service(runner=runner, executable="openocd").flash(
                    self.make_plan(directory)
                )
        self.assertEqual(len(runner.commands), 1)
        self.assertIn("flash info 0", runner.commands[0])
        self.assertNotIn("erase_sector", " ".join(runner.commands[0]))
        self.assertEqual(captured.exception.phase, "target_check")

    def test_target_timeout_is_reported_explicitly(self) -> None:
        runner = ScriptedRunner([
            CommandResult(("openocd",), -1, "partial log", timed_out=True)
        ])
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            B300Service(runner=runner, executable="openocd").inspect_target(
                ProbeRef("SAFE123")
            )

    def test_boot_verify_timeout_runs_resume_recovery(self) -> None:
        runner = ScriptedRunner([
            CommandResult(("openocd",), -1, "halted", timed_out=True),
            result("resumed"),
        ])
        _command, verification = B300Service(
            runner=runner, executable="openocd"
        ).verify_boot(ProbeRef("SAFE123"))
        self.assertFalse(verification.passed)
        self.assertIn("timed out", verification.reason)
        self.assertEqual(len(runner.commands), 2)
        self.assertIn("resume", runner.commands[1])
        self.assertNotIn("halt", " ".join(runner.commands[1]))

    def test_changed_hex_is_rejected_before_any_hardware_command(self) -> None:
        runner = ScriptedRunner([])
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            write_hex(directory, 0x08000000, b"\x00")
            with self.assertRaisesRegex(
                ProvisioningError, "changed after approval"
            ) as captured:
                B300Service(runner=runner, executable="openocd").flash(plan)
        self.assertEqual(runner.commands, [])
        self.assertEqual(captured.exception.phase, "validating")
        self.assertIn("select", captured.exception.next_action.lower())

    def test_forged_plan_hash_is_rejected_before_any_hardware_command(self) -> None:
        runner = ScriptedRunner([])
        with tempfile.TemporaryDirectory() as directory:
            approved = self.make_plan(directory)
            forged = replace(
                approved,
                image=replace(approved.image, sha256="0" * 64),
            )
            with self.assertRaisesRegex(ValueError, "does not match approved plan"):
                B300Service(runner=runner, executable="openocd").flash(forged)
        self.assertEqual(runner.commands, [])

    def test_service_rejects_concurrent_stlink_operations(self) -> None:
        class BlockingRunner:
            def __init__(self):
                self.commands = []
                self.started = threading.Event()
                self.release = threading.Event()

            def run(self, command, event_sink=None, **options):
                self.commands.append(tuple(command))
                if len(self.commands) == 1:
                    self.started.set()
                    self.release.wait(timeout=2)
                    return result(
                        "Info : Target voltage: 3.09\n"
                        "Info : device id = 0x101f6413\n"
                        "Info : flash size = 512 KiB"
                    )
                return result("unexpected concurrent command")

        runner = BlockingRunner()
        service = B300Service(runner=runner, executable="openocd")
        first = threading.Thread(
            target=lambda: service.inspect_target(ProbeRef(None)), daemon=True
        )
        first.start()
        self.assertTrue(runner.started.wait(timeout=1))
        try:
            with self.assertRaisesRegex(RuntimeError, "already running"):
                service.read_sector(ProbeRef(None), 0)
        finally:
            runner.release.set()
            first.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.assertEqual(len(runner.commands), 1)


if __name__ == "__main__":
    unittest.main()
