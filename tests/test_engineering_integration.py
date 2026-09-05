"""Production shared selection routes without hardware IO."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
from b300_gui.main_window_v18 import MainWindowV18
from b300_core.project_profiles import ProjectProfile, ProjectProfileStore
from b300_core.gateway_profiles import GatewayProfile, GatewayProfileStore
from b300_core.service import FlashResult
from b300_core.models import ProbeInfo
from tests.test_gui_smoke import FakeService
from tests.test_core_hex_policy import write_hex, APPLICATION_VECTOR


class EngineeringIntegrationTests(unittest.TestCase):
    def test_shared_context_routes_and_guards_local_hardware(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            symbols = root / 'app.axf'
            symbols.write_bytes(b'test fixture')
            hexfile = write_hex(temp, 0x08010000, APPLICATION_VECTOR)
            projects = ProjectProfileStore(root / 'projects.json')
            profile = ProjectProfile('p1', 'Project', root, symbols, hexfile)
            projects.upsert(profile)
            gateways = GatewayProfileStore(root / 'gateways.json', legacy_path=root / 'legacy.json')
            gateway = GatewayProfile.create('IPC', '192.0.2.8', 'tester')
            gateways.upsert(gateway)
            probe = ProbeInfo(name='ST-Link', serial='TEST-A', source='usb', usb_identity='test')
            window = MainWindowV18(service=FakeService(), probe_loader=lambda: (probe,),
                automatic_updates=False, first_run_setup=False, project_store=projects, gateway_store=gateways)
            try:
                context = window.app_context
                self.assertIs(window.monitor_view.context, context)
                self.assertIs(window.debug_vscode_view.context, context)
                self.assertTrue(os.path.samefile(window.program_view._selected_file, hexfile))
                context.select_connection(gateway.profile_id)
                window.service.inspect_target = Mock()
                window.inspect_target()
                window.service.inspect_target.assert_not_called()
                self.assertFalse(window.program_view.btn_flash_app.isEnabled())
                self.assertEqual(window.device_view._probes, [])
                context.set_hardware_busy(True)
                self.assertFalse(context.select_connection('local'))
                context.set_hardware_busy(False)
                context.select_connection('local')
                window.show_page('settings')
                self.assertTrue(window.shared_context_bar.isHidden())
                window.show_page('monitor')
                self.assertFalse(window.shared_context_bar.isHidden())
                window._flash_finished(FlashResult('succeeded', None, None, None, None))
                self.assertIsNone(window.target_info)
                self.assertEqual(window._selected_factory_probe().serial, 'TEST-A')
                result = SimpleNamespace(state=SimpleNamespace(gdb_target='127.0.0.1:12345'), symbols=symbols)
                with patch.object(window._vscode_controller, 'start_local', return_value=result) as start, \
                        patch.object(window, '_render_bridge_state'):
                    window._on_v18_open_local_vscode(root, symbols)
                    self.assertEqual(start.call_args.kwargs['probe'].serial, 'TEST-A')
                with patch.object(window._vscode_controller, 'start_gateway', return_value=result.state) as start, \
                        patch.object(window, '_render_bridge_state'):
                    window._on_v18_start_gateway()
                    self.assertEqual(start.call_args.kwargs['probe'].serial, 'TEST-A')
                window.busy = True
                window._update_controls()
                self.assertFalse(window.header_bar.btn_open_project.isEnabled())
                with patch('b300_gui.main_window_v18.ProjectManagerDialog') as manager:
                    window._open_project_manager()
                    manager.assert_not_called()
                window.busy = False
            finally:
                window.close()
                window.deleteLater()
                app.processEvents()
