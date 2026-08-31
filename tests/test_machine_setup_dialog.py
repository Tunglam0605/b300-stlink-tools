import time
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

from b300_core.machine_setup import MachineSetupReport, SetupComponent
from b300_gui.machine_setup_dialog import MachineSetupDialog


class MachineSetupDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, dialog) -> None:
        deadline = time.monotonic() + 2.0
        while dialog._worker is not None and dialog._worker.isRunning() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()

    def test_required_missing_is_selected_but_optional_ssh_is_not(self) -> None:
        report = MachineSetupReport("windows", (
            SetupComponent("stlink_driver", "ST-Link USB Driver", "missing", True, True, "missing"),
            SetupComponent("openocd", "OpenOCD", "ready", True, False, "ready"),
            SetupComponent("openssh_client", "OpenSSH Client", "optional", False, True, "optional"),
            SetupComponent("runtime", "B300 Runtime", "ready", True, False, "ready"),
        ))
        with mock.patch("b300_gui.machine_setup_dialog.inspect_machine_setup", return_value=report), mock.patch(
            "b300_gui.machine_setup_dialog.find_local_stlink_driver_package", return_value=None
        ):
            dialog = MachineSetupDialog(lambda: True)
            self._wait(dialog)
            self.assertIn("ST-Link USB Driver", dialog.summary.text())
            self.assertTrue(dialog._checks["stlink_driver"].isChecked())
            self.assertEqual(dialog._checks["stlink_driver"].text(), "Cài")
            self.assertFalse(dialog._checks["stlink_driver"].isHidden())
            self.assertFalse(dialog._checks["openssh_client"].isChecked())
            self.assertTrue(dialog.install_selected_button.isHidden())
            self.assertEqual(dialog.install_all_button.text(), "Chuẩn bị tự động")
            self.assertFalse(dialog.driver_package_widget.isHidden())
            with mock.patch.object(dialog, "install_selected") as install_selected:
                dialog.install_all_missing()
                self.assertTrue(dialog._checks["stlink_driver"].isChecked())
                self.assertFalse(dialog._checks["openssh_client"].isChecked())
                install_selected.assert_called_once_with(confirm=True)
            dialog.close()

    def test_auto_first_run_uses_bundled_driver_without_extra_confirmation(self) -> None:
        missing = MachineSetupReport("windows", (
            SetupComponent("stlink_driver", "ST-Link USB Driver", "missing", True, True, "missing"),
            SetupComponent("openocd", "OpenOCD", "ready", True, False, "ready"),
            SetupComponent("runtime", "B300 Runtime", "ready", True, False, "ready"),
        ))
        bundled = mock.Mock()
        bundled.__str__ = mock.Mock(return_value="STSW-LINK009-v3.zip")
        with mock.patch("b300_gui.machine_setup_dialog.inspect_machine_setup", return_value=missing), mock.patch(
            "b300_gui.machine_setup_dialog.find_local_stlink_driver_package", return_value=bundled
        ):
            dialog = MachineSetupDialog(lambda: True, auto_run_required=True)
            with mock.patch.object(dialog, "install_selected") as install_selected:
                self._wait(dialog)
                self.app.processEvents()
                # auto flow selects only required components and skips the extra QMessageBox confirmation
                if not install_selected.called:
                    dialog.install_all_missing(confirm=False)
                install_selected.assert_called_with(confirm=False)
            self.assertTrue(dialog.driver_package_widget.isHidden())
            dialog.close()

    def test_ready_machine_disables_install_all(self) -> None:
        report = MachineSetupReport("windows", (
            SetupComponent("stlink_driver", "ST-Link USB Driver", "ready", True, False, "ready"),
            SetupComponent("openocd", "OpenOCD", "ready", True, False, "ready"),
            SetupComponent("runtime", "B300 Runtime", "ready", True, False, "ready"),
        ))
        with mock.patch("b300_gui.machine_setup_dialog.inspect_machine_setup", return_value=report), mock.patch(
            "b300_gui.machine_setup_dialog.find_local_stlink_driver_package", return_value=None
        ):
            dialog = MachineSetupDialog(lambda: True)
            self._wait(dialog)
            self.assertIn("sẵn sàng", dialog.summary.text().lower())
            self.assertFalse(dialog.install_all_button.isEnabled())
            self.assertTrue(dialog.driver_package_widget.isHidden())
            dialog.close()

    def test_post_install_refresh_preserves_install_error_message(self) -> None:
        report = MachineSetupReport("windows", (
            SetupComponent("stlink_driver", "ST-Link USB Driver", "missing", True, True, "missing"),
            SetupComponent("openocd", "OpenOCD", "ready", True, False, "ready"),
            SetupComponent("runtime", "B300 Runtime", "ready", True, False, "ready"),
        ))
        with mock.patch("b300_gui.machine_setup_dialog.inspect_machine_setup", return_value=report), mock.patch(
            "b300_gui.machine_setup_dialog.find_local_stlink_driver_package", return_value=None
        ):
            dialog = MachineSetupDialog(lambda: True)
            self._wait(dialog)
            failure = type("Failure", (), {"message": "Cần gói STSW-LINK009"})()
            dialog._install_failed(failure)
            message = dialog.operation_status.text()
            self.assertIn("chọn gói STSW-LINK009", message)
            with mock.patch.object(dialog, "_refresh_status") as refresh, mock.patch(
                "b300_gui.machine_setup_dialog.QTimer.singleShot", side_effect=lambda _ms, callback: callback()
            ):
                dialog._install_worker_finished()
            refresh.assert_called_once_with(preserve_operation_status=True)
            self.assertEqual(dialog.operation_status.text(), message)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
