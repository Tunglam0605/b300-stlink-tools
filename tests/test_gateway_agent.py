from __future__ import annotations

import tempfile
import threading
import time
import unittest
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

    def test_agent_idle_only_ticks_coordinator(self):
        result = self.agent.run_once()
        self.assertEqual((result.state, result.reason_code), ("IDLE", "GATEWAY_IDLE"))
        self.assertEqual(self.coordinator.calls, [("tick",)])

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
