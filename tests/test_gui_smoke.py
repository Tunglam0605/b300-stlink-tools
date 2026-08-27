from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from b300_gui.main_window import MainWindow
from b300_core.hex_image import inspect_image
from b300_core.policy import build_flash_plan
from tests.test_core_hex_policy import write_hex


class FakeService:
    def doctor(self):
        return True, "openocd"

    def inspect_image(self, path):
        return inspect_image(path)

    def plan(self, image, probe):
        return build_flash_plan(image, probe)

    def flash_command(self, plan):
        return ["openocd", "-c", "flash erase_sector 0 3 7"]


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_starts_safe_and_has_no_com_selector(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        self.assertFalse(window.flash_button.isEnabled())
        self.assertEqual(window.probe_combo.count(), 1)
        self.assertIsNone(window.findChild(QWidget, "comSelector"))
        self.assertIn("Sector 3–7", window.flash_plan_label.text())
        window.close()

    def test_valid_image_enables_dry_run_without_hardware_write(self) -> None:
        window = MainWindow(service=FakeService(), probe_loader=lambda: ())
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, b"\x01")
            self.assertTrue(window.load_image_path(image))
            self.assertTrue(window.flash_button.isEnabled())
            window.show_dry_run()
        self.assertIn("DRY-RUN", window.log_view.toPlainText())
        self.assertIn("flash erase_sector 0 3 7", window.log_view.toPlainText())
        window.close()


if __name__ == "__main__":
    unittest.main()
