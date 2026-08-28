from __future__ import annotations

import unittest

from b300_core.factory_policy import build_factory_plan
from b300_core.factory_resource import load_trusted_bootloader
from b300_core.models import ProbeRef, TargetInfo
from b300_core.openocd import build_factory_flash_command, build_factory_protect_command


class FactoryOpenOcdTests(unittest.TestCase):
    def make_plan(self):
        trusted = load_trusted_bootloader()
        target = TargetInfo(
            0x101F6413, 512, 3.1, "Sector 0-2 protected; Sector 3-7 not protected",
            (0, 1, 2), True,
        )
        return build_factory_plan(trusted.image, ProbeRef("FACTORY123"), target)

    def test_factory_flash_erases_only_sectors_zero_to_two(self) -> None:
        command = build_factory_flash_command(self.make_plan(), "openocd")
        rendered = "\n".join(command)
        self.assertIn("flash erase_sector 0 0 2", rendered)
        self.assertIn("program {", rendered)
        self.assertIn(" verify", rendered)
        self.assertNotIn("mass_erase", rendered)
        self.assertNotIn("erase_sector 0 3", rendered)
        self.assertNotIn("stm32f2x lock", rendered)
        self.assertNotIn("stm32f2x unlock", rendered)

    def test_factory_wrprotect_commands_touch_only_sectors_zero_to_two(self) -> None:
        off = "\n".join(build_factory_protect_command(ProbeRef("FACTORY123"), "openocd", False))
        on = "\n".join(build_factory_protect_command(ProbeRef("FACTORY123"), "openocd", True))
        self.assertIn("flash protect 0 0 2 off", off)
        self.assertIn("flash protect 0 0 2 on", on)
        self.assertIn("reset halt", off)
        self.assertIn("reset halt", on)
        for rendered in (off, on):
            self.assertNotIn("mass_erase", rendered)
            self.assertNotIn("stm32f2x lock", rendered)
            self.assertNotIn("stm32f2x unlock", rendered)


if __name__ == "__main__":
    unittest.main()
