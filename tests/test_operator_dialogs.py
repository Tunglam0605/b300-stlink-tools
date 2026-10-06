from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from b300_gui.operator_dialogs import SafetyActionDialog


class SafetyActionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dangerous_action_requires_exact_typed_confirmation(self) -> None:
        dialog = SafetyActionDialog(
            "Factory",
            "Bootloader",
            "Authorized maintenance only",
            severity="danger",
            required_text="PROVISION BOOTLOADER",
        )
        try:
            self.assertFalse(dialog.confirm_button.isEnabled())
            self.assertEqual(dialog.confirm_input.placeholderText(), "PROVISION BOOTLOADER")
            dialog.confirm_input.setText("provision bootloader")
            self.assertFalse(dialog.confirm_button.isEnabled())
            dialog.confirm_input.setText("PROVISION BOOTLOADER")
            self.assertTrue(dialog.confirm_button.isEnabled())
        finally:
            dialog.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            self.assertFalse(shiboken6.isValid(dialog))


if __name__ == "__main__":
    unittest.main()
