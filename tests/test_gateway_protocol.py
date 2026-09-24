from __future__ import annotations

import io
import json
import stat
import unittest
from contextlib import redirect_stdout
from unittest import mock
from types import SimpleNamespace

import b300_stlink
from b300_cli.parser import parse_args
from b300_core.gateway_protocol import (
    GATEWAY_AGENT_ENSURE_COMMAND, GATEWAY_AGENT_STATUS_COMMAND,
    GATEWAY_ENSURE_COMMAND, GATEWAY_STATUS_COMMAND, gateway_capabilities,
)
from b300_core.debug_service import DebugState
from b300_core.gateway_status import GatewaySnapshot
from b300_core.models import ProbeInfo
from b300_core.gateway_agent import GatewayAgentStatus


class GatewayProtocolTests(unittest.TestCase):
    def test_flash_capability_rejects_third_party_state_and_ingress_owners(self):
        from b300_core.gateway_system_mode import IsolatedGatewayConfig
        from pathlib import Path
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000,
                                       operator_gid=2002, flash_enabled=True)
        jobs = SimpleNamespace(ingress_root=config.ingress_root)
        def info(path, state_uid, ingress_uid):
            if path == config.state_root:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=state_uid)
            if path == config.ingress_root:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o750,
                                       st_uid=ingress_uid, st_gid=2002)
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660,
                                   st_uid=2000, st_gid=2002)
        with mock.patch.object(b300_stlink.os, "getuid", return_value=2000, create=True), \
                mock.patch.object(b300_stlink, "ingress_mount_isolated", return_value=True, create=True), \
                mock.patch.object(b300_stlink.DEFAULT_HARDWARE_OWNER, "path",
                                  config.state_root / "hardware-owner.lock"):
            for state_uid, ingress_uid, expected in ((2000, 2000, True),
                                                     (3000, 2000, False),
                                                     (2000, 3000, False)):
                with self.subTest(state_uid=state_uid, ingress_uid=ingress_uid), \
                        mock.patch.object(Path, "lstat", autospec=True,
                                          side_effect=lambda path: info(path, state_uid, ingress_uid)):
                    self.assertEqual(b300_stlink._isolated_flash_ready(config, jobs, object()),
                                     expected)

    def test_flash_capability_refuses_absent_ingress_mount(self):
        from b300_core.gateway_system_mode import IsolatedGatewayConfig
        from pathlib import Path
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000)
        def info(path):
            if path == config.state_root:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=2000)
            if path == config.ingress_root:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o710, st_uid=2000, st_gid=2002)
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=2000)
        with mock.patch.object(b300_stlink, "ingress_mount_isolated", return_value=False,
                               create=True), \
             mock.patch.object(b300_stlink.os, "getuid", return_value=2000, create=True), \
             mock.patch.object(Path, "lstat", autospec=True, side_effect=info), \
             mock.patch.object(b300_stlink.DEFAULT_HARDWARE_OWNER, "path",
                               config.state_root / "hardware-owner.lock"):
            self.assertFalse(b300_stlink._isolated_flash_ready(
                config, SimpleNamespace(ingress_root=config.ingress_root), object()))

    def test_pending_replug_marker_withholds_flash_capability(self):
        from b300_core.gateway_system_mode import IsolatedGatewayConfig
        from pathlib import Path
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000,
                                       operator_gid=2002, flash_enabled=False)
        def info(path):
            if path == config.state_root:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=2000)
            if path == config.ingress_root:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o710,
                                       st_uid=2000, st_gid=2003)
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660,
                                   st_uid=2000, st_gid=2002)
        with mock.patch.object(b300_stlink.os, "getuid", return_value=2000, create=True), \
             mock.patch.object(b300_stlink, "ingress_mount_isolated", return_value=True), \
             mock.patch.object(Path, "lstat", autospec=True, side_effect=info), \
             mock.patch.object(b300_stlink.DEFAULT_HARDWARE_OWNER, "path",
                               config.state_root / "hardware-owner.lock"):
            self.assertFalse(b300_stlink._isolated_flash_ready(
                config, SimpleNamespace(ingress_root=config.ingress_root), object()))

    def test_live_agent_status_reports_its_own_isolated_capability(self):
        from b300_core.gateway_agent import GatewayAgent
        from b300_core.gateway_agent_protocol import GatewayRequest
        coordinator = mock.Mock()
        coordinator.public_snapshot.return_value.to_record.return_value = {"state": "IDLE"}
        agent = GatewayAgent(coordinator, request_store=mock.Mock(),
                             capabilities=("gateway-exclusive-lease-v1",
                                           "remote_application_flash_isolated_v1"))
        response = agent._dispatch(GatewayRequest.create("status", {}))
        self.assertIn("remote_application_flash_isolated_v1",
                      response["result"]["capabilities"])

    def test_isolated_status_does_not_invent_live_flash_capability(self):
        from b300_core.gateway_system_mode import IsolatedGatewayConfig
        from pathlib import Path
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000)
        response = {"protocol_version": 1, "status": "ok", "reason_code": "OK",
                    "result": {"state": "IDLE"},
                    "capabilities": ["gateway-exclusive-lease-v1"]}
        output = io.StringIO()
        with mock.patch.object(b300_stlink.sys, "platform", "linux"), \
                mock.patch.object(b300_stlink, "load_isolated_gateway_config", return_value=config), \
                mock.patch.object(b300_stlink, "_isolated_gateway_submit", return_value=response), \
                redirect_stdout(output):
            code = b300_stlink.main(["debug", "gateway-agent-ensure", "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn("remote_application_flash_isolated_v1",
                         json.loads(output.getvalue())["capabilities"])

    def test_isolated_runtime_commands_return_gateway_snapshots_for_debug_clients(self):
        from b300_core.gateway_agent import GatewayAgent
        from b300_core.gateway_system_mode import IsolatedGatewayConfig
        from pathlib import Path
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000)
        ready = self._ready()
        coordinator = mock.Mock()
        coordinator.runtime_snapshot.return_value = ready
        agent = GatewayAgent(coordinator, request_store=mock.Mock())
        for mode, operation in (("gateway-status", "runtime_status"),
                                ("gateway-ensure", "runtime_ensure"),
                                ("gateway-rescan", "runtime_rescan")):
            with self.subTest(mode=mode):
                output = io.StringIO()
                def submit(_config, request, _timeout):
                    self.assertEqual(request.operation, operation)
                    return agent._dispatch(request)
                with mock.patch.object(b300_stlink.sys, "platform", "linux"), \
                        mock.patch.object(b300_stlink, "load_isolated_gateway_config", return_value=config), \
                        mock.patch.object(b300_stlink, "_isolated_gateway_submit", side_effect=submit), \
                        redirect_stdout(output):
                    self.assertEqual(b300_stlink.main(["debug", mode, "--json"]), 0)
                snapshot = GatewaySnapshot.from_record(json.loads(output.getvalue()))
                self.assertTrue(snapshot.attach_ready)
                self.assertEqual(snapshot.gdb_endpoint, "127.0.0.1:3333")


    def test_missing_isolated_socket_does_not_launch_user_gateway(self):
        from b300_core.gateway_system_mode import IsolatedGatewayConfig
        from pathlib import Path
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000)
        output = io.StringIO()
        with mock.patch.object(b300_stlink.sys, "platform", "linux"), \
                mock.patch.object(b300_stlink, "load_isolated_gateway_config", return_value=config), \
                mock.patch.object(b300_stlink, "GatewayUnixClient") as client, \
                mock.patch.object(b300_stlink, "GatewayAgentProcessManager") as manager, \
                redirect_stdout(output):
            client.return_value.submit_request.side_effect = FileNotFoundError
            code = b300_stlink.main(["debug", "gateway-agent-ensure", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["reason_code"],
                         "GATEWAY_AGENT_NOT_RUNNING")
        manager.assert_not_called()

    def test_direct_gateway_child_is_refused_in_isolated_mode(self):
        from b300_core.gateway_system_mode import IsolatedGatewayConfig
        from pathlib import Path
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000)
        output = io.StringIO()
        with mock.patch.object(b300_stlink.sys, "platform", "linux"), \
                mock.patch.object(b300_stlink, "load_isolated_gateway_config", return_value=config), \
                mock.patch.object(b300_stlink, "_run_managed_gateway_child") as child, \
                redirect_stdout(output):
            self.assertEqual(b300_stlink.main([
                "debug", "gateway", "--managed-child", "--json"]), 1)
        self.assertIn("isolated Gateway Agent", output.getvalue())
        child.assert_not_called()

    def test_new_cli_does_not_advertise_flash_from_old_running_agent(self):
        old = GatewayAgentStatus("old-agent", 42, 10.0, "IDLE", "GATEWAY_IDLE")
        manager = mock.Mock()
        manager.status.return_value = old
        output = io.StringIO()
        with mock.patch.object(b300_stlink, "GatewayAgentProcessManager", return_value=manager), \
                mock.patch.object(b300_stlink, "GatewayLeaseStore") as leases, \
                redirect_stdout(output):
            leases.return_value.read.return_value = None
            code = b300_stlink.main(["debug", "gateway-agent-status", "--json"])
        self.assertEqual(code, 0)
        record = json.loads(output.getvalue())
        self.assertNotIn("remote_application_flash_v1", record["capabilities"])
        self.assertIn("gateway-exclusive-lease-v1", record["capabilities"])

    def test_protocol_advertises_status_and_idempotent_ensure(self) -> None:
        capabilities = gateway_capabilities()
        self.assertEqual(capabilities["protocol_version"], 1)
        self.assertIn("gateway-status", capabilities["capabilities"])
        self.assertIn("gateway-ensure", capabilities["capabilities"])
        self.assertIn("gateway-gdb-activity-v1", capabilities["capabilities"])
        self.assertIn("gateway-exclusive-lease-v1", capabilities["capabilities"])

    def test_remote_commands_are_fixed_per_user_cli_commands(self) -> None:
        self.assertEqual(GATEWAY_STATUS_COMMAND, "b300-stlink debug gateway-status --json")
        self.assertEqual(GATEWAY_ENSURE_COMMAND, "b300-stlink debug gateway-ensure --json")
        rendered = (GATEWAY_STATUS_COMMAND + " " + GATEWAY_ENSURE_COMMAND).lower()
        self.assertNotIn("sudo", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("0.0.0.0", rendered)
        self.assertEqual(
            GATEWAY_AGENT_STATUS_COMMAND,
            "b300-stlink debug gateway-agent-status --json",
        )
        self.assertEqual(
            GATEWAY_AGENT_ENSURE_COMMAND,
            "b300-stlink debug gateway-agent-ensure --json",
        )

    def test_parser_exposes_gateway_runtime_commands(self) -> None:
        for mode in ("gateway-status", "gateway-ensure", "gateway-rescan"):
            with self.subTest(mode=mode):
                args = parse_args(["debug", mode, "--json"])
                self.assertEqual(args.debug_mode, mode)

    def test_parser_exposes_gateway_agent_control_commands(self) -> None:
        for mode in (
            "gateway-agent", "gateway-agent-status", "gateway-agent-ensure",
            "gateway-acquire", "gateway-renew", "gateway-release",
        ):
            with self.subTest(mode=mode):
                args = parse_args(["debug", mode, "--json"])
                self.assertEqual(args.debug_mode, mode)

    def test_gateway_agent_entrypoint_acquires_owner_lock_and_runs_agent(self) -> None:
        args = parse_args(["debug", "gateway-agent", "--managed-child", "--json"])
        store = mock.Mock(start_lock_path=mock.sentinel.owner_lock_path)
        owner_lock = mock.Mock()
        agent = mock.Mock()
        agent.run.return_value = 0

        with mock.patch.object(
                b300_stlink, "GatewayAgentStatusStore", return_value=store,
        ), mock.patch.object(
                b300_stlink, "GatewayAgentOwnerLock", return_value=owner_lock,
        ), mock.patch.object(
                b300_stlink, "GatewaySupervisor",
        ), mock.patch.object(
                b300_stlink, "GatewayLeaseCoordinator",
        ), mock.patch.object(
                b300_stlink, "GatewayAgent", return_value=agent,
        ):
            result = b300_stlink.run_gateway_agent_command(args)

        self.assertEqual(result, 0)
        owner_lock.acquire.assert_called_once_with()
        owner_lock.release.assert_called_once_with()
        agent.run.assert_called_once_with()

    def test_gateway_acquire_accepts_documented_mode_alias(self) -> None:
        args = parse_args([
            "debug", "gateway-acquire", "--mode", "VSCODE_DEBUG",
            "--client-id", "client-1", "--client-label", "ENG-LAPTOP-02", "--json",
        ])
        self.assertEqual(args.lease_mode, "VSCODE_DEBUG")

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
        self.assertIn("gateway-gdb-activity-v1", record["capabilities"])

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
