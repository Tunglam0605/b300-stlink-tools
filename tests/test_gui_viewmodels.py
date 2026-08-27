from __future__ import annotations

import tempfile
import unittest

from b300_core.hex_image import inspect_image
from b300_core.models import ProbeRef, TargetInfo
from b300_core.policy import build_flash_plan
from b300_gui.viewmodels import FlashViewState, confirmation_text
from tests.test_core_hex_policy import write_hex


class GuiViewModelTests(unittest.TestCase):
    def test_flash_requires_ready_target_valid_image_and_idle_state(self) -> None:
        self.assertTrue(FlashViewState(True, True, False).can_flash)
        self.assertFalse(FlashViewState(False, True, False).can_flash)
        self.assertFalse(FlashViewState(True, False, False).can_flash)
        self.assertFalse(FlashViewState(True, True, True).can_flash)

    def test_confirmation_names_probe_hash_and_destructive_sector_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = inspect_image(write_hex(directory, 0x08010000, b"\x01"))
            plan = build_flash_plan(
                image,
                ProbeRef("ABC123"),
                TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected"),
            )
            text = confirmation_text(plan)
        self.assertIn("ABC123", text)
        self.assertIn(image.sha256, text)
        self.assertIn("Erase Sector 3–7", text)
        self.assertIn("Sector 0–2", text)


if __name__ == "__main__":
    unittest.main()
