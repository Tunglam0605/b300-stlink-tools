from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from b300_core.metadata import decode_ota_metadata
from b300_gui.memory_tab import MemoryTab, format_hex_preview
from tests.test_core_probe_memory_metadata import make_metadata


class GuiMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.processEvents()
        # Keep the process-wide Qt application alive for later GUI test modules.
        cls.app = None

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

    def test_memory_labels_pair_vietnamese_with_technical_english(self) -> None:
        tab = MemoryTab(service=object(), probe_provider=lambda: None)
        labels = {label.text() for label in tab.findChildren(QLabel)}
        self.assertIn("CHỈ ĐỌC (READ-ONLY) · CPU tạm dừng khi đọc", " ".join(labels))
        self.assertIn("Phân loại (Classification):", labels)
        self.assertIn("Trạng thái (State):", labels)
        self.assertIn("Kích thước image (Image size):", labels)

    def test_sector_worker_survives_until_read_finishes(self) -> None:
        class ReadService:
            def read_sector(self, probe, sector_index, event_sink=None,
                            cancel_event=None):
                return b"\x12\x34"

        tab = MemoryTab(service=ReadService(), probe_provider=lambda: None)
        tab.read_selected_sector()
        deadline = time.monotonic() + 1.0
        while not tab.current_data and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(tab.current_data, b"\x12\x34")
        self.assertTrue(tab.read_button.isEnabled())
        while tab._threads and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
