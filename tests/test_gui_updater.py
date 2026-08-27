from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from b300_core.updater import UpdateCheckResult
from b300_core.release_manifest import parse_latest_manifest
from b300_gui.main_window import MainWindow
from tests.test_gui_smoke import FakeService
from tests.test_release_manifest import MESSAGE, SIGNATURE, TEST_PUBLIC_KEY


RELEASE = parse_latest_manifest(MESSAGE, SIGNATURE, TEST_PUBLIC_KEY)


class FakeUpdateClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def check(self, current_version):
        if self.error:
            raise self.error
        return self.result


class GuiUpdaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.processEvents()
        cls.app.shutdown()
        cls.app = None

    def make_window(self, client=None, installer=None, settings=None,
                    automatic_updates=False) -> MainWindow:
        return MainWindow(
            service=FakeService(), probe_loader=lambda: (),
            update_client=client, automatic_updates=automatic_updates,
            update_installer=installer,
            settings=settings,
        )

    def temporary_settings(self, root: Path) -> QSettings:
        settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        settings.clear()
        return settings

    def test_help_menu_exposes_update_release_notes_and_about(self) -> None:
        window = self.make_window()
        self.assertEqual(window.check_updates_action.text(), "Kiểm tra cập nhật")
        self.assertEqual(window.release_notes_action.text(), "Ghi chú phiên bản")
        self.assertEqual(window.about_action.text(), "Giới thiệu")
        window.show_about()
        self.assertEqual(window.about_dialog.version_value.text(), "0.3.0")
        self.assertIn("0.12.0-7", window.about_dialog.openocd_value.text())
        self.assertTrue(window.about_dialog.build_value.text())
        window.about_dialog.close()
        window.close()

    def test_available_release_dialog_displays_version_notes_and_download(self) -> None:
        result = UpdateCheckResult(True, RELEASE, RELEASE.platforms["windows-x64"])
        window = self.make_window(FakeUpdateClient(result=result))
        window._update_check_finished(result, manual=True)
        dialog = window.update_dialog
        self.assertEqual(dialog.new_version_value.text(), "0.3.1")
        self.assertIn("Safe update", dialog.notes_view.toPlainText())
        self.assertEqual(dialog.action_button.text(), "Tải bản cập nhật")
        self.assertTrue(dialog.action_button.isEnabled())
        dialog.close()
        window.close()

    def test_automatic_failure_is_silent_but_manual_failure_is_visible(self) -> None:
        window = self.make_window()
        with mock.patch.object(QMessageBox, "warning") as warning:
            window._update_check_failed(RuntimeError("offline"), manual=False)
            warning.assert_not_called()
            window._update_check_failed(RuntimeError("offline"), manual=True)
            warning.assert_called_once()
        window.close()

    def test_install_action_tracks_all_hardware_busy_sources(self) -> None:
        result = UpdateCheckResult(True, RELEASE, RELEASE.platforms["windows-x64"])
        window = self.make_window()
        window._update_check_finished(result, manual=True)
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / result.asset.filename
            package.write_bytes(b"ready")
            window._update_download_finished(package)
            self.assertTrue(window.update_dialog.action_button.isEnabled())
            self.assertEqual(window.update_dialog.action_button.text(), "Cài đặt ngay")
            window.busy = True
            window._refresh_update_install_state()
            self.assertFalse(window.update_dialog.action_button.isEnabled())
            self.assertIn("thao tác phần cứng", window.update_dialog.install_reason.text())
            window.busy = False
            window.memory_tab._threads.append(object())
            window.memory_tab.operation_state_changed.emit(True)
            self.assertFalse(window.update_dialog.action_button.isEnabled())
            window.memory_tab._threads.clear()
            window.memory_tab.operation_state_changed.emit(False)
            self.assertTrue(window.update_dialog.action_button.isEnabled())
        window.update_dialog.close()
        window.close()

    def test_windows_install_launches_verified_plan_only_after_confirmation(self) -> None:
        result = UpdateCheckResult(True, RELEASE, RELEASE.platforms["windows-x64"])
        launched = []
        window = self.make_window(installer=launched.append)
        window._update_check_finished(result, manual=True)
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / result.asset.filename
            package.write_bytes(b"verified")
            window._update_download_finished(package.resolve())
            with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                window._install_downloaded_update()
        self.assertEqual(len(launched), 1)
        self.assertTrue(launched[0].managed)
        self.assertEqual(launched[0].package.name, result.asset.filename)

    def test_first_run_records_version_without_showing_whats_new(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = self.temporary_settings(Path(temp))
            window = self.make_window(settings=settings)
            window._show_whats_new_if_needed()
            self.assertEqual(settings.value("updates/last_seen_version"), "0.3.0")
            self.assertIsNone(window.whats_new_dialog)
            window.close()

    def test_upgrade_shows_whats_new_once_and_records_current_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = self.temporary_settings(Path(temp))
            settings.setValue("updates/last_seen_version", "0.2.0")
            window = self.make_window(settings=settings)
            window._show_whats_new_if_needed()
            self.assertIsNotNone(window.whats_new_dialog)
            self.assertIn("0.3.0", window.whats_new_dialog.windowTitle())
            self.assertIn("GitHub Releases", window.whats_new_dialog.notes_view.toPlainText())
            self.assertEqual(settings.value("updates/last_seen_version"), "0.3.0")
            window.whats_new_dialog.close()
            window.whats_new_dialog = None
            window._show_whats_new_if_needed()
            self.assertIsNone(window.whats_new_dialog)
            window.close()

    def test_same_newer_or_corrupt_seen_version_does_not_show_whats_new(self) -> None:
        for value in ("0.3.0", "0.4.0", "not-a-version"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                settings = self.temporary_settings(Path(temp))
                settings.setValue("updates/last_seen_version", value)
                window = self.make_window(settings=settings)
                window._show_whats_new_if_needed()
                self.assertIsNone(window.whats_new_dialog)
                self.assertEqual(settings.value("updates/last_seen_version"), "0.3.0")
                window.close()

    def test_disabled_automatic_updates_do_not_schedule_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = self.temporary_settings(Path(temp))
            settings.setValue("updates/automatic", False)
            client = FakeUpdateClient(error=AssertionError("must not run"))
            window = self.make_window(
                client=client, settings=settings, automatic_updates=True,
            )
            with mock.patch.object(window, "check_for_updates") as check:
                self.app.processEvents()
                check.assert_not_called()
            window.close()


if __name__ == "__main__":
    unittest.main()
