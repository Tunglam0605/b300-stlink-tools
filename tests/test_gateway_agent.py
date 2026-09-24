from __future__ import annotations

import tempfile
import threading
import time
import os
import unittest
from unittest import mock
from pathlib import Path

from b300_core.gateway_agent import (
    GatewayAgent, GatewayAgentOwnerLock, GatewayAgentProcessManager, GatewayAgentStatus,
    GatewayAgentStatusStore,
)
from b300_core.gateway_agent_protocol import (
    MAX_REQUEST_BYTES,
    GatewayRequest,
    GatewayRequestStore,
)
from b300_cli.parser import parse_args
import b300_stlink


class FakeCoordinator:
    def __init__(self):
        self.calls = []

    def tick(self):
        self.calls.append(("tick",))
        return type("Snapshot", (), {"active": False, "state": "IDLE", "reason_code": "GATEWAY_IDLE"})()

    def public_snapshot(self):
        self.calls.append(("status",))
        return type("Snapshot", (), {"to_record": lambda self: {"active": False, "state": "IDLE"}})()

    def acquire(self, request):
        self.calls.append(("acquire", request))
        return type("Result", (), {"to_record": lambda self: {"reason_code": "GATEWAY_BUSY"}})()

    def shutdown(self, reason):
        self.calls.append(("shutdown", reason))
        return type("Snapshot", (), {"to_record": lambda self: {"active": False}})()


class GatewayAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GatewayRequestStore(Path(self.temp.name))
        self.coordinator = FakeCoordinator()
        self.agent = GatewayAgent(self.coordinator, request_store=self.store)

    def test_isolated_agent_injects_private_program_jobs(self):
        config = type("Config", (), {
            "state_root": Path(self.temp.name) / "private",
            "ingress_root": Path(self.temp.name) / "ingress",
            "socket_path": Path(self.temp.name) / "agent.sock",
            "operator_uid": -2,
        })()
        args = parse_args(["debug", "gateway-agent", "--json"])
        with mock.patch.object(b300_stlink.sys, "platform", "linux"), \
             mock.patch("b300_stlink.load_isolated_gateway_config", return_value=config), \
             mock.patch("b300_stlink.GatewayAgentOwnerLock") as owner, \
             mock.patch("b300_stlink.GatewaySupervisor"), \
             mock.patch("b300_stlink.GatewayLeaseCoordinator"), \
             mock.patch("b300_stlink.GatewayUnixServer"), \
             mock.patch("b300_stlink.GatewayProgramJobs") as jobs, \
             mock.patch("b300_stlink.GatewayAgent") as agent:
            agent.return_value.run.return_value = 0
            self.assertEqual(b300_stlink._run_gateway_agent(args), 0)
        jobs.assert_called_once()
        self.assertEqual(jobs.call_args.kwargs["root"], config.state_root / "program-jobs")
        self.assertEqual(jobs.call_args.kwargs["ingress_root"], config.ingress_root)
        self.assertIs(agent.call_args.kwargs["program_jobs"], jobs.return_value)
        owner.return_value.release.assert_called_once()

    def test_isolated_agent_releases_owner_lock_if_ingress_is_unsafe(self):
        config = type("Config", (), {
            "state_root": Path(self.temp.name) / "private",
            "ingress_root": Path(self.temp.name) / "ingress",
            "socket_path": Path(self.temp.name) / "agent.sock",
            "operator_uid": -2,
        })()
        args = parse_args(["debug", "gateway-agent", "--json"])
        with mock.patch.object(b300_stlink.sys, "platform", "linux"), \
             mock.patch("b300_stlink.load_isolated_gateway_config", return_value=config), \
             mock.patch("b300_stlink.GatewayAgentOwnerLock") as owner, \
             mock.patch("b300_stlink.GatewaySupervisor"), \
             mock.patch("b300_stlink.GatewayLeaseCoordinator"), \
             mock.patch("b300_stlink.GatewayUnixServer"), \
             mock.patch("b300_stlink.GatewayProgramJobs", side_effect=ValueError("unsafe ingress")):
            with self.assertRaises(ValueError):
                b300_stlink._run_gateway_agent(args)
        owner.return_value.release.assert_called_once()

    def test_agent_idle_only_ticks_coordinator(self):
        result = self.agent.run_once()
        self.assertEqual((result.state, result.reason_code), ("IDLE", "GATEWAY_IDLE"))
        self.assertEqual(self.coordinator.calls, [("tick",)])

    def test_isolated_status_uses_socket_without_per_user_spawn(self):
        args = parse_args(["debug", "gateway-agent-status", "--json"])
        with mock.patch.object(b300_stlink.sys, "platform", "linux"), \
                mock.patch("b300_stlink.load_isolated_gateway_config") as marker, \
                mock.patch("b300_stlink.GatewayUnixClient") as client, \
                mock.patch("b300_stlink.GatewayAgentProcessManager") as manager, \
                mock.patch("b300_stlink.emit_snapshot") as emit:
            marker.return_value.socket_path = Path("/run/b300-stlink/agent.sock")
            client.return_value.submit_request.return_value = {
                "protocol_version": 1, "request_id": "req", "status": "ok",
                "reason_code": "OK", "result": {"state": "IDLE"},
            }
            self.assertEqual(b300_stlink.run_gateway_agent_command(args), 0)
            manager.return_value.ensure_running.assert_not_called()
            self.assertEqual(client.return_value.submit_request.call_args.args[0].operation,
                             "status")
            self.assertEqual(emit.call_args.args[0]["state"], "IDLE")

    def test_isolated_missing_socket_reports_not_running_without_spawn(self):
        args = parse_args(["debug", "gateway-agent-ensure", "--json"])
        with mock.patch.object(b300_stlink.sys, "platform", "linux"), \
                mock.patch("b300_stlink.load_isolated_gateway_config") as marker, \
                mock.patch("b300_stlink.GatewayUnixClient") as client, \
                mock.patch("b300_stlink.GatewayAgentProcessManager") as manager, \
                mock.patch("b300_stlink.emit_snapshot") as emit:
            marker.return_value.socket_path = Path("/run/b300-stlink/agent.sock")
            client.return_value.submit_request.side_effect = FileNotFoundError()
            self.assertEqual(b300_stlink.run_gateway_agent_command(args), 1)
            manager.return_value.ensure_running.assert_not_called()
            self.assertEqual(emit.call_args.args[0]["reason_code"],
                             "GATEWAY_AGENT_NOT_RUNNING")

    def test_valid_status_request_is_processed_once(self):
        request = GatewayRequest.create("status", {}, request_id="req-1", timeout_seconds=5)
        self.store.enqueue(request)
        self.agent.run_once()
        response = self.store.read_response("req-1")
        self.assertEqual(response["status"], "ok")
        self.assertEqual([call[0] for call in self.coordinator.calls], ["status", "tick"])
        self.agent.run_once()
        self.assertEqual([call[0] for call in self.coordinator.calls], ["status", "tick", "tick"])

    def test_duplicate_request_id_is_rejected_without_dispatch(self):
        request = GatewayRequest.create("status", {}, request_id="req-2", timeout_seconds=5)
        self.store.enqueue(request)
        self.agent.run_once()
        replay = self.store.submit_request(request)
        self.assertEqual(replay["reason_code"], "REQUEST_REPLAYED")
        self.assertEqual([call[0] for call in self.coordinator.calls].count("status"), 1)

    def test_acknowledge_response_removes_artifact_and_keeps_replay_guard(self):
        request = GatewayRequest.create("status", {}, request_id="req-ack", timeout_seconds=5)
        self.store.enqueue(request)
        self.agent.run_once()
        self.assertTrue(self.store.response_path("req-ack").exists())
        response = self.store.read_response("req-ack")
        self.assertEqual(response["status"], "ok")
        self.store.acknowledge_response("req-ack")
        self.assertFalse(self.store.response_path("req-ack").exists())
        self.assertTrue(self.store.completed_path("req-ack").exists())
        replay = self.store.submit_request(request)
        self.assertEqual(replay["reason_code"], "REQUEST_REPLAYED")

    def test_malformed_response_is_discarded(self):
        request_id = "req-malformed"
        self.store.responses_dir.mkdir(parents=True, exist_ok=True)
        path = self.store.response_path(request_id)
        path.write_text('{"protocol_version": 999}', encoding="utf-8")
        self.assertIsNone(self.store.read_response(request_id))
        self.assertFalse(path.exists())

    def test_expired_completion_tombstone_is_pruned_before_replay_check(self):
        request = GatewayRequest.create("status", {}, request_id="req-expired-done", timeout_seconds=5)
        self.store._prepare()
        marker = self.store.completed_path(request.request_id)
        marker.write_text("completed\n", encoding="ascii")
        old = time.time() - 2 * 3600
        os.utime(str(marker), (old, old))
        result = self.store.submit_request(request, timeout_seconds=0.01)
        self.assertEqual(result["reason_code"], "AGENT_RESPONSE_TIMEOUT")
        self.assertFalse(marker.exists())

    def test_oversized_request_never_reaches_coordinator(self):
        path = self.store.requests_dir / "req-oversized.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{" + b"x" * MAX_REQUEST_BYTES)
        self.agent.run_once()
        self.assertEqual([call[0] for call in self.coordinator.calls], ["tick"])
        self.assertFalse(path.exists())

    def test_expired_request_is_rejected_without_dispatch(self):
        request = GatewayRequest.create(
            "status", {}, request_id="req-expired", timeout_seconds=1, now_mono=10,
        )
        self.store.enqueue(request)
        agent = GatewayAgent(self.coordinator, request_store=self.store, clock=lambda: 12)
        agent.run_once()
        response = self.store.read_response("req-expired")
        self.assertEqual(response["reason_code"], "REQUEST_EXPIRED")
        self.assertNotIn("status", [call[0] for call in self.coordinator.calls])

    def test_process_manager_reuses_live_agent_without_spawning(self):
        status_store = GatewayAgentStatusStore(Path(self.temp.name) / "status.json")
        status_store.write(GatewayAgentStatus("agent-1", 42, 10.0, "IDLE", "GATEWAY_IDLE"))
        spawns = []
        manager = GatewayAgentProcessManager(
            store=status_store,
            process_alive=lambda pid: pid == 42,
            process_factory=lambda command: spawns.append(tuple(command)),
            clock=lambda: 11.0,
        )
        status = manager.ensure_running(("b300-stlink", "debug", "gateway-agent"))
        self.assertEqual(status.pid, 42)
        self.assertEqual(spawns, [])

    def test_windows_process_probe_error_keeps_owner_status_fail_closed(self):
        status_store = GatewayAgentStatusStore(Path(self.temp.name) / "status.json")
        status_store.write(GatewayAgentStatus("agent-1", 42, 10.0, "IDLE", "GATEWAY_IDLE"))
        manager = GatewayAgentProcessManager(store=status_store, clock=lambda: 11.0)

        with mock.patch(
            "b300_core.gateway_agent.os.kill",
            side_effect=SystemError("os.kill probe failed"),
        ):
            status = manager.status()

        self.assertIsNotNone(status)
        self.assertEqual(status.pid, 42)

    def test_process_manager_rejects_unsafe_or_unrelated_command(self):
        manager = GatewayAgentProcessManager(
            store=GatewayAgentStatusStore(Path(self.temp.name) / "status.json"),
            process_alive=lambda _pid: False,
            process_factory=lambda _command: None,
        )
        for command in (("sudo", "b300-stlink", "debug", "gateway-agent"),
                        ("python", "other.py")):
            with self.subTest(command=command), self.assertRaises(ValueError):
                manager.ensure_running(command, timeout_seconds=0.1)

    def test_owner_lock_rejects_live_owner_and_reclaims_dead_owner(self):
        path = Path(self.temp.name) / "owner.lock"
        first = GatewayAgentOwnerLock(path, pid=42, process_alive=lambda pid: pid == 42)
        first.acquire()
        with self.assertRaisesRegex(RuntimeError, "ALREADY_RUNNING"):
            GatewayAgentOwnerLock(path, pid=43, process_alive=lambda pid: pid == 42).acquire()
        reclaimed = GatewayAgentOwnerLock(path, pid=43, process_alive=lambda _pid: False)
        reclaimed.acquire()
        self.assertEqual(path.read_text(encoding="ascii"), "43\n")
        reclaimed.release()
        self.assertFalse(path.exists())

    def test_owner_lock_treats_same_process_pid_as_live(self):
        path = Path(self.temp.name) / "same-process.lock"
        first = GatewayAgentOwnerLock(path, pid=os.getpid(), process_alive=lambda _pid: False)
        first.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "ALREADY_RUNNING"):
                GatewayAgentOwnerLock(
                    path, pid=os.getpid(), process_alive=lambda _pid: False,
                ).acquire()
        finally:
            first.release()

    def test_corrupt_owner_lock_fails_closed(self):
        path = Path(self.temp.name) / "owner.lock"
        path.write_text("not-a-pid", encoding="ascii")
        with self.assertRaisesRegex(RuntimeError, "LOCK_CORRUPT"):
            GatewayAgentOwnerLock(path, pid=43, process_alive=lambda _pid: False).acquire()

    def test_concurrent_process_ensure_spawns_only_once(self):
        status_store = GatewayAgentStatusStore(Path(self.temp.name) / "status.json")
        status_store.start_lock_path = Path(self.temp.name) / "starting.lock"
        state = {"alive": False, "spawns": 0}
        barrier = threading.Barrier(3)

        def spawn(_command):
            state["spawns"] += 1
            state["alive"] = True
            status_store.write(GatewayAgentStatus("agent-1", 42, time.monotonic(), "IDLE", "GATEWAY_IDLE"))
            return object()

        managers = [GatewayAgentProcessManager(
            store=status_store,
            process_alive=lambda pid: pid == 42 and state["alive"],
            process_factory=spawn,
        ) for _ in range(2)]
        results = []

        def ensure(manager):
            barrier.wait()
            results.append(manager.ensure_running(("b300-stlink", "debug", "gateway-agent")))

        threads = [threading.Thread(target=ensure, args=(manager,)) for manager in managers]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        self.assertEqual((state["spawns"], len(results)), (1, 2))


if __name__ == "__main__":
    unittest.main()
