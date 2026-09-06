from __future__ import annotations

import unittest

from b300_core.gateway_status import GatewaySnapshot, GatewaySnapshotTracker


def ready_snapshot(**changes):
    values = {
        "schema_version": 1,
        "instance_id": "gateway-a",
        "generation": 1,
        "sequence": 1,
        "state": "READY",
        "reason_code": "TARGET_VERIFIED",
        "selected_probe": {"serial": "SAFE123", "usb_identity": "usb:1"},
        "gdb_endpoint": "127.0.0.1:3333",
        "tcl_endpoint": "127.0.0.1:6666",
        "cpu_state": "running",
        "evidence_age_ms": 0,
    }
    values.update(changes)
    return GatewaySnapshot.from_record(values)


class GatewayStatusTests(unittest.TestCase):
    def test_ready_requires_probe_target_evidence_and_loopback_endpoints(self) -> None:
        for changes in (
            {"selected_probe": None},
            {"cpu_state": "unknown"},
            {"evidence_age_ms": 5001},
            {"gdb_endpoint": "0.0.0.0:3333"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ready_snapshot(**changes)

    def test_tracker_rejects_old_events_and_expires_readiness_with_monotonic_time(self) -> None:
        now = [10.0]
        tracker = GatewaySnapshotTracker(clock=lambda: now[0], freshness_timeout_seconds=3.0)
        first = ready_snapshot(sequence=4)
        self.assertTrue(tracker.accept(first))
        self.assertFalse(tracker.accept(ready_snapshot(sequence=3)))
        now[0] = 13.1
        self.assertFalse(tracker.attach_ready)

    def test_new_instance_replaces_higher_sequence_from_previous_process(self) -> None:
        tracker = GatewaySnapshotTracker(clock=lambda: 1.0)
        self.assertTrue(tracker.accept(ready_snapshot(sequence=99)))
        restarted = ready_snapshot(instance_id="gateway-b", generation=0, sequence=1)
        self.assertTrue(tracker.accept(restarted))
        self.assertEqual(tracker.snapshot.instance_id, "gateway-b")


if __name__ == "__main__":
    unittest.main()
