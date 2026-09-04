from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


if __name__ == "__main__":
    unittest.main()
