from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b300_core.gateway_lease import (
    GatewayLease,
    GatewayLeaseGrant,
    GatewayLeasePolicy,
    GatewayLeasePublicSnapshot,
    GatewayLeaseRequest,
    GatewayLeaseStore,
    sanitize_client_label,
    token_digest,
)


def valid_record(**changes):
    record = {
        "schema_version": 1,
        "lease_id": "lease-123",
        "token_digest": token_digest("secret-token"),
        "generation": 4,
        "request_id": "request-123",
        "client_id": "client-a",
        "client_label": "ENG-LAPTOP-02",
        "mode": "VSCODE_DEBUG",
        "state": "ACTIVE",
        "acquired_at": "2026-09-08T09:00:00Z",
        "last_heartbeat_mono": 100.0,
        "deadline_mono": 120.0,
        "grace_deadline_mono": None,
        "gateway_instance_id": "gateway-a",
        "gateway_generation": 2,
        "probe_serial": "STLINK123",
        "reason_code": "LEASE_ACTIVE",
    }
    record.update(changes)
    return record


class GatewayLeaseContractTests(unittest.TestCase):
    def test_policy_defaults_match_gateway_safety_design(self) -> None:
        policy = GatewayLeasePolicy()
        self.assertEqual(policy.heartbeat_interval_seconds, 5.0)
        self.assertEqual(policy.lease_ttl_seconds, 20.0)
        self.assertEqual(policy.reconnect_grace_seconds, 10.0)
        self.assertEqual(policy.cleanup_timeout_seconds, 5.0)

    def test_policy_rejects_non_finite_non_positive_and_short_ttl(self) -> None:
        invalid = (
            {"heartbeat_interval_seconds": 0},
            {"heartbeat_interval_seconds": float("inf")},
            {"lease_ttl_seconds": 5, "heartbeat_interval_seconds": 5},
            {"reconnect_grace_seconds": -1},
            {"cleanup_timeout_seconds": 0},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GatewayLeasePolicy(**changes)

    def test_client_label_is_bounded_and_removes_control_characters(self) -> None:
        self.assertEqual(
            sanitize_client_label("  ENG\nLAPTOP\t01\x00  "),
            "ENG LAPTOP 01",
        )
        self.assertEqual(len(sanitize_client_label("x" * 300)), 64)
        self.assertEqual(sanitize_client_label("\n\t"), "Unknown Client")

    def test_request_requires_safe_ids_mode_label_and_optional_serial(self) -> None:
        request = GatewayLeaseRequest(
            "request-1", "client_a", " ENG\nLaptop ", "live_watch", " STLINK123 ",
        )
        self.assertEqual(request.client_label, "ENG Laptop")
        self.assertEqual(request.mode, "LIVE_WATCH")
        self.assertEqual(request.probe_serial, "STLINK123")
        for changes in (
            {"request_id": "bad/id"},
            {"client_id": "bad client"},
            {"mode": "FLASH"},
            {"probe_serial": "bad;serial"},
        ):
            values = {
                "request_id": "request-1",
                "client_id": "client-a",
                "client_label": "Laptop",
                "mode": "VSCODE_DEBUG",
                "probe_serial": None,
            }
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GatewayLeaseRequest(**values)

    def test_lease_round_trip_is_exact_and_strict(self) -> None:
        lease = GatewayLease.from_record(valid_record())
        self.assertEqual(lease.to_record(), valid_record())
        self.assertEqual(lease.mode, "VSCODE_DEBUG")
        self.assertEqual(lease.state, "ACTIVE")

    def test_lease_rejects_coerced_json_types_and_inconsistent_deadlines(self) -> None:
        invalid = (
            {"schema_version": True},
            {"generation": True},
            {"generation": -1},
            {"gateway_generation": "2"},
            {"mode": "FLASH"},
            {"state": "IDLE"},
            {"deadline_mono": float("inf")},
            {"last_heartbeat_mono": 121.0},
            {"state": "GRACE", "grace_deadline_mono": None},
            {"state": "ACTIVE", "grace_deadline_mono": 130.0},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GatewayLease.from_record(valid_record(**changes))

    def test_public_snapshot_and_grant_records_never_expose_token_material(self) -> None:
        lease = GatewayLease.from_record(valid_record())
        public = GatewayLeasePublicSnapshot.from_lease(lease, now_mono=103.4)
        record = public.to_record()
        self.assertEqual(record["heartbeat_age_seconds"], 3)
        self.assertEqual(record["client_label"], "ENG-LAPTOP-02")
        self.assertNotIn("token", json.dumps(record).lower())

        grant = GatewayLeaseGrant.from_lease(lease, "secret-token", now_mono=103.4)
        self.assertEqual(grant.token, "secret-token")
        self.assertNotIn("secret-token", json.dumps(grant.public.to_record()))

    def test_public_snapshot_rejects_untrusted_types_values_and_extra_fields(self) -> None:
        invalid = (
            {"lease_id": "bad/id"},
            {"generation": True},
            {"generation": -1},
            {"generation": 2 ** 31},
            {"client_label": " C:\\secrets\\token "},
            {"mode": "FLASH"},
            {"state": "IDLE"},
            {"acquired_at": "2026-09-08T09:00:00Z\nsecret"},
            {"heartbeat_age_seconds": -1},
            {"heartbeat_age_seconds": 86401},
            {"heartbeat_age_seconds": True},
            {"gateway_instance_id": "gateway/one"},
            {"gateway_generation": "2"},
            {"gateway_generation": 2 ** 31},
            {"probe_serial": "serial;secret"},
            {"reason_code": "bad reason"},
            {"_extra": "ignored"},
        )
        base = GatewayLeasePublicSnapshot.from_lease(
            GatewayLease.from_record(valid_record()), now_mono=103.4,
        ).to_record()
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                candidate = dict(base)
                candidate.update(changes)
                GatewayLeasePublicSnapshot.from_record(candidate)

    def test_public_snapshot_preserves_safe_values_and_normalizes_mode(self) -> None:
        base = GatewayLeasePublicSnapshot.from_lease(
            GatewayLease.from_record(valid_record()), now_mono=103.4,
        ).to_record()
        base["mode"] = "live_watch"
        snapshot = GatewayLeasePublicSnapshot.from_record(base)
        self.assertEqual(snapshot.mode, "LIVE_WATCH")
        self.assertEqual(snapshot.heartbeat_age_seconds, 3)

    def test_store_round_trip_never_persists_raw_token_and_clear_is_generation_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lease.json"
            store = GatewayLeaseStore(path)
            lease = GatewayLease.from_record(valid_record())
            store.write(lease)

            self.assertEqual(store.read(), lease)
            self.assertNotIn("secret-token", path.read_text(encoding="utf-8"))
            self.assertFalse(store.clear_if_generation(lease.generation - 1))
            self.assertTrue(path.exists())
            self.assertTrue(store.clear_if_generation(lease.generation))
            self.assertFalse(path.exists())
            self.assertIsNone(store.read())

    def test_corrupt_store_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lease.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unreadable/corrupt"):
                GatewayLeaseStore(path).read()

    def test_token_digest_is_deterministic_but_rejects_invalid_token(self) -> None:
        digest = token_digest("secret-token")
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, token_digest("secret-token"))
        self.assertNotEqual(digest, token_digest("different-token"))
        for token in ("", "x" * 257, "token\nvalue"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                token_digest(token)


if __name__ == "__main__":
    unittest.main()
