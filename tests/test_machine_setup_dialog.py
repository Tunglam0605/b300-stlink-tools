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
        with mock.patch("b300_gui.machine_setup_dialog.inspect_machine_setup", return_value=report):
            dialog = MachineSetupDialog(lambda: True)
            self._wait(dialog)
            self.assertIn("ST-Link USB Driver", dialog.summary.text())
            self.assertTrue(dialog._checks["stlink_driver"].isChecked())
            self.assertFalse(dialog._checks["openssh_client"].isChecked())
            with mock.patch.object(dialog, "install_selected") as install_selected:
                dialog.install_all_missing()
                self.assertTrue(dialog._checks["stlink_driver"].isChecked())
                self.assertFalse(dialog._checks["openssh_client"].isChecked())
                install_selected.assert_called_once_with()
            dialog.close()

    def test_ready_machine_disables_install_all(self) -> None:
        report = MachineSetupReport("windows", (
            SetupComponent("stlink_driver", "ST-Link USB Driver", "ready", True, False, "ready"),
            SetupComponent("openocd", "OpenOCD", "ready", True, False, "ready"),
            SetupComponent("runtime", "B300 Runtime", "ready", True, False, "ready"),
        ))
        with mock.patch("b300_gui.machine_setup_dialog.inspect_machine_setup", return_value=report):
            dialog = MachineSetupDialog(lambda: True)
            self._wait(dialog)
            self.assertIn("sẵn sàng", dialog.summary.text().lower())
            self.assertFalse(dialog.install_all_button.isEnabled())
            dialog.close()


if __name__ == "__main__":
    unittest.main()
