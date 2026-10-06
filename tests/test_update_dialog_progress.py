import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from b300_gui.update_dialog import UpdateDialog
from b300_core.release_manifest import parse_latest_manifest
from tests.test_release_manifest import MESSAGE, SIGNATURE, TEST_PUBLIC_KEY


class UpdateDialogProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        release = parse_latest_manifest(MESSAGE, SIGNATURE, TEST_PUBLIC_KEY)
        self.dialog = UpdateDialog("0.3.0", release, release.select("windows-x64"))

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_progress_shows_bytes_speed_and_remaining_time(self):
        with patch("b300_gui.update_dialog.time.monotonic", return_value=100):
            self.dialog.set_downloading()
        with patch("b300_gui.update_dialog.time.monotonic", return_value=104):
            self.dialog.set_download_progress(8 * 1024 * 1024, 16 * 1024 * 1024)
        self.assertEqual(self.dialog.progress.value(), 50)
        text = self.dialog.download_details.text()
        self.assertIn("8.0 / 16.0 MiB", text)
        self.assertIn("2.00 MiB/s", text)
        self.assertIn("4 giây", text)

    def test_retry_resets_speed_and_waits_for_first_bytes(self):
        with patch("b300_gui.update_dialog.time.monotonic", return_value=100):
            self.dialog.set_downloading()
        with patch("b300_gui.update_dialog.time.monotonic", return_value=200):
            self.dialog.set_downloading()
            self.dialog.set_download_progress(0, 16 * 1024 * 1024)
        self.assertIn("Đang kết nối", self.dialog.download_details.text())
        with patch("b300_gui.update_dialog.time.monotonic", return_value=202):
            self.dialog.set_download_progress(8 * 1024 * 1024, 16 * 1024 * 1024)
        self.assertIn("4.00 MiB/s", self.dialog.download_details.text())

    def test_verified_package_is_ready_for_install(self):
        self.dialog.set_downloading()
        self.dialog.set_ready(Path("B300.exe"))
        self.dialog.set_install_allowed(True)
        self.assertIn("Đã xác minh", self.dialog.download_details.text())
        self.assertEqual(self.dialog.progress.value(), 100)
        self.assertEqual(self.dialog.action_button.text(), "Cài đặt ngay")
        self.assertTrue(self.dialog.action_button.isEnabled())
