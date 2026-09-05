import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from b300_core.gateway_profiles import GatewayProfile
from b300_core.gateway_sessions import GatewaySessionManager
from b300_core.models import ProbeInfo, TargetInfo
from b300_core.project_profiles import ProjectProfile

class AppContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import b300_gui
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('b300_gui.app_context'), 'AppContext module must exist')
        from b300_gui.app_context import AppContext
        self.sessions = GatewaySessionManager()
        self.ctx = AppContext(self.sessions)
        self.projects = (ProjectProfile('one','One',Path('work'),Path('one.axf')), ProjectProfile('two','Two',Path('work2'),Path('two.axf')))
        self.gateway = GatewayProfile.create('Lab','lab.example','engineer',profile_id='lab')
        self.ctx.set_profiles(self.projects, (self.gateway,))

    def test_shared_selection_single_notification_and_busy_refusal(self):
        events=[]; self.ctx.changed.connect(lambda: events.append(self.ctx.selected_project))
        self.assertTrue(self.ctx.select_project('two'))
        self.assertEqual(events, [self.projects[1]])
        self.ctx.set_hardware_busy(True)
        self.assertFalse(self.ctx.select_project('one'))
        self.assertFalse(self.ctx.select_connection('lab'))
        self.assertFalse(self.ctx.select_probe('B'))
        self.assertEqual(self.ctx.selected_project, self.projects[1])

    def test_connection_and_probe_invalidate_evidence(self):
        target=TargetInfo(0x413,512,3.3,'unknown')
        self.ctx.set_probes((ProbeInfo('A','ST-Link','usb'),ProbeInfo('B','ST-Link','usb')), 'A')
        self.ctx.set_target_info(target)
        self.ctx.select_probe('B')
        self.assertIsNone(self.ctx.target_info)
        self.ctx.set_target_info(target)
        self.ctx.select_connection('lab')
        self.assertIsNone(self.ctx.target_info)
        self.assertIsNone(self.ctx.selected_probe)
        self.assertEqual(self.ctx.probes, ())
        self.assertIs(self.ctx.gateway_sessions,self.sessions)
        self.assertEqual(self.sessions._sessions, {})

    def test_profiles_keep_selection_and_apply_defaults_on_first_load(self):
        from b300_gui.app_context import AppContext
        context=AppContext(self.sessions)
        context.set_profiles(self.projects,(self.gateway,),default_project_id='two',default_gateway_id='lab')
        self.assertEqual(context.selected_project.project_id,'two')
        self.assertEqual(context.selected_connection.connection_id,'lab')
        context.select_connection('local')
        context.set_profiles(self.projects,(self.gateway,),default_project_id='one',default_gateway_id='lab')
        self.assertTrue(context.selected_connection.is_local)
        self.assertEqual(context.selected_project.project_id,'two')

    def test_bar_renders_context_without_implying_target_ready(self):
        from b300_gui.widgets.shared_context_bar import SharedContextBar
        bar=SharedContextBar(self.ctx)
        self.addCleanup(bar.close)
        self.ctx.set_probes((ProbeInfo('A','ST-Link','usb'),), 'A')
        self.assertIn('đã phát hiện',bar.connection_status.text().lower())
        self.assertIn('chưa kiểm tra',bar.target_label.text().lower())
        self.ctx.select_project('two')
        self.assertEqual(bar.project_combo.currentData(),'two')
        self.ctx.select_connection('lab')
        self.assertIn('chưa kết nối',bar.connection_status.text().lower())
        self.ctx.set_hardware_busy(True)
        for widget in (bar.project_combo,bar.connection_combo,bar.probe_combo,bar.manage_projects_button,bar.manage_connections_button):
            self.assertFalse(widget.isEnabled())

    def test_project_editor_preserves_optional_hex_and_family(self):
        from b300_gui.project_manager_dialog import ProjectEditDialog
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'app.axf').touch(); (root/'app.hex').touch()
            profile=ProjectProfile.create('Main',root,root/'app.axf',application_hex=root/'app.hex',target_family='STM32F407')
            editor=ProjectEditDialog(profile)
            self.addCleanup(editor.close)
            self.assertEqual(editor.profile(),profile)

    def test_bar_keeps_unselected_probe_explicit_with_multiple_devices(self):
        from b300_gui.widgets.shared_context_bar import SharedContextBar
        bar=SharedContextBar(self.ctx)
        self.addCleanup(bar.close)
        self.ctx.set_probes((ProbeInfo('A','ST-Link','usb'),ProbeInfo('B','ST-Link','usb')))
        self.assertIsNone(bar.probe_combo.currentData())
        self.assertIn('chọn st-link',bar.probe_combo.currentText().lower())
        bar.probe_combo.setCurrentIndex(bar.probe_combo.findData('B'))
        self.assertEqual(self.ctx.selected_probe,'B')
        self.assertEqual(bar.probe_combo.toolTip(),'B')

    def test_gateway_reserved_local_id_cannot_select_usb_instead(self):
        gateway=GatewayProfile.create('Remote local','lab.example','operator',profile_id='local')
        self.ctx.set_profiles(self.projects,(gateway,))
        self.assertEqual(len({c.connection_id for c in self.ctx.connections}),2)
        self.assertTrue(self.ctx.select_connection('gateway:local'))
        self.assertEqual(self.ctx.selected_connection.gateway,gateway)
        self.assertFalse(self.ctx.selected_connection.is_local)
