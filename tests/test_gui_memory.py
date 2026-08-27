from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from b300_core.metadata import decode_ota_metadata
from b300_gui.memory_tab import MemoryTab, format_hex_preview
from tests.test_core_probe_memory_metadata import make_metadata


class GuiMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_memory_tab_lists_all_sectors_and_has_no_write_controls(self) -> None:
        tab = MemoryTab(service=object(), probe_provider=lambda: None)
        self.assertEqual(tab.sector_combo.count(), 8)
        labels = " ".join(button.text() for button in tab.findChildren(QPushButton))
        self.assertNotIn("Write", labels)
        self.assertNotIn("Mass Erase", labels)
        self.assertNotIn("Option Bytes", labels)
        self.assertFalse(tab.export_button.isEnabled())

    def test_hex_preview_is_bounded_and_marks_omitted_bytes(self) -> None:
        preview = format_hex_preview(bytes(range(256)) * 20, limit=64)
        self.assertIn("00000000", preview)
        self.assertIn("omitted", preview)
        self.assertLess(len(preview), 1000)

    def test_metadata_fields_render_valid_confirmed_record(self) -> None:
        tab = MemoryTab(service=object(), probe_provider=lambda: None)
        tab.show_metadata(decode_ota_metadata(make_metadata(state=3)))
        self.assertEqual(tab.metadata_values["Classification"].text(), "VALID")
        self.assertEqual(tab.metadata_values["State"].text(), "CONFIRMED (3)")
        self.assertEqual(tab.metadata_values["Board token"].text(), "B300_F407ZE")


if __name__ == "__main__":
    unittest.main()
