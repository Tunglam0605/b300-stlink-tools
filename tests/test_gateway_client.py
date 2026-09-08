from __future__ import annotations

import unittest
from itertools import count
from threading import Event, Thread

from b300_core.gateway_client import GatewayClientCoordinator
from b300_core.gateway_status import GatewaySnapshot
from b300_core.remote_session import RemoteForward, RemoteForwardError, RemoteSessionError


def ready(*, instance="gw-a", generation=1, sequence=1, gdb=3333, tcl=6666):
    return GatewaySnapshot.from_record({
        "schema_version": 1, "instance_id": instance, "generation": generation,
        "sequence": sequence, "state": "READY", "reason_code": "TARGET_VERIFIED",
        "selected_probe": {"serial": "SAFE", "usb_identity": "usb:1"},
        "gdb_endpoint": "127.0.0.1:%d" % gdb,
        "tcl_endpoint": "127.0.0.1:%d" % tcl,
        "cpu_state": "running", "evidence_age_ms": 0,
    })


def waiting(reason="NO_PROBE"):
    return GatewaySnapshot.from_record({
        "schema_version": 1, "instance_id": "gw-a", "generation": 1,
        "sequence": 1, "state": "WAITING_PROBE", "reason_code": reason,
        "selected_probe": None, "gdb_endpoint": None, "tcl_endpoint": None,
        "cpu_state": "unknown", "evidence_age_ms": None,
    })


class FakeSession:
    def __init__(self, statuses=(), rescans=(), ensures=(), *, fail_open=0):
        self.statuses = list(statuses); self.rescans = list(rescans); self.ensures = list(ensures)
        self.generation = 9; self.connected = True; self.opened = []; self.closed = []
        self.forward_names = set()
        self._ports = count(42000); self.fail_open = fail_open

    @property
    def state(self):
        return type("State", (), {"generation": self.generation, "authenticated": self.connected,
                                   "forwards": tuple(sorted(self.forward_names))})()

    def gateway_status(self, **_kwargs):
        return self.statuses.pop(0)

    def gateway_rescan(self, **_kwargs):
        return self.rescans.pop(0)

    def ensure_gateway_ready(self, **_kwargs):
        item = self.ensures.pop(0)
        if isinstance(item, Exception): raise item
        return item

    def open_forward(self, name, *, remote_port, local_port=0, remote_host="127.0.0.1", local_host="127.0.0.1"):
        self.opened.append((name, remote_port))
        if self.fail_open:
            self.fail_open -= 1
            raise RemoteForwardError("bind failed")
        self.forward_names.add(name)
        return RemoteForward(name, local_host, next(self._ports), remote_host, remote_port)

    def close_forward(self, name):
        self.closed.append(name)
        self.forward_names.discard(name)


class BlockingSession(FakeSession):
    def __init__(self, *args, block_status=False, block_bind=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.block_status = block_status; self.block_bind = block_bind
        self.entered = Event(); self.release = Event()

    def gateway_status(self, **kwargs):
        item = self.statuses.pop(0)
        if self.block_status:
            self.entered.set(); self.release.wait(2)
        return item

    def open_forward(self, name, **kwargs):
        if self.block_bind and name.endswith("_1_gdb"):
            self.entered.set(); self.release.wait(2)
        return super().open_forward(name, **kwargs)


def coordinator(session):
    return GatewayClientCoordinator(session, "lab", owner_token="test")


class GatewayClientCoordinatorTests(unittest.TestCase):
    def test_ready_binding_reuses_status_without_duplicate_ensure(self):
        session = FakeSession(statuses=[ready(), ready(sequence=2)])
        client = coordinator(session)
        first = client.ensure_ready()
        second = client.health()
        self.assertEqual((first.binding.gdb_endpoint, first.binding.tcl_endpoint),
                         (second.binding.gdb_endpoint, second.binding.tcl_endpoint))
        self.assertEqual(second.binding.sequence, 2)
        self.assertEqual(session.opened, [("gateway_client_test_1_gdb", 3333), ("gateway_client_test_1_tcl", 6666)])
        self.assertEqual(session.ensures, [])

    def test_gateway_restart_or_port_change_invalidates_then_rebinds(self):
        session = FakeSession(statuses=[ready(), ready(instance="gw-b", generation=1, gdb=4333)])
        client = coordinator(session)
        first = client.ensure_ready().binding
        second = client.health().binding
        self.assertNotEqual(first, second)
        self.assertEqual(session.closed, ["gateway_client_test_1_gdb", "gateway_client_test_1_tcl"])
        self.assertEqual(session.opened[-2:], [("gateway_client_test_2_gdb", 4333), ("gateway_client_test_2_tcl", 6666)])

    def test_tcl_only_change_rebinds_both_owned_forwards(self):
        session = FakeSession(statuses=[ready(), ready(sequence=2, tcl=7666)])
        client = coordinator(session)
        client.ensure_ready(); client.health()
        self.assertEqual(session.closed, ["gateway_client_test_1_gdb", "gateway_client_test_1_tcl"])
        self.assertEqual(session.opened[-2:], [("gateway_client_test_2_gdb", 3333), ("gateway_client_test_2_tcl", 7666)])

    def test_probe_replug_recovers_from_rescan_without_reconnecting_ssh(self):
        session = FakeSession(statuses=[ready(), waiting()], rescans=[ready(generation=2, sequence=2)])
        client = coordinator(session)
        client.ensure_ready(); state = client.health()
        self.assertEqual(state.state, "READY")
        self.assertEqual(session.generation, 9)
        self.assertEqual(session.ensures, [])

    def test_tunnel_reopen_uses_fresh_ready_snapshot(self):
        session = FakeSession(statuses=[ready()], rescans=[ready(sequence=2)], fail_open=1)
        client = coordinator(session)
        state = client.ensure_ready()
        self.assertEqual(state.state, "READY")
        self.assertEqual(len(session.opened), 3)

    def test_lost_owned_tunnel_reopens_from_the_next_fresh_ready_snapshot(self):
        session = FakeSession(statuses=[ready(), ready(sequence=2)])
        client = coordinator(session)
        client.ensure_ready()
        session.forward_names.remove("gateway_client_test_1_tcl")
        state = client.health()
        self.assertEqual(state.state, "READY")
        self.assertEqual(len(session.opened), 4)
        self.assertEqual(session.closed, ["gateway_client_test_1_gdb", "gateway_client_test_1_tcl"])
        self.assertEqual(session.opened[-2:], [("gateway_client_test_2_gdb", 3333), ("gateway_client_test_2_tcl", 6666)])

    def test_recovery_exhaustion_becomes_stale_and_never_closes_foreign_forward(self):
        session = FakeSession(statuses=[ready(), waiting()], rescans=[waiting(), waiting(), waiting()], ensures=[waiting(), waiting(), waiting()])
        client = coordinator(session)
        client.ensure_ready(); state = client.health()
        self.assertEqual((state.state, state.reason_code), ("STALE", "GATEWAY_RECOVERY_EXHAUSTED"))
        self.assertNotIn("foreign", session.closed)
        self.assertEqual(session.generation, 9)

    def test_nonretriable_cli_error_stales_without_retry(self):
        error = RemoteSessionError("old", reason_code="CLI_TOO_OLD", phase="gateway_cli", next_action="update", retriable=False)
        session = FakeSession(statuses=[ready(), waiting()], rescans=[waiting()], ensures=[error])
        client = coordinator(session)
        client.ensure_ready(); state = client.health()
        self.assertEqual((state.state, state.reason_code), ("STALE", "CLI_TOO_OLD"))

    def test_multiple_probes_stales_without_recovery_attempt(self):
        session = FakeSession(statuses=[ready(), waiting("MULTIPLE_PROBES")])
        client = coordinator(session)
        client.ensure_ready(); state = client.health()
        self.assertEqual((state.state, state.reason_code), ("STALE", "MULTIPLE_PROBES"))
        self.assertEqual(session.rescans, [])

    def test_old_snapshot_and_old_binding_are_rejected(self):
        session = FakeSession(statuses=[ready(generation=2, sequence=2), ready(generation=1, sequence=99)])
        client = coordinator(session)
        binding = client.ensure_ready().binding
        state = client.health()
        self.assertEqual(state.binding, binding)
        self.assertFalse(client.accept_binding(binding.__class__(
            "lab", 9, "gw-a", 1, 99, binding.gdb_endpoint, binding.tcl_endpoint
        )))

    def test_two_coordinators_keep_each_others_forwards_intact(self):
        session = FakeSession(
            statuses=[ready(), ready(sequence=2), waiting()], rescans=[ready(generation=2, sequence=3)],
        )
        first = GatewayClientCoordinator(session, "lab", owner_token="first")
        second = GatewayClientCoordinator(session, "lab", owner_token="second")
        first.ensure_ready(); second.ensure_ready()
        second_binding = second.binding

        first.health()

        self.assertEqual(second.state.state, "READY")
        self.assertEqual(second.binding, second_binding)
        self.assertIn("gateway_client_second_1_gdb", session.forward_names)
        self.assertIn("gateway_client_second_1_tcl", session.forward_names)
        self.assertNotIn("gateway_client_second_1_gdb", session.closed)
        self.assertNotIn("gateway_client_second_1_tcl", session.closed)

    def test_close_releases_only_its_owned_forwards(self):
        session = FakeSession(statuses=[ready()])
        client = coordinator(session)
        client.ensure_ready()
        session.forward_names.add("vscode_foreign_gdb")
        client.close()
        self.assertEqual(session.closed, ["gateway_client_test_1_gdb", "gateway_client_test_1_tcl"])
        self.assertIn("vscode_foreign_gdb", session.forward_names)

    def test_owner_token_is_safe_bounded_and_unique_per_session(self):
        session = FakeSession()
        first = GatewayClientCoordinator(session, "lab", owner_token="safe-token_1")
        self.assertLessEqual(len("gateway_client_safe-token_1_999_gdb"), 64)
        with self.assertRaises(ValueError):
            GatewayClientCoordinator(session, "lab", owner_token="safe-token_1")
        with self.assertRaises(ValueError):
            GatewayClientCoordinator(FakeSession(), "lab", owner_token="bad;token")

    def test_old_blocked_status_cannot_overwrite_newer_gui_epoch(self):
        session = BlockingSession(statuses=[ready(sequence=1), ready(sequence=3)], block_status=True)
        client = coordinator(session)
        worker = Thread(target=client.ensure_ready); worker.start()
        self.assertTrue(session.entered.wait(1))
        client.accept_health_snapshot(ready(sequence=2))
        session.block_status = False
        client.ensure_ready()
        session.release.set(); worker.join(2)
        self.assertEqual(client.binding.sequence, 3)

    def test_obsolete_bind_closes_only_its_staged_forward_names(self):
        session = BlockingSession(statuses=[ready(sequence=1), ready(sequence=3)], block_bind=True)
        client = coordinator(session)
        worker = Thread(target=client.ensure_ready); worker.start()
        self.assertTrue(session.entered.wait(1))
        client.accept_health_snapshot(ready(sequence=2))
        session.block_bind = False
        client.ensure_ready()
        session.release.set(); worker.join(2)
        self.assertEqual(client.binding.sequence, 3)
        self.assertIn("gateway_client_test_2_gdb", session.forward_names)
        self.assertIn("gateway_client_test_2_tcl", session.forward_names)
        self.assertIn("gateway_client_test_1_gdb", session.closed)
        self.assertNotIn("gateway_client_test_2_gdb", session.closed)

    def test_close_during_bind_prevents_late_commit(self):
        session = BlockingSession(statuses=[ready()], block_bind=True)
        client = coordinator(session)
        worker = Thread(target=client.ensure_ready); worker.start()
        self.assertTrue(session.entered.wait(1))
        client.close(); session.release.set(); worker.join(2)
        self.assertIsNone(client.binding)
        self.assertEqual(session.forward_names, set())

    def test_reordered_snapshot_does_not_cancel_recovery(self):
        session = FakeSession(statuses=[waiting()], rescans=[ready(generation=2, sequence=2)], ensures=[ready(generation=4, sequence=4)])
        client = coordinator(session)
        client.accept_health_snapshot(ready(generation=3, sequence=3))
        state = client.ensure_ready()
        self.assertEqual(state.state, "READY")

    def test_tcl_stage_failure_keeps_committed_and_foreign_routes(self):
        class TclFailureSession(FakeSession):
            def open_forward(self, name, **kwargs):
                if name.endswith("_2_tcl"):
                    raise RemoteForwardError("tcl failed")
                return super().open_forward(name, **kwargs)
        session = TclFailureSession(statuses=[ready(), ready(sequence=2, tcl=7666)])
        client = coordinator(session); first = client.ensure_ready().binding
        session.forward_names.add("foreign")
        state = client.health()
        self.assertEqual(state.binding, first)
        self.assertIn("gateway_client_test_1_gdb", session.forward_names)
        self.assertIn("gateway_client_test_1_tcl", session.forward_names)
        self.assertIn("foreign", session.forward_names)

    def test_session_state_is_never_read_while_coordinator_lock_is_held(self):
        class ReentrantSession(FakeSession):
            coordinator_ref = None
            @property
            def state(self):
                lock = self.coordinator_ref._lock
                self.assertFalse(getattr(lock, "_is_owned")())
                return super().state
            def assertFalse(self, value):
                if value: raise AssertionError("coordinator lock held during session access")
        session = ReentrantSession(statuses=[ready()])
        client = coordinator(session); session.coordinator_ref = client
        self.assertEqual(client.ensure_ready().state, "READY")


if __name__ == "__main__":
    unittest.main()
