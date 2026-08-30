from __future__ import annotations

import os
import time
import unittest
from types import SimpleNamespace

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
        self.assertEqual(tab.metadata_values["Source"].text(), "OTA (OTAM)")
        self.assertEqual(tab.metadata_values["State"].text(), "CONFIRMED (3)")
        self.assertEqual(tab.metadata_values["Board token"].text(), "B300_F407ZE")

    def test_application_health_renders_bootable_crc_and_vector_evidence(self) -> None:
        tab = MemoryTab(service=object(), probe_provider=lambda: None)
        metadata = decode_ota_metadata(make_metadata(state=3))
        health = SimpleNamespace(
            metadata=metadata, lifecycle="BOOTABLE", bootable=True,
            reason="Application Metadata, image CRC, and vector permit bootability.",
            next_action="No action is required.", bytes_checked=126580,
            image_crc_valid=True, actual_image_crc32=metadata.image_crc32,
            application_vector=SimpleNamespace(
                valid=True, reset_vector=0x08010361, reason="Application vector is valid."
            ),
        )
        tab.show_application_health(health)
        self.assertEqual(tab.health_values["Lifecycle"].text(), "BOOTABLE")
        self.assertEqual(tab.health_values["Bootable"].text(), "YES")
        self.assertEqual(tab.health_values["Image CRC"].text(), "MATCH")
        self.assertEqual(tab.health_values["Expected CRC32"].text(), "0x%08X" % metadata.image_crc32)
        self.assertEqual(tab.health_values["Actual CRC32"].text(), "0x%08X" % metadata.image_crc32)
        self.assertEqual(tab.health_values["Vector"].text(), "VALID · reset=0x08010361")
        self.assertEqual(tab.health_values["Bytes checked"].text(), "126580")
        self.assertIn("BOOTABLE", tab.health_notice.text())
        self.assertIn("MATCH", tab.range_info_label.text())

    def test_application_health_renders_nonbootable_crc_mismatch_with_next_action(self) -> None:
        tab = MemoryTab(service=object(), probe_provider=lambda: None)
        metadata = decode_ota_metadata(make_metadata(state=3))
        health = SimpleNamespace(
            metadata=metadata, lifecycle="IMAGE_CRC_MISMATCH", bootable=False,
            reason="Application image CRC does not match metadata and cannot prove bootability.",
            next_action="Reprovision or OTA-recover the Application.", bytes_checked=metadata.image_size,
            image_crc_valid=False, actual_image_crc32=0xDEADBEEF,
            application_vector=SimpleNamespace(
                valid=True, reset_vector=0x08010361, reason="Application vector is valid."
            ),
        )
        tab.show_application_health(health)
        self.assertEqual(tab.health_values["Lifecycle"].text(), "IMAGE_CRC_MISMATCH")
        self.assertEqual(tab.health_values["Bootable"].text(), "NO")
        self.assertEqual(tab.health_values["Image CRC"].text(), "MISMATCH")
        self.assertEqual(tab.health_values["Actual CRC32"].text(), "0xDEADBEEF")
        self.assertIn("OTA-recover", tab.health_values["Next action"].text())
        self.assertIn("IMAGE_CRC_MISMATCH", tab.health_notice.text())

    def test_application_health_worker_is_read_only_busy_aware_and_survives_until_finish(self) -> None:
        metadata = decode_ota_metadata(make_metadata(state=3))
        health = SimpleNamespace(
            metadata=metadata, lifecycle="BOOTABLE", bootable=True, reason="healthy",
            next_action="No action is required.", bytes_checked=metadata.image_size,
            image_crc_valid=True, actual_image_crc32=metadata.image_crc32,
            application_vector=SimpleNamespace(valid=True, reset_vector=0x08010361, reason="valid"),
        )
        calls = []

        class HealthService:
            def inspect_application_health(self, probe, event_sink=None, cancel_event=None):
                calls.append((probe, event_sink is not None, cancel_event is not None))
                return health

        probe = object()
        tab = MemoryTab(service=HealthService(), probe_provider=lambda: probe)
        tab.read_application_health()
        self.assertTrue(tab._busy)
        self.assertFalse(tab.health_button.isEnabled())
        deadline = time.monotonic() + 1.0
        while tab._threads and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(calls, [(probe, True, True)])
        self.assertFalse(tab._busy)
        self.assertTrue(tab.health_button.isEnabled())
        self.assertEqual(tab.health_values["Lifecycle"].text(), "BOOTABLE")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_metadata_snapshot_is_invalidated_after_external_flash_change(self) -> None:
        tab = MemoryTab(service=object(), probe_provider=lambda: None)
        tab.show_metadata(decode_ota_metadata(make_metadata(state=1)))
        self.assertEqual(tab.metadata_values["State"].text(), "IN_PROGRESS (1)")
        tab.health_values["Lifecycle"].setText("BOOTABLE")
        tab.invalidate_metadata_view("Application provisioning completed.")
        self.assertEqual(tab.metadata_values["Classification"].text(), "STALE")
        self.assertEqual(tab.metadata_values["State"].text(), "—")
        self.assertEqual(tab.health_values["Lifecycle"].text(), "STALE")
        self.assertIn("Application Health snapshot", tab.health_notice.text())
        self.assertIn("cần đọc lại", tab.status_label.text())
        self.assertIn("Đọc Application metadata", tab.metadata_notice.text())

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
