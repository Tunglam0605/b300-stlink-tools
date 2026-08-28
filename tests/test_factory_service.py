from __future__ import annotations

import unittest

from b300_core.factory_policy import build_factory_plan
from b300_core.factory_resource import load_trusted_bootloader
from b300_core.models import CommandResult, ProbeRef, TargetInfo
from b300_core.service import B300Service


class ScriptedRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.commands = []

    def run(self, command, event_sink=None, **options):
        self.commands.append(tuple(command))
        result = self.outputs.pop(0)
        if event_sink:
            for line in result.output.splitlines():
                event_sink(line)
        return CommandResult(tuple(command), result.returncode, result.output)


def result(output: str, returncode: int = 0) -> CommandResult:
    return CommandResult(("openocd",), returncode, output)


def target_output(protected: bool) -> CommandResult:
    state = "protected" if protected else "not protected"
    return result(
        "Info : Target voltage: 3.10\n"
        "Info : device id = 0x101f6413\n"
        "Info : flash size = 512 KiB\n"
        "#  0: 0x00000000 (0x4000 16kB) %s\n" % state +
        "#  1: 0x00004000 (0x4000 16kB) %s\n" % state +
        "#  2: 0x00008000 (0x4000 16kB) %s\n" % state +
        "#  3: 0x0000c000 (0x4000 16kB) not protected"
    )


class FactoryServiceTests(unittest.TestCase):
    def make_plan(self):
        trusted = load_trusted_bootloader()
        return build_factory_plan(
            trusted.image, ProbeRef("FACTORY123"),
            TargetInfo(0x101F6413, 512, 3.10, "S0-S2 protected", (0, 1, 2), True),
        )

    def test_factory_flow_unprotects_programs_reprotects_and_verifies(self) -> None:
        runner = ScriptedRunner([
            target_output(True),
            result("protection off"),
            target_output(False),
            result("** Programming Started **\n** Verify Started **\n** Verified OK **"),
            result("protection on"),
            target_output(True),
            result("reset complete"),
            target_output(True),
        ])
        outcome = B300Service(runner=runner, executable="openocd").provision_bootloader(
            self.make_plan()
        )
        self.assertTrue(outcome.succeeded)
        rendered = [" ".join(command) for command in runner.commands]
        self.assertIn("flash protect 0 0 2 off", rendered[1])
        self.assertIn("flash erase_sector 0 0 2", rendered[3])
        self.assertIn("flash protect 0 0 2 on", rendered[4])
        self.assertIn("reset run", rendered[6])
        self.assertTrue(outcome.final_target.protection_reported)
        self.assertEqual(outcome.final_target.protected_sectors, (0, 1, 2))
        self.assertNotIn("mass_erase", " ".join(rendered))

    def test_program_failure_still_restores_and_verifies_wrp(self) -> None:
        runner = ScriptedRunner([
            target_output(True),
            result("protection off"),
            target_output(False),
            result("verify failed", 1),
            result("protection on"),
            target_output(True),
        ])
        outcome = B300Service(runner=runner, executable="openocd").provision_bootloader(
            self.make_plan()
        )
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_phase, "programming")
        self.assertIn("WRP was restored", outcome.reason)
        rendered = " ".join(" ".join(command) for command in runner.commands)
        self.assertIn("flash protect 0 0 2 on", rendered)
        self.assertNotIn("reset run", rendered)

    def test_unknown_protection_blocks_before_any_erase_or_protect(self) -> None:
        runner = ScriptedRunner([result(
            "Info : Target voltage: 3.10\n"
            "Info : device id = 0x101f6413\n"
            "Info : flash size = 512 KiB"
        )])
        service = B300Service(runner=runner, executable="openocd")
        with self.assertRaisesRegex(ValueError, "write-protection"):
            service.provision_bootloader(self.make_plan())
        self.assertEqual(len(runner.commands), 1)
        rendered = " ".join(runner.commands[0])
        self.assertNotIn("erase_sector", rendered)
        self.assertNotIn("flash protect", rendered)


    def test_reinspect_failure_after_unprotect_still_requests_wrp_restore(self) -> None:
        runner = ScriptedRunner([
            target_output(True),
            result("protection off"),
            result("temporary connection failure", 1),
            result("protection on"),
            target_output(True),
        ])
        outcome = B300Service(runner=runner, executable="openocd").provision_bootloader(
            self.make_plan()
        )
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.failure_phase, "unprotecting")
        rendered = [" ".join(command) for command in runner.commands]
        self.assertIn("flash protect 0 0 2 off", rendered[1])
        self.assertIn("flash protect 0 0 2 on", rendered[3])
        self.assertNotIn("flash erase_sector 0 0 2", " ".join(rendered))
        self.assertIn("restore", outcome.next_action.lower())



if __name__ == "__main__":
    unittest.main()
