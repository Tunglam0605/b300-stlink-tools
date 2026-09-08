import unittest
import json


class DeviceStateTests(unittest.TestCase):
    def setUp(self):
        from b300_core.device_state import DeviceStateStore
        self.now = [10.0]
        self.store = DeviceStateStore(clock=lambda: self.now[0])

    def test_rejects_out_of_order_sequence_for_the_same_gateway_binding(self):
        self.assertTrue(self.store.reduce(
            connection_id="lab", gateway_instance_id="gw-1", gateway_generation=2,
            sequence=4, probe_serial="A", gdb_endpoint="127.0.0.1:3333",
            tcl_endpoint="127.0.0.1:6666", target_state="running",
        ))
        self.assertTrue(self.store.reduce(
            sequence=5, gdb_endpoint="127.0.0.1:3333", tcl_endpoint="127.0.0.1:6666",
            target_state="running",
        ))
        self.assertFalse(self.store.reduce(sequence=3, target_state="halted"))
        self.assertEqual(self.store.snapshot.sequence, 5)
        self.assertEqual(self.store.snapshot.target_state, "running")

    def test_new_gateway_generation_revokes_endpoints_and_freshness_before_live(self):
        self.store.reduce(
            connection_id="lab", gateway_instance_id="gw-1", gateway_generation=2,
            sequence=4, probe_serial="A", gdb_endpoint="127.0.0.1:3333",
            tcl_endpoint="127.0.0.1:6666", target_state="running",
        )
        old_epoch = self.store.snapshot.epoch
        self.assertTrue(self.store.reduce(gateway_generation=3, sequence=1))
        current = self.store.snapshot
        self.assertEqual(current.epoch, old_epoch + 1)
        self.assertIsNone(current.gdb_endpoint)
        self.assertIsNone(current.tcl_endpoint)
        self.assertIsNone(current.checked_monotonic)
        self.assertEqual(current.liveness, "STALE")

    def test_probe_and_axf_changes_increment_epoch_and_reject_old_epoch(self):
        self.store.reduce(probe_serial="A", axf_basename="one.axf", axf_fingerprint="1")
        epoch = self.store.snapshot.epoch
        self.assertTrue(self.store.reduce(probe_serial="B"))
        self.assertEqual(self.store.snapshot.epoch, epoch + 1)
        self.assertFalse(self.store.reduce(epoch=epoch, target_state="running"))
        self.assertTrue(self.store.reduce(axf_basename="two.axf", axf_fingerprint="2"))
        self.assertEqual(self.store.snapshot.epoch, epoch + 2)

    def test_only_loopback_endpoints_are_accepted(self):
        with self.assertRaises(ValueError):
            self.store.reduce(gdb_endpoint="10.0.0.2:3333")

    def test_lease_token_is_not_exported(self):
        self.store.reduce(owner_kind="debug", lease_token="private-token")
        self.assertNotIn("lease_token", self.store.snapshot.to_record())

    def test_target_state_rejects_non_support_safe_values(self):
        with self.assertRaises(ValueError):
            self.store.reduce(target_state=object())
        json.dumps(self.store.snapshot.to_record())

    def test_old_lease_token_cannot_release_new_owner(self):
        self.store.reduce(owner_kind="DEBUGGING", lease_token="new")
        self.assertFalse(self.store.reduce(owner_kind=None, lease_token="old"))
        self.assertEqual(self.store.snapshot.owner_kind, "DEBUGGING")

    def test_live_becomes_stale_after_freshness_deadline(self):
        self.store.reduce(target_state="running", checked_monotonic=10.0,
                          gdb_endpoint="127.0.0.1:3333", tcl_endpoint="127.0.0.1:6666")
        self.assertEqual(self.store.snapshot.liveness, "LIVE")
        self.now[0] = 16.0
        self.assertTrue(self.store.expire(5.0, reason="heartbeat expired"))
        self.assertEqual(self.store.snapshot.liveness, "STALE")
        self.assertEqual(self.store.snapshot.reason, "heartbeat expired")
