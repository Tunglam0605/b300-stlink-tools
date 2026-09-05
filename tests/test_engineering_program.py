"""Project-driven programming presentation; no hardware operations."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication, QRadioButton, QWidget, QScrollArea
from PySide6.QtCore import QPoint
from b300_gui.views.program_view import ProgramView
from b300_gui.widgets.flash_plan_bar import FlashPlanBar
from b300_core.models import TargetInfo


class EngineeringProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_project_firmware_has_only_secondary_override(self):
        view = ProgramView()
        self.assertIn("Chọn HEX khác", view.btn_browse_app.text())
        self.assertFalse(view.findChildren(QRadioButton))
        self.assertFalse(hasattr(view, "remote_fw_edit"))
        self.assertFalse(hasattr(view, "cb_dry_run"))

    def test_shared_program_cards_and_policy_bar(self):
        view = ProgramView()
        self.assertGreaterEqual(sum(w.objectName() == "engineeringCard" for w in view.findChildren(QWidget)), 4)
        bar = view.flash_plan_bar
        self.assertFalse(any("#" in w.styleSheet() for w in [bar, *bar.findChildren(QWidget)]))
        bar.resize(1050, 200)
        bar.show()
        self.app.processEvents()
        self.assertLess(bar.seg_boot.x(), bar.seg_meta.x())
        self.assertLess(bar.seg_meta.x(), bar.seg_app.x())

    def test_clear_project_file_disables_stale_actions(self):
        view = ProgramView()
        view._selected_file = Path("application.hex")
        view.btn_flash_app.setEnabled(True)
        view.btn_dry_run_action.setEnabled(True)
        view.clear_project_file()
        self.assertFalse(view.btn_flash_app.isEnabled())
        self.assertFalse(view.btn_dry_run_action.isEnabled())
        self.assertIsNone(view._selected_file)

    def test_dry_run_and_program_remain_distinct(self):
        view = ProgramView()
        view._selected_file = Path("application.hex")
        calls = []
        view.flash_application_requested.connect(lambda path, dry: calls.append(dry))
        view._on_dry_run_clicked()
        view._on_flash_app_clicked()
        self.assertEqual(calls, [True, False])

    def test_target_summary_keeps_inspected_capacity(self):
        view = ProgramView()
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "protected", (0, 1, 2), True))
        self.assertIn("512 KiB", view.lbl_target.text())

    def test_laptop_content_does_not_require_horizontal_scroll(self):
        view = ProgramView()
        view.resize(1100, 680)
        view.show()
        self.app.processEvents()
        self.assertEqual(view.findChild(QScrollArea).horizontalScrollBar().maximum(), 0)
        view.close()

    def test_failed_check_color_is_cleared_with_evidence(self):
        view = ProgramView()
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "unprotected", (), True))
        self.assertEqual(view.badge_preflight.objectName(), "statusPillDanger")
        view.set_target_info(None)
        self.assertEqual(view.badge_preflight.objectName(), "statusPillNeutral")

    def test_primary_action_stays_in_laptop_viewport_while_scrolling(self):
        view = ProgramView()
        view.resize(1100, 530)
        view.show()
        self.app.processEvents()
        scroll = view.findChild(QScrollArea)
        for position in (0, scroll.verticalScrollBar().maximum()):
            scroll.verticalScrollBar().setValue(position)
            self.app.processEvents()
            top = view.btn_flash_app.mapTo(view, QPoint(0, 0))
            self.assertGreaterEqual(top.y(), 0)
            self.assertLessEqual(top.y() + view.btn_flash_app.height(), view.height())
        view.close()


if __name__ == "__main__":
    unittest.main()
