from __future__ import annotations

import tempfile
import threading
import unittest
import re
import struct
import zlib
from dataclasses import replace
from pathlib import Path

from b300_core.hex_image import inspect_image
from b300_core.metadata import build_stlink_metadata
from b300_core.models import CommandResult, ProbeRef, TargetInfo
from b300_core.policy import build_flash_plan
from b300_core.service import B300Service, ProvisioningError
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex


class ScriptedRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.commands = []
        self.options = []
        self.written_metadata = None
        self.previous_metadata = None
        self.metadata_readback_override = None
        self.confirmation_override = None

    @staticmethod
    def _confirmed(record: bytes) -> bytes:
        values = list(struct.unpack("<IIIII16sII", record))
        values[2] = 3
        values[6] = (values[6] + 1) & 0xFFFFFFFF
        head = struct.pack("<IIIII16sI", *values[:-1])
        values[7] = zlib.crc32(head) & 0xFFFFFFFF
        return struct.pack("<IIIII16sII", *values)

    def run(self, command, event_sink=None, **options):
        command = tuple(command)
        self.commands.append(command)
        self.options.append(options)
        rendered = " ".join(command)

        # Product memory reads use a bounded dump file. Model those filesystem
        # side effects without consuming the scripted OpenOCD phase results.
        if "B300_READ_FAILED" in rendered and "0x0800C000 44" in rendered:
            match = re.search(r"dump_image \{([^}]*)\} 0x0800C000 44", rendered)
            if match:
                path = Path(match.group(1))
                if self.written_metadata is None:
                    data = self.previous_metadata if self.previous_metadata is not None else b"\xFF" * 44
                elif self.confirmation_override is not None:
                    data = self.confirmation_override
                else:
                    data = self._confirmed(self.written_metadata)
                path.write_bytes(data)
            scripted = CommandResult(command, 0, "memory dumped")
        else:
            scripted = self.outputs.pop(0)
            if ("flash write_image" in rendered and "0x0800C000 bin" in rendered and
                    "dump_image" in rendered and scripted.returncode == 0):
                write_match = re.search(r"flash write_image \{([^}]*)\} 0x0800C000 bin", rendered)
                dump_match = re.search(r"dump_image \{([^}]*)\} 0x0800C000 44", rendered)
                if write_match and dump_match:
                    self.written_metadata = Path(write_match.group(1)).read_bytes()
                    data = (self.metadata_readback_override
                            if self.metadata_readback_override is not None
                            else self.written_metadata)
                    Path(dump_match.group(1)).write_bytes(data)

        if event_sink:
            for line in scripted.output.splitlines():
                event_sink(line)
        return CommandResult(
            command, scripted.returncode, scripted.output,
            timed_out=scripted.timed_out, cancelled=scripted.cancelled,
        )


def result(output: str, returncode: int = 0) -> CommandResult:
    return CommandResult(("openocd",), returncode, output)


def supported_target() -> CommandResult:
    return result(
        "Info : Target voltage: 3.09\n"
        "Info : device id = 0x101f6413\n"
        "Info : flash size = 512 KiB\n"
        "#  0: 0x00000000 (0x4000 16kB) protected\n"
        "#  1: 0x00004000 (0x4000 16kB) protected\n"
        "#  2: 0x00008000 (0x4000 16kB) protected\n"
        "#  3: 0x0000c000 (0x4000 16kB) not protected\n"
        "#  4: 0x00010000 (0x10000 64kB) not protected\n"
        "#  5: 0x00020000 (0x20000 128kB) not protected\n"
        "#  6: 0x00040000 (0x20000 128kB) not protected\n"
        "#  7: 0x00060000 (0x20000 128kB) not protected"
    )


def metadata_program_result(plan) -> CommandResult:
    del plan
    return result("metadata programmed and verified")



class FlashServiceTests(unittest.TestCase):
    def make_plan(self, directory: str):
        image = inspect_image(write_hex(directory, 0x08010000, APPLICATION_VECTOR))
        return build_flash_plan(
            image,
            ProbeRef("SAFE123"),
            TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True),
        )

    def test_flash_runs_once_then_verifies_boot(self) -> None:
        phases = []
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            runner = ScriptedRunner([
                supported_target(),
                result("** Programming Started **\n** Programming Finished **\n"
                       "** Verify Started **\n** Verified OK **"),
                metadata_program_result(plan),
                result("reset complete"),
                result("pc (/32): 0x0802496e\n"
                       "0x40002854: 00000000"),
            ])
            outcome = B300Service(runner=runner, executable="openocd").flash(
                plan, phase_sink=phases.append
            )
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(len(runner.commands), 7)
        self.assertIn("0x0800C000 44", " ".join(runner.commands[0]))
        self.assertIn("flash info 0", runner.commands[1])
        rendered = " ".join(item for command in runner.commands for item in command)
        self.assertNotIn("mww 0x40002860", rendered)
        self.assertNotIn("flash protect", rendered)
        self.assertNotIn("mass_erase", rendered)
        self.assertIn("flash erase_sector 0 3 7", rendered)
        self.assertIn("flash write_image", " ".join(runner.commands[3]))
        self.assertIn("0x0800C000", " ".join(runner.commands[3]))
        self.assertIn("reset run", runner.commands[4])
        self.assertEqual([event.phase for event in phases], [
            "validating", "metadata_reading", "target_check", "erasing", "programming", "verifying",
            "metadata_programming", "metadata_verifying", "resetting",
            "metadata_confirming", "post_verifying", "succeeded",
        ])
        self.assertEqual(
            [event.cancellable for event in phases],
            [True, True, True, False, False, False, False, False, False, False, False, False],
        )
        self.assertTrue(outcome.boot_verification.passed)
        self.assertIsNotNone(outcome.metadata_command)
        self.assertEqual(
            [item["timeout_seconds"] for item in runner.options],
            [60.0, 20.0, 180.0, 30.0, 20.0, 60.0, 20.0],
        )

    def test_verify_failure_does_not_retry_flash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            runner = ScriptedRunner([
                supported_target(),
                result("** Verified OK **"),
                metadata_program_result(plan),
                result("reset complete"),
                result("pc (/32): 0x08002138\n"
                       "0x40002854: 00000000"),
            ])
            outcome = B300Service(runner=runner, executable="openocd").flash(plan)
        self.assertEqual(outcome.status, "programmed_boot_failed")
        self.assertEqual(len(runner.commands), 7)

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
        self.assertEqual(len(runner.commands), 3)
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
        self.assertEqual(len(runner.commands), 3)
        self.assertNotIn("mww 0x40002860", " ".join(runner.commands[1]))

    def test_misleading_verified_text_does_not_reset(self) -> None:
        runner = ScriptedRunner([
            supported_target(), result("Error: image is not Verified OK")
        ])
        with tempfile.TemporaryDirectory() as directory:
            outcome = B300Service(runner=runner, executable="openocd").flash(
                self.make_plan(directory)
            )
        self.assertEqual(outcome.status, "flash_failed")
        self.assertEqual(len(runner.commands), 3)
        self.assertNotIn("mww 0x40002860", " ".join(runner.commands[1]))

    def test_metadata_program_failure_never_resets_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            runner = ScriptedRunner([
                supported_target(),
                result("** Verified OK **"),
                result("metadata write failed", returncode=1),
            ])
            outcome = B300Service(runner=runner, executable="openocd").flash(plan)
        self.assertEqual(outcome.status, "metadata_failed")
        self.assertEqual(outcome.failure_phase, "metadata_programming")
        self.assertEqual(len(runner.commands), 4)
        rendered = " ".join(item for command in runner.commands for item in command)
        self.assertNotIn("reset run", rendered)

    def test_metadata_readback_mismatch_never_resets_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            wrong = bytearray(build_stlink_metadata(plan.image))
            wrong[16] ^= 0x01
            runner = ScriptedRunner([
                supported_target(),
                result("** Verified OK **"),
                metadata_program_result(plan),
            ])
            runner.metadata_readback_override = bytes(wrong)
            outcome = B300Service(runner=runner, executable="openocd").flash(plan)
        self.assertEqual(outcome.status, "metadata_failed")
        self.assertEqual(outcome.failure_phase, "metadata_verifying")
        self.assertEqual(len(runner.commands), 4)
        self.assertNotIn("reset run", " ".join(item for c in runner.commands for item in c))

    def test_reinspection_blocks_if_bootloader_wrp_disappeared(self) -> None:
        runner = ScriptedRunner([result(
            "Info : Target voltage: 3.09\n"
            "Info : device id = 0x101f6413\n"
            "Info : flash size = 512 KiB\n"
            "#  0: 0x00000000 (0x4000 16kB) not protected\n"
            "#  1: 0x00004000 (0x4000 16kB) protected\n"
            "#  2: 0x00008000 (0x4000 16kB) protected\n"
            "#  3: 0x0000c000 (0x4000 16kB) not protected\n"
            "#  4: 0x00010000 (0x10000 64kB) not protected\n"
            "#  5: 0x00020000 (0x20000 128kB) not protected\n"
            "#  6: 0x00040000 (0x20000 128kB) not protected\n"
            "#  7: 0x00060000 (0x20000 128kB) not protected"
        )])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProvisioningError, "WRP"):
                B300Service(runner=runner, executable="openocd").flash(
                    self.make_plan(directory)
                )
        self.assertEqual(len(runner.commands), 2)
        self.assertNotIn("erase_sector", " ".join(item for c in runner.commands for item in c))

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
        self.assertEqual(len(runner.commands), 2)
        self.assertIn("flash info 0", runner.commands[1])
        self.assertNotIn("erase_sector", " ".join(item for c in runner.commands for item in c))
        self.assertEqual(captured.exception.phase, "target_check")

    def test_target_usb_permission_error_has_ubuntu_udev_guidance(self) -> None:
        runner = ScriptedRunner([
            CommandResult(("openocd",), 1,
                          "Error: libusb_open() failed with LIBUSB_ERROR_ACCESS")
        ])
        with self.assertRaisesRegex(RuntimeError, "udev rule") as captured:
            B300Service(runner=runner, executable="openocd").inspect_target(
                ProbeRef(None)
            )
        message = str(captured.exception)
        self.assertIn("0483", message)
        self.assertIn("374x", message)
        self.assertIn("do not use sudo", message)

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

    def test_valid_previous_metadata_advances_verified_and_confirmed_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            previous = ScriptedRunner._confirmed(build_stlink_metadata(plan.image, sequence=8))
            runner = ScriptedRunner([
                supported_target(),
                result("** Verified OK **"),
                metadata_program_result(plan),
                result("reset complete"),
                result("pc (/32): 0x0802496e\n0x40002854: 00000000"),
            ])
            runner.previous_metadata = previous
            outcome = B300Service(runner=runner, executable="openocd").flash(plan)
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.written_metadata.sequence, 10)
        self.assertEqual(outcome.written_metadata.state_name, "VERIFIED")
        self.assertEqual(outcome.confirmed_metadata.sequence, 11)
        self.assertEqual(outcome.confirmed_metadata.state_name, "CONFIRMED")
        self.assertEqual(outcome.verified_metadata_bytes, build_stlink_metadata(plan.image, sequence=10))

    def test_confirmation_mismatch_times_out_fail_closed_without_boot_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = self.make_plan(directory)
            runner = ScriptedRunner([
                supported_target(),
                result("** Verified OK **"),
                metadata_program_result(plan),
                result("reset complete"),
            ])
            # Valid STLM, but the Bootloader confirmation sequence never becomes the
            # expected successor for the record written by this transaction.
            runner.confirmation_override = ScriptedRunner._confirmed(
                build_stlink_metadata(plan.image, sequence=100)
            )
            now = [0.0]
            service = B300Service(
                runner=runner, executable="openocd",
                clock=lambda: now[0],
                sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
            )
            outcome = service.flash(plan)
        self.assertEqual(outcome.status, "programmed_boot_failed")
        self.assertEqual(outcome.failure_phase, "metadata_confirming")
        self.assertIsNone(outcome.confirmed_metadata)
        self.assertIsNone(outcome.boot_command)
        self.assertIn("did not confirm", outcome.reason.lower())



if __name__ == "__main__":
    unittest.main()
