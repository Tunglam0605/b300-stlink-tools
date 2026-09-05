"""Vietnamese production copy for the PROGRAM and DEVICE surfaces."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
import unittest

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from b300_core.models import ProbeInfo, TargetInfo
from b300_gui.views.device_view import DeviceView
from b300_gui.views.program_view import ProgramView
from b300_gui.widgets.device_info_panel import DeviceInfoPanel
from b300_gui.widgets.flash_plan_bar import FlashPlanBar


FORBIDDEN_UI_ENGLISH = re.compile(
    r"\b(?:Application|Target|Project|Protection|Firmware|Preflight|Override|"
    r"Factory|Flash|Device ID|Revision|Sectors?|READY|DISCONNECTED|OFFLINE|"
    r"PROTECTED|readable|Read protected|evidence)\b|Dry Run|Address span|"
    r"Option Bytes|Vector table|Reset reason|core policy",
    re.IGNORECASE,
)


def visible_copy(widget):
    values = []
    for child in (*widget.findChildren(QLabel), *widget.findChildren(QPushButton)):
        current = child
        hidden = False
        while current is not None and current is not widget:
            if current.isHidden():
                hidden = True
                break
            current = current.parentWidget()
        if hidden:
            continue
        values.extend((child.text(), child.toolTip()))
    return "\n".join(value for value in values if value)


class VietnameseProgramDeviceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def assertVietnameseUi(self, widget):
        text = visible_copy(widget)
        match = FORBIDDEN_UI_ENGLISH.search(text)
        self.assertIsNone(match, "Còn chuỗi giao diện tiếng Anh: %r\n%s" % (match.group(0) if match else None, text))

    def test_program_chrome_and_dynamic_target_state_are_vietnamese(self):
        view = ProgramView()
        view.set_probes((ProbeInfo("TEST-A", "ST-Link V3", "test"),), "TEST-A")
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "S0-S2 protected", (0, 1, 2), True))
        self.assertEqual(view.btn_flash_app.text(), "NẠP ỨNG DỤNG")
        self.assertEqual(view.btn_dry_run_action.text(), "CHẠY THỬ")
        self.assertVietnameseUi(view)

    def test_device_chrome_and_dynamic_evidence_are_vietnamese(self):
        view = DeviceView()
        view.set_probes((ProbeInfo("TEST-A", "ST-Link V3", "test"),), "TEST-A")
        view.set_target_info(TargetInfo(0x413, 512, 3.3, "S0-S2 protected", (0, 1, 2), True))
        self.assertEqual(view.btn_doctor.text(), "KIỂM TRA MCU")
        self.assertVietnameseUi(view)

    def test_device_sidebar_and_memory_plan_are_vietnamese(self):
        panel = DeviceInfoPanel()
        panel.set_probes((ProbeInfo("TEST-A", "ST-Link V3", "test"),), "TEST-A")
        panel.set_target_info(TargetInfo(0x413, 512, 3.3, "S0-S2 protected", (0, 1, 2), True))
        plan = FlashPlanBar()
        self.assertVietnameseUi(panel)
        self.assertVietnameseUi(plan)


if __name__ == "__main__":
    unittest.main()
