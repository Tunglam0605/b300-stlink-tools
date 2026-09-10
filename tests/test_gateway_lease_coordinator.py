from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from b300_core.gateway_lease import (
    GatewayLeaseBusy,
    GatewayLeaseGrant,
    GatewayLeasePolicy,
    GatewayLeaseRequest,
    GatewayLeaseStore,
)
from b300_core.gateway_lease_coordinator import GatewayLeaseCoordinator
from b300_core.gateway_status import GatewaySnapshot
from b300_core.gateway_supervisor import GatewaySupervisor
from b300_core.debug_service import DebugState
from b300_core.models import ProbeInfo


def snapshot(state="READY", reason="TARGET_VERIFIED", generation=1):
    ready = state == "READY"
    return GatewaySnapshot.from_record({
        "schema_version": 1,
        "instance_id": "gateway-1",
        "generation": generation,
        "sequence": generation,
        "state": state,
        "reason_code": reason,
        "selected_probe": {
            "serial": "SAFE123", "usb_identity": "usb:1", "source": "test",
        } if ready else None,
        "gdb_endpoint": "127.0.0.1:3333" if ready else None,
        "tcl_endpoint": "127.0.0.1:6666" if ready else None,
        "cpu_state": "running" if ready else "unknown",
        "evidence_age_ms": 0 if ready else None,
    })


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeSupervisor:
    def __init__(self):
        self.ensure_calls = 0
        self.maintain_calls = 0
        self.stop_calls = 0
        self.ensure_result = snapshot()
        self.maintain_result = self.ensure_result
        self.snapshot = snapshot("STOPPED", "USER_STOPPED", 0)
        self.stop_error = None
        self.stop_blocker = None
        self.recovery_owner = False
        self.reconcile_calls = 0
        self.stop_confirmed = True

    def ensure(self):
        self.ensure_calls += 1
        self.snapshot = self.ensure_result
        return self.snapshot

    def maintain_once(self):
        self.maintain_calls += 1
        self.snapshot = self.maintain_result
        return self.snapshot

    def stop(self):
        self.stop_calls += 1
        if self.stop_blocker is not None:
            self.stop_blocker.wait()
        if self.stop_error is not None:
            raise self.stop_error
        self.snapshot = snapshot("STOPPED", "USER_STOPPED", self.snapshot.generation)
        return self.snapshot

    def reconcile_lease_owner(self, _lease):
        self.reconcile_calls += 1
        return self.recovery_owner

    def confirm_lease_owner_stopped(self, _lease, timeout_seconds):
        return self.stop_confirmed and timeout_seconds > 0


class _OwnedService:
    executable = "/trusted/openocd"

    class Process:
        pid = 4242

    def __init__(self):
        self._state = DebugState.STOPPED
        self._process = self.Process()

    @property
    def state(self): return self._state

    @property
    def process(self): return self._process

    def start(self, _config, event_sink=None): self._state = DebugState.READY
    def stop(self): self._state = DebugState.STOPPED


def request(client_id="client-a", mode="VSCODE_DEBUG"):
    return GatewayLeaseRequest(
        request_id="request-" + client_id,
        client_id=client_id,
        client_label=client_id.upper(),
        mode=mode,
        probe_serial="SAFE123",
    )


class GatewayLeaseCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = FakeClock()
        self.supervisor = FakeSupervisor()
        self.tokens = iter(("token-one", "token-two", "token-three"))
        self.ids = iter(("lease-one", "lease-two", "lease-three"))
        self.coordinator = GatewayLeaseCoordinator(
            self.supervisor,
            store=GatewayLeaseStore(Path(self.temp.name) / "lease.json"),
            policy=GatewayLeasePolicy(
                heartbeat_interval_seconds=1,
                lease_ttl_seconds=5,
                reconnect_grace_seconds=3,
                cleanup_timeout_seconds=2,
            ),
            clock=self.clock,
            token_factory=lambda: next(self.tokens),
            lease_id_factory=lambda: next(self.ids),
            acquired_at_factory=lambda: "2026-09-08T00:00:00Z",
        )

    def test_two_clients_racing_acquire_produce_one_grant_and_one_busy(self):
        results = []
        barrier = threading.Barrier(3)

        def acquire(item):
            barrier.wait()
            results.append(self.coordinator.acquire(item))

        threads = [
            threading.Thread(target=acquire, args=(request("client-a"),)),
            threading.Thread(target=acquire, args=(request("client-b", "LIVE_WATCH"),)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(isinstance(item, GatewayLeaseGrant) for item in results), 1)
        self.assertEqual(sum(isinstance(item, GatewayLeaseBusy) for item in results), 1)
        self.assertEqual(self.supervisor.ensure_calls, 1)

    def test_start_stop_start_uses_new_generation_and_stale_stop_is_harmless(self):
        first = self.coordinator.acquire(request())
        released = self.coordinator.release(first.lease_id, first.token, first.generation)
        second = self.coordinator.acquire(request())
        stale = self.coordinator.release(first.lease_id, first.token, first.generation)

        self.assertFalse(released.active)
        self.assertGreater(second.generation, first.generation)
        self.assertEqual(stale.reason_code, "LEASE_INVALID")
        self.assertTrue(self.coordinator.public_snapshot().active)
        self.assertEqual(self.supervisor.stop_calls, 1)

    def test_heartbeat_expiry_enters_grace_then_cleans_owned_gateway(self):
        grant = self.coordinator.acquire(request())
        self.clock.advance(6)
        grace = self.coordinator.tick()
        self.assertEqual((grace.state, grace.reason_code), ("GRACE", "LEASE_GRACE"))
        self.assertEqual(self.supervisor.stop_calls, 0)

        self.clock.advance(4)
        idle = self.coordinator.tick()
        self.assertFalse(idle.active)
        self.assertEqual(idle.reason_code, "LEASE_EXPIRED")
        self.assertEqual(self.supervisor.stop_calls, 1)
        stale = self.coordinator.renew(grant.lease_id, grant.token, grant.generation)
        self.assertEqual(stale.reason_code, "LEASE_INVALID")

    def test_owner_can_reconnect_during_grace(self):
        grant = self.coordinator.acquire(request())
        self.clock.advance(6)
        self.coordinator.tick()
        renewed = self.coordinator.renew(grant.lease_id, grant.token, grant.generation)
        self.assertEqual((renewed.active, renewed.state), (True, "ACTIVE"))
        self.clock.advance(4)
        self.assertTrue(self.coordinator.tick().active)
        self.assertEqual(self.supervisor.stop_calls, 0)

    def test_late_heartbeat_after_ttl_and_grace_cannot_resurrect_owner(self):
        grant = self.coordinator.acquire(request())
        self.clock.advance(8)
        renewed = self.coordinator.renew(grant.lease_id, grant.token, grant.generation)
        self.assertEqual((renewed.active, renewed.reason_code), (False, "LEASE_INVALID"))
        self.assertEqual(self.supervisor.stop_calls, 1)

    def test_exact_deadlines_are_not_extended_by_a_late_tick(self):
        self.coordinator.acquire(request())
        self.clock.advance(5)
        grace = self.coordinator.tick()
        self.assertEqual(grace.state, "GRACE")
        self.clock.advance(3)
        idle = self.coordinator.tick()
        self.assertEqual((idle.active, idle.reason_code), (False, "LEASE_EXPIRED"))

    def test_invalid_token_never_renews_or_releases_owner(self):
        grant = self.coordinator.acquire(request())
        renewed = self.coordinator.renew(grant.lease_id, "wrong-token", grant.generation)
        released = self.coordinator.release(grant.lease_id, "wrong-token", grant.generation)
        self.assertEqual((renewed.reason_code, released.reason_code),
                         ("LEASE_INVALID", "LEASE_INVALID"))
        self.assertTrue(self.coordinator.public_snapshot().active)
        self.assertEqual(self.supervisor.stop_calls, 0)

    def test_failed_start_cleans_reservation_and_owned_supervisor(self):
        self.supervisor.ensure_result = snapshot("FAILED", "DEBUG_PORT_BUSY")
        result = self.coordinator.acquire(request())
        self.assertFalse(result.active)
        self.assertEqual(result.reason_code, "DEBUG_PORT_BUSY")
        self.assertEqual(self.supervisor.stop_calls, 1)
        self.assertIsNone(self.coordinator.store.read())

    def test_no_probe_without_owner_record_releases_reserved_lease_safely(self):
        owner_path = Path(self.temp.name) / "openocd-owner.json"
        supervisor = GatewaySupervisor(
            probe_discovery=lambda: (),
            owner_record_path=owner_path,
        )
        coordinator = GatewayLeaseCoordinator(
            supervisor,
            store=self.coordinator.store,
            policy=self.coordinator.policy,
            clock=self.clock,
            token_factory=lambda: "token-one",
            lease_id_factory=lambda: "lease-one",
            acquired_at_factory=lambda: "2026-09-08T00:00:00Z",
        )

        result = coordinator.acquire(request())

        self.assertFalse(result.active)
        self.assertEqual((result.state, result.reason_code), ("IDLE", "NO_PROBE"))
        self.assertNotIn(result.reason_code, {"RECOVERY_REQUIRED", "CLEANUP_UNVERIFIED"})
        self.assertIsNone(coordinator.store.read())
        self.assertFalse(owner_path.exists())

    def test_nonready_gateway_with_uncertain_owner_stays_fail_closed(self):
        self.supervisor.ensure_result = snapshot("WAITING_PROBE", "NO_PROBE")
        self.supervisor.stop_confirmed = False

        result = self.coordinator.acquire(request())

        self.assertTrue(result.active)
        self.assertEqual((result.state, result.reason_code),
                         ("RECOVERY_REQUIRED", "CLEANUP_UNVERIFIED"))
        self.assertEqual(self.coordinator.store.read().state, "RECOVERY_REQUIRED")

    def test_cleanup_failure_remains_fail_closed_and_blocks_another_client(self):
        self.coordinator.acquire(request())
        self.supervisor.stop_error = RuntimeError("owned process did not stop")
        self.clock.advance(9)
        failed = self.coordinator.tick()
        self.assertEqual((failed.active, failed.state), (True, "RECOVERY_REQUIRED"))
        busy = self.coordinator.acquire(request("client-b", "LIVE_WATCH"))
        self.assertIsInstance(busy, GatewayLeaseBusy)
        self.assertEqual(self.coordinator.store.read().state, "RECOVERY_REQUIRED")

    def test_release_requires_process_and_endpoint_shutdown_proof(self):
        grant = self.coordinator.acquire(request())
        self.supervisor.stop_confirmed = False

        result = self.coordinator.release(grant.lease_id, grant.token, grant.generation)

        self.assertEqual((result.active, result.state), (True, "RECOVERY_REQUIRED"))
        self.assertEqual(self.coordinator.store.read().state, "RECOVERY_REQUIRED")

    def test_probe_loss_enters_grace_and_reconnect_can_recover_same_owner(self):
        grant = self.coordinator.acquire(request())
        self.supervisor.maintain_result = snapshot("DISCONNECTED", "PROBE_REMOVED")
        lost = self.coordinator.tick()
        self.assertEqual((lost.state, lost.reason_code), ("GRACE", "PROBE_REMOVED"))
        self.coordinator.renew(grant.lease_id, grant.token, grant.generation)
        self.supervisor.maintain_result = snapshot("READY", "TARGET_VERIFIED", 2)
        recovered = self.coordinator.tick()
        self.assertEqual((recovered.state, recovered.gateway_generation), ("ACTIVE", 2))

    def test_openocd_exit_is_restarted_at_most_once_for_same_lease(self):
        self.coordinator.acquire(request())
        self.supervisor.maintain_result = snapshot("FAILED", "OPENOCD_EXITED")
        self.supervisor.ensure_result = snapshot("READY", "TARGET_VERIFIED", 2)
        recovered = self.coordinator.tick()
        self.assertEqual((recovered.active, recovered.state), (True, "ACTIVE"))
        self.assertEqual((self.supervisor.stop_calls, self.supervisor.ensure_calls), (1, 2))

        self.supervisor.maintain_result = snapshot("FAILED", "OPENOCD_EXITED", 2)
        failed = self.coordinator.tick()
        self.assertEqual((failed.state, failed.reason_code), ("GRACE", "OPENOCD_EXITED"))
        self.assertEqual(self.supervisor.ensure_calls, 2)

    def test_idle_tick_does_not_touch_hardware(self):
        result = self.coordinator.tick()
        self.assertFalse(result.active)
        self.assertEqual(self.supervisor.ensure_calls, 0)
        self.assertEqual(self.supervisor.maintain_calls, 0)
        self.assertEqual(self.supervisor.stop_calls, 0)

    def test_restart_tick_cleans_only_a_proven_b300_owned_gateway(self):
        self.coordinator.acquire(request())
        self.supervisor.recovery_owner = True
        restarted = GatewayLeaseCoordinator(
            self.supervisor, store=self.coordinator.store, policy=self.coordinator.policy,
            clock=self.clock,
        )

        recovered = restarted.tick()

        self.assertFalse(recovered.active)
        self.assertEqual(recovered.reason_code, "RECOVERY_RECONCILED")
        self.assertEqual((self.supervisor.reconcile_calls, self.supervisor.stop_calls), (1, 1))
        self.assertIsNone(restarted.store.read())

    def test_real_restart_recovery_clears_proven_lease_and_owner_once(self):
        identity = [{"pid": 4242, "start_identity": "start-a",
                     "executable": "/trusted/openocd", "boot_identity": "boot-a"}]
        owner_path = Path(self.temp.name) / "openocd-owner.json"
        first = GatewaySupervisor(
            service_factory=_OwnedService, probe_discovery=lambda: (
                ProbeInfo("SAFE123", "ST-Link", "test", "usb:1"),),
            target_state_probe=lambda _config: "running", owner_record_path=owner_path,
            process_identity=lambda _pid: identity[0],
            endpoints_closed=lambda _gdb, _tcl: identity[0] is None,
        )
        original = GatewayLeaseCoordinator(
            first, store=self.coordinator.store, policy=self.coordinator.policy,
            clock=self.clock, token_factory=lambda: "token-one",
            lease_id_factory=lambda: "lease-one", acquired_at_factory=lambda: "2026-09-08T00:00:00Z",
        )
        grant = original.acquire(request())
        self.assertTrue(grant.public.active)
        self.assertTrue(owner_path.exists())
        def shutdown(_endpoint): identity[0] = None
        fresh = GatewaySupervisor(
            owner_record_path=owner_path, process_identity=lambda _pid: identity[0],
            endpoint_owner_pid=lambda _endpoint: 4242, shutdown_openocd=shutdown,
            endpoints_closed=lambda _gdb, _tcl: identity[0] is None,
        )
        restarted = GatewayLeaseCoordinator(
            fresh, store=self.coordinator.store, policy=self.coordinator.policy, clock=self.clock,
        )

        result = restarted.tick()

        self.assertEqual((result.active, result.state, result.reason_code),
                         (False, "IDLE", "RECOVERY_RECONCILED"))
        self.assertFalse(owner_path.exists())
        self.assertIsNone(restarted.store.read())

    def test_restart_tick_keeps_unknown_gateway_in_actionable_recovery(self):
        self.coordinator.acquire(request())
        restarted = GatewayLeaseCoordinator(
            self.supervisor, store=self.coordinator.store, policy=self.coordinator.policy,
            clock=self.clock,
        )

        status = restarted.tick()

        self.assertEqual((status.active, status.state, status.reason_code),
                         (True, "RECOVERY_REQUIRED", "RECOVERY_OWNER_UNPROVEN"))
        self.assertEqual((self.supervisor.reconcile_calls, self.supervisor.stop_calls), (1, 0))

    def test_restart_tick_reconciles_persisted_lease_when_owner_never_existed(self):
        self.coordinator.acquire(request())
        owner_path = Path(self.temp.name) / "openocd-owner.json"
        restarted_supervisor = GatewaySupervisor(
            owner_record_path=owner_path,
        )
        restarted = GatewayLeaseCoordinator(
            restarted_supervisor,
            store=self.coordinator.store,
            policy=self.coordinator.policy,
            clock=self.clock,
        )

        status = restarted.tick()

        self.assertEqual((status.active, status.state, status.reason_code),
                         (False, "IDLE", "RECOVERY_RECONCILED"))
        self.assertIsNone(restarted.store.read())

    def test_restart_tick_keeps_malformed_owner_evidence_fail_closed(self):
        self.coordinator.acquire(request())
        owner_path = Path(self.temp.name) / "openocd-owner.json"
        owner_path.write_text("{bad", encoding="utf-8")
        restarted = GatewayLeaseCoordinator(
            GatewaySupervisor(owner_record_path=owner_path),
            store=self.coordinator.store,
            policy=self.coordinator.policy,
            clock=self.clock,
        )

        status = restarted.tick()

        self.assertEqual((status.active, status.state, status.reason_code),
                         (True, "RECOVERY_REQUIRED", "RECOVERY_OWNER_UNPROVEN"))
        self.assertIsNotNone(restarted.store.read())

    def test_restart_tick_keeps_recovery_required_when_proven_cleanup_fails(self):
        self.coordinator.acquire(request())
        self.supervisor.recovery_owner = True
        self.supervisor.stop_error = RuntimeError("owned process did not stop")
        restarted = GatewayLeaseCoordinator(
            self.supervisor, store=self.coordinator.store, policy=self.coordinator.policy,
            clock=self.clock,
        )

        status = restarted.tick()

        self.assertEqual((status.active, status.state), (True, "RECOVERY_REQUIRED"))
        self.assertEqual(status.reason_code, "CLEANUP_IN_PROGRESS")
        self.assertEqual((self.supervisor.reconcile_calls, self.supervisor.stop_calls), (1, 1))

    def test_restart_tick_bounds_proven_cleanup_timeout(self):
        self.coordinator.acquire(request())
        self.supervisor.recovery_owner = True
        release_stop = threading.Event()
        self.supervisor.stop_blocker = release_stop
        self.addCleanup(release_stop.set)
        restarted = GatewayLeaseCoordinator(
            self.supervisor, store=self.coordinator.store,
            policy=GatewayLeasePolicy(
                heartbeat_interval_seconds=1, lease_ttl_seconds=5,
                reconnect_grace_seconds=3, cleanup_timeout_seconds=0.01,
            ),
            clock=self.clock,
        )

        status = restarted.tick()

        self.assertEqual((status.active, status.state, status.reason_code),
                         (True, "RECOVERY_REQUIRED", "CLEANUP_IN_PROGRESS"))
        self.assertEqual((self.supervisor.reconcile_calls, self.supervisor.stop_calls), (1, 1))

    def test_restart_release_with_matching_token_never_cleans_unproven_owner(self):
        grant = self.coordinator.acquire(request())
        restarted = GatewayLeaseCoordinator(
            self.supervisor, store=self.coordinator.store, policy=self.coordinator.policy,
            clock=self.clock,
        )

        status = restarted.release(grant.lease_id, grant.token, grant.generation)

        self.assertEqual((status.active, status.state, status.reason_code),
                         (True, "RECOVERY_REQUIRED", "RECOVERY_OWNER_UNPROVEN"))
        self.assertEqual((self.supervisor.reconcile_calls, self.supervisor.stop_calls), (1, 0))
        self.assertEqual(restarted.store.read().state, "RECOVERY_REQUIRED")

    def test_restart_release_cleans_only_proven_owner(self):
        grant = self.coordinator.acquire(request())
        self.supervisor.recovery_owner = True
        restarted = GatewayLeaseCoordinator(
            self.supervisor, store=self.coordinator.store, policy=self.coordinator.policy,
            clock=self.clock,
        )

        status = restarted.release(grant.lease_id, grant.token, grant.generation)

        self.assertEqual((status.active, status.reason_code), (False, "RECOVERY_RECONCILED"))
        self.assertEqual((self.supervisor.reconcile_calls, self.supervisor.stop_calls), (1, 1))
        self.assertIsNone(restarted.store.read())


if __name__ == "__main__":
    unittest.main()
