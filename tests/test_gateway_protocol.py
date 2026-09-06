from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import b300_stlink
from b300_cli.parser import parse_args
from b300_core.gateway_protocol import (
    GATEWAY_ENSURE_COMMAND, GATEWAY_STATUS_COMMAND, gateway_capabilities,
)
from b300_core.debug_service import DebugState
from b300_core.gateway_status import GatewaySnapshot
from b300_core.models import ProbeInfo


class GatewayProtocolTests(unittest.TestCase):
    def test_protocol_advertises_status_and_idempotent_ensure(self) -> None:
        capabilities = gateway_capabilities()
        self.assertEqual(capabilities["protocol_version"], 1)
        self.assertIn("gateway-status", capabilities["capabilities"])
        self.assertIn("gateway-ensure", capabilities["capabilities"])

    def test_remote_commands_are_fixed_per_user_cli_commands(self) -> None:
        self.assertEqual(GATEWAY_STATUS_COMMAND, "b300-stlink debug gateway-status --json")
        self.assertEqual(GATEWAY_ENSURE_COMMAND, "b300-stlink debug gateway-ensure --json")
        rendered = (GATEWAY_STATUS_COMMAND + " " + GATEWAY_ENSURE_COMMAND).lower()
        self.assertNotIn("sudo", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("0.0.0.0", rendered)

    def test_parser_exposes_gateway_runtime_commands(self) -> None:
        for mode in ("gateway-status", "gateway-ensure", "gateway-rescan"):
            with self.subTest(mode=mode):
                args = parse_args(["debug", mode, "--json"])
                self.assertEqual(args.debug_mode, mode)

    def test_gateway_status_cli_emits_runtime_snapshot_and_capabilities(self) -> None:
        ready = self._ready()
        manager = mock.Mock()
        manager.status.return_value = ready
        output = io.StringIO()
        with mock.patch.object(b300_stlink, "GatewayProcessManager", return_value=manager), \
                redirect_stdout(output):
            result = b300_stlink.main(["debug", "gateway-status", "--json"])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["state"], "READY")
        self.assertIn("gateway-ensure", record["capabilities"])

    def test_gateway_ensure_cli_spawns_explicit_loopback_child(self) -> None:
        manager = mock.Mock()
        manager.ensure.return_value = self._ready()
        output = io.StringIO()
        with mock.patch.object(b300_stlink, "GatewayProcessManager", return_value=manager), \
                redirect_stdout(output):
            result = b300_stlink.main(["debug", "gateway-ensure", "--json"])
        self.assertEqual(result, 0)
        command = tuple(manager.ensure.call_args.args[0])
        self.assertIn("--bind-address", command)
        self.assertEqual(command[command.index("--bind-address") + 1], "127.0.0.1")
        self.assertNotIn("sudo", tuple(item.lower() for item in command))

    def test_gateway_rescan_cli_signals_the_managed_owner(self) -> None:
        manager = mock.Mock()
        manager.rescan.return_value = self._ready()
        output = io.StringIO()
        with mock.patch.object(b300_stlink, "GatewayProcessManager", return_value=manager), \
                redirect_stdout(output):
            result = b300_stlink.main(["debug", "gateway-rescan", "--json"])
        self.assertEqual(result, 0)
        self.assertTrue(manager.rescan.called)
        self.assertFalse(manager.ensure.called)

    def test_managed_gateway_child_publishes_ready_only_after_targets_evidence(self) -> None:
        store = mock.Mock()
        ready = self._ready()
        ready = type(ready).from_record({
            **ready.to_record(), "gdb_endpoint": "127.0.0.1:4333",
            "tcl_endpoint": "127.0.0.1:7666",
        })

        class Supervisor:
            def __init__(self, **kwargs):
                self.sink = kwargs["snapshot_sink"]
                self.calls = 0

            def maintain_once(self):
                self.calls += 1
                if self.calls == 1:
                    self.sink(ready)
                    return ready
                raise KeyboardInterrupt

            def stop(self):
                pass

        output = io.StringIO()
        with mock.patch.object(b300_stlink, "GatewayStatusStore", return_value=store), \
                mock.patch.object(b300_stlink, "GatewaySupervisor", Supervisor), \
                mock.patch.object(b300_stlink.time, "sleep"), redirect_stdout(output):
            result = b300_stlink.main([
                "debug", "gateway", "--managed-child", "--gdb-port", "4333",
                "--tcl-port", "7666", "--json",
            ])
        self.assertEqual(result, 0)
        states = [call.args[0].state for call in store.write.call_args_list]
        self.assertIn("READY", states)
        ready = next(call.args[0] for call in store.write.call_args_list if call.args[0].state == "READY")
        self.assertEqual(ready.gdb_endpoint, "127.0.0.1:4333")
        self.assertEqual(ready.tcl_endpoint, "127.0.0.1:7666")

    def test_managed_gateway_child_publishes_waiting_probe_before_exiting(self) -> None:
        store = mock.Mock()
        waiting = GatewaySnapshot.from_record({
            "schema_version": 1, "instance_id": "runtime", "generation": 0,
            "sequence": 1, "state": "WAITING_PROBE", "reason_code": "NO_PROBE",
            "selected_probe": None, "gdb_endpoint": None, "tcl_endpoint": None,
            "cpu_state": "unknown", "evidence_age_ms": None,
        })

        class Supervisor:
            def __init__(self, **kwargs):
                self.sink = kwargs["snapshot_sink"]
                self.calls = 0

            def maintain_once(self):
                self.calls += 1
                if self.calls == 1:
                    self.sink(waiting)
                    return waiting
                raise KeyboardInterrupt

            def stop(self):
                pass

        output = io.StringIO()
        with mock.patch.object(b300_stlink, "GatewayStatusStore", return_value=store), \
                mock.patch.object(b300_stlink, "GatewaySupervisor", Supervisor), \
                mock.patch.object(b300_stlink, "DebugService") as service, \
                mock.patch.object(b300_stlink.time, "sleep"), redirect_stdout(output):
            result = b300_stlink.main(["debug", "gateway", "--managed-child", "--json"])
        self.assertEqual(result, 0)
        self.assertFalse(service.called)
        self.assertEqual(store.write.call_args.args[0].state, "WAITING_PROBE")
        self.assertEqual(store.write.call_args.args[0].reason_code, "NO_PROBE")

    @staticmethod
    def _ready():
        from b300_core.gateway_status import GatewaySnapshot
        return GatewaySnapshot.from_record({
            "schema_version": 1, "instance_id": "runtime", "generation": 1,
            "sequence": 2, "state": "READY", "reason_code": "TARGET_VERIFIED",
            "selected_probe": {"serial": "SAFE123", "usb_identity": "usb:1"},
            "gdb_endpoint": "127.0.0.1:3333", "tcl_endpoint": "127.0.0.1:6666",
            "cpu_state": "running", "evidence_age_ms": 0,
        })


if __name__ == "__main__":
    unittest.main()
