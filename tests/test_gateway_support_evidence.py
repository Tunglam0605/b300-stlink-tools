import json
import unittest

from b300_core.support_bundle import _operational_evidence_record


class GatewaySupportEvidenceTests(unittest.TestCase):
    def test_public_agent_and_lease_evidence_is_bounded_and_redacted(self):
        evidence = _operational_evidence_record({
            "gateway_agent": {
                "state": "READY",
                "reason_code": "IDLE",
                "instance_id": "agent-01",
                "pid": 4210,
                "password": "secret-password",
            },
            "gateway_lease": {
                "active": True,
                "client_label": "ENG-LAPTOP-02",
                "mode": "VSCODE_DEBUG",
                "state": "ACTIVE",
                "reason_code": "GATEWAY_BUSY",
                "acquired_at": "2026-09-08T01:02:03Z",
                "heartbeat_age_seconds": 4,
                "generation": 7,
                "gateway_generation": 12,
                "gateway_instance_id": "agent-01",
                "lease_id": "private-lease-id",
                "token": "raw-token",
                "probe_serial": "STLINK-SECRET",
            },
        })

        self.assertEqual(evidence["gateway_agent"], {
            "state": "READY", "reason_code": "IDLE", "instance_id": "agent-01", "pid": 4210,
        })
        self.assertEqual(evidence["gateway_lease"]["client_label"], "ENG-LAPTOP-02")
        self.assertEqual(evidence["gateway_lease"]["heartbeat_age_seconds"], 4)
        encoded = json.dumps(evidence, sort_keys=True)
        for secret in ("secret-password", "private-lease-id", "raw-token", "STLINK-SECRET"):
            self.assertNotIn(secret, encoded)

    def test_invalid_or_unsafe_gateway_evidence_is_dropped(self):
        evidence = _operational_evidence_record({
            "gateway_agent": {
                "state": "user path",
                "reason_code": "mixed-case",
                "instance_id": "C:\\Users\\Admin\\agent",
                "pid": -1,
            },
            "gateway_lease": {
                "active": "yes",
                "mode": "SSH",
                "state": "active",
                "acquired_at": "not-a-time",
                "heartbeat_age_seconds": 999999,
            },
        })
        self.assertNotIn("gateway_agent", evidence)
        self.assertEqual(evidence["gateway_lease"], {})


if __name__ == "__main__":
    unittest.main()
