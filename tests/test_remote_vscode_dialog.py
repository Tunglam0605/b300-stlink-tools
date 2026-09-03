from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_gui.remote_vscode_dialog import RemoteVsCodeDialog


class RemoteVsCodeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make_dialog(self) -> RemoteVsCodeDialog:
        dialog = RemoteVsCodeDialog(lambda: None)
        dialog.host_edit.setText("gateway.example")
        dialog.user_edit.setText("automation")
        return dialog

    def test_refresh_preview_requires_no_managed_ssh_prerequisites(self) -> None:
        dialog = self.make_dialog()
        try:
            dialog.refresh_preview()
            preview = dialog.preview.text()
            self.assertIn("password", preview.lower())
            self.assertNotIn("managed b300 ssh identity", preview.lower())
        finally:
            dialog.close()

    def test_export_kit_requires_no_managed_ssh_prerequisites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "remote-kit"
            with mock.patch("b300_gui.remote_vscode_dialog.QFileDialog.getExistingDirectory", return_value=str(destination)) as choose, \
                    mock.patch("b300_gui.remote_vscode_dialog.QMessageBox.warning") as warning, \
                    mock.patch("b300_gui.remote_vscode_dialog.QMessageBox.information") as info:
                dialog = self.make_dialog()
                try:
                    dialog.export_kit()
                    choose.assert_called_once()
                    warning.assert_not_called()
                    info.assert_called_once()
                    self.assertTrue(destination.exists())
                finally:
                    dialog.close()
