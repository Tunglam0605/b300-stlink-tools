import io
import unittest
from contextlib import redirect_stdout
from unittest import mock
from dataclasses import replace

import b300_stlink
from b300_core.gateway_agent import GatewayAgentStatus
from b300_core.gateway_status import GatewaySnapshot
from tests import test_gateway_protocol

class FieldRuntimeTests(unittest.TestCase):
    def test_per_user_runtime_uses_agent_queue_without_legacy_owner(self):
        ready = test_gateway_protocol.GatewayProtocolTests()._ready()
        for mode in ("gateway-status", "gateway-ensure", "gateway-rescan"):
            with self.subTest(mode=mode):
                agent = mock.Mock()
                agent.status.return_value = GatewayAgentStatus("agent", 123, 1.0, "IDLE", "GATEWAY_IDLE")
                queue = mock.Mock()
                queue.submit_request.return_value = {"status": "ok", "result": ready.to_record()}
                with mock.patch.object(b300_stlink, "_isolated_gateway_config", return_value=None), \
                     mock.patch.object(b300_stlink, "GatewayAgentProcessManager", return_value=agent), \
                     mock.patch.object(b300_stlink, "GatewayRequestStore", return_value=queue), \
                     mock.patch.object(b300_stlink, "GatewayProcessManager") as legacy, redirect_stdout(io.StringIO()):
                    legacy.return_value.status.return_value = ready
                    legacy.return_value.ensure.return_value = ready
                    legacy.return_value.rescan.return_value = ready
                    self.assertEqual(b300_stlink.main(["debug", mode, "--json"]), 0)
                queue.submit_request.assert_called_once()
                self.assertEqual(queue.submit_request.call_args.args[0].operation, "runtime_" + mode[8:])
                legacy.assert_not_called()

class FieldIdleHealthTests(unittest.TestCase):
    def test_authenticated_idle_agent_is_not_stale_or_attach_ready(self):
        from PySide6.QtWidgets import QApplication
        from b300_gui.gateway_health_controller import GatewayHealthController
        from tests.test_gateway_health_controller import FakeManager, snapshot
        app = QApplication.instance() or QApplication([])
        controller = GatewayHealthController(FakeManager(), worker_factory=None)
        stopped = snapshot("STOPPED", reason="GATEWAY_PROCESS_NOT_RUNNING")
        controller.accept_snapshot(replace(stopped, agent_status=GatewayAgentStatus("agent", 123, 1.0, "IDLE", "GATEWAY_IDLE")))
        self.assertEqual(controller.warning, "")
        self.assertFalse(controller.attach_ready)
        controller.accept_snapshot(replace(stopped, sequence=2, agent_status=None))
        self.assertNotEqual(controller.warning, "")

class FieldRuntimeFailureTests(unittest.TestCase):
    def test_agent_runtime_failure_never_falls_back_to_legacy_gateway(self):
        agent = mock.Mock()
        agent.status.return_value = GatewayAgentStatus("agent", 123, 1.0, "IDLE", "GATEWAY_IDLE")
        queue = mock.Mock()
        queue.submit_request.side_effect = TimeoutError("agent timeout")
        args = b300_stlink.parse_args(["debug", "gateway-rescan", "--json"])
        with mock.patch.object(b300_stlink, "_isolated_gateway_config", return_value=None), \
             mock.patch.object(b300_stlink, "GatewayAgentProcessManager", return_value=agent), \
             mock.patch.object(b300_stlink, "GatewayRequestStore", return_value=queue), \
             mock.patch.object(b300_stlink, "GatewayProcessManager") as legacy:
            with self.assertRaises(TimeoutError):
                b300_stlink.run_gateway_runtime_command(args)
        legacy.assert_not_called()

    def test_idle_rescan_does_not_start_openocd(self):
        import tempfile
        from pathlib import Path
        from b300_core.gateway_lease_coordinator import GatewayLeaseCoordinator
        from b300_core.gateway_lease import GatewayLeaseStore
        from tests.test_gateway_lease_coordinator import FakeSupervisor
        supervisor = FakeSupervisor()
        with tempfile.TemporaryDirectory() as temporary:
            coordinator = GatewayLeaseCoordinator(supervisor, store=GatewayLeaseStore(Path(temporary)/"lease.json"))
            for action in ("status", "ensure", "rescan"):
                snapshot = coordinator.runtime_snapshot(action)
                self.assertEqual(snapshot.reason_code, "GATEWAY_IDLE")
                self.assertFalse(snapshot.attach_ready)
        self.assertEqual(supervisor.ensure_calls, 0)
        self.assertEqual(supervisor.rescan_calls, 0)

class FieldLegacyStatusTests(unittest.TestCase):
    def test_missing_agent_cannot_advertise_unleased_legacy_ready(self):
        agent = mock.Mock()
        agent.status.return_value = None
        legacy_ready = test_gateway_protocol.GatewayProtocolTests()._ready()
        output = io.StringIO()
        with mock.patch.object(b300_stlink, "_isolated_gateway_config", return_value=None), \
             mock.patch.object(b300_stlink, "GatewayAgentProcessManager", return_value=agent), \
             mock.patch.object(b300_stlink, "GatewayProcessManager") as legacy, redirect_stdout(output):
            legacy.return_value.status.return_value = legacy_ready
            self.assertEqual(b300_stlink.main(["debug", "gateway-status", "--json"]), 1)
        import json
        result = json.loads(output.getvalue())
        self.assertEqual(result["reason_code"], "GATEWAY_AGENT_NOT_RUNNING")
        self.assertIsNone(result["gdb_endpoint"])
        legacy.assert_not_called()
