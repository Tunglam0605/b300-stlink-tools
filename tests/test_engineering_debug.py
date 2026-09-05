import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import unittest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QSpinBox
from b300_core.gateway_profiles import GatewayProfile
from b300_core.project_profiles import ProjectProfile
from b300_core.vscode_environment import VsCodeEnvironmentStatus
from b300_gui.app_context import AppContext
from b300_gui.views.debug_vscode_view import DebugVsCodeView

class EngineeringDebugTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.context=AppContext()
        self.project=ProjectProfile('main','Main',Path('workspace'),Path('main.axf'))
        self.gateway=GatewayProfile.create('Lab','lab.example','operator',2222,profile_id='lab')
        self.context.set_profiles((self.project,),(self.gateway,))
        self.view=DebugVsCodeView(context=self.context)
        self.addCleanup(self.view.close)
    def test_one_action_routes_shared_local_and_remote_selection(self):
        local=[]; remote=[]
        self.view.open_local_vscode_requested.connect(lambda a,b:local.append((a,b)))
        self.view.open_remote_vscode_requested.connect(remote.append)
        self.view.btn_open_vscode.click()
        self.assertEqual(local,[(Path('workspace'),Path('main.axf'))])
        self.context.select_connection('lab')
        self.view.btn_open_vscode.click()
        self.assertEqual(remote,[{'host':'lab.example','user':'operator','ssh_port':2222,'local_gdb_port':0,'workspace':Path('workspace'),'elf':Path('main.axf'),'gateway_id':'lab','project_id':'main'}])
        self.assertEqual(self.view.findChildren(QComboBox),[self.view.activity_log.filter_combo])
        self.assertEqual(self.view.findChildren(QLineEdit),[])
        self.assertEqual(self.view.findChildren(QSpinBox),[])
    def test_busy_or_missing_project_refuses_launch_and_ssh_test_is_remote_only(self):
        emitted=[]; self.view.open_local_vscode_requested.connect(lambda *args:emitted.append(args))
        self.assertFalse(self.view.btn_test_client_conn.isEnabled())
        self.context.set_hardware_busy(True)
        self.view._open_vscode()
        self.assertEqual(emitted,[])
        self.assertFalse(self.view.btn_open_vscode.isEnabled())
        self.context.set_hardware_busy(False)
        self.context.set_profiles((),())
        self.assertFalse(self.view.btn_open_vscode.isEnabled())
    def test_environment_unknown_and_reported_results_are_distinct(self):
        self.assertIn('Chưa kiểm tra',self.view.env_vscode.text())
        self.assertNotIn('Sẵn sàng',self.view.env_openocd.text())
        self.view.set_environment_status(VsCodeEnvironmentStatus(True,False,True,reason='Extension missing'))
        self.assertIn('Sẵn sàng',self.view.env_vscode.text())
        self.assertIn('Thiếu',self.view.env_cortex.text())
        self.assertEqual(self.view.env_detail.text(),'Extension missing')
        self.view.set_bridge_state('LOCAL','READY','bridge online','127.0.0.1:3333')
        self.assertTrue(self.view.btn_stop_bridge.isEnabled())
        self.view.set_bridge_state(None,'STOPPED')
        self.assertFalse(self.view.btn_stop_bridge.isEnabled())

    def test_rebinding_detaches_old_context_and_remote_test_uses_current_endpoint(self):
        second=AppContext()
        second.set_profiles((self.project,),(self.gateway,),default_gateway_id='lab')
        self.view.bind_context(second)
        events=[]
        self.view.test_client_connection_requested.connect(events.append)
        self.context.set_hardware_busy(True)
        self.assertTrue(self.view.btn_open_vscode.isEnabled())
        self.view.btn_test_client_conn.click()
        self.assertEqual(events[0]['host'],'lab.example')
        second.set_hardware_busy(True)
        self.view._emit_client_connection_test()
        self.assertEqual(len(events),1)

    def test_responsive_launch_session_row_and_real_activity_log(self):
        from PySide6.QtWidgets import QBoxLayout
        self.view.resize(1000,650)
        self.view.show()
        self.app.processEvents()
        self.assertTrue(hasattr(self.view,'work_row'))
        self.assertEqual(self.view.work_row.direction(),QBoxLayout.Direction.LeftToRight)
        self.view.resize(700,650)
        self.app.processEvents()
        self.assertEqual(self.view.work_row.direction(),QBoxLayout.Direction.TopToBottom)
        self.view.append_log('INFO bridge request received')
        self.assertIn('bridge request received',self.view.activity_log.terminal.toPlainText())

    def test_bridge_endpoint_is_visible_only_from_actual_ready_state(self):
        self.view.set_bridge_state('CLIENT','READY','SSH forward established','127.0.0.1:43819')
        self.assertIn('127.0.0.1:43819',self.view.bridge_status.text())
        self.view.set_bridge_state('CLIENT','FAILED','Forward closed')
        self.assertEqual(self.view.bridge_status.property('state'),'failure')
        self.assertNotIn('43819',self.view.bridge_status.text())
