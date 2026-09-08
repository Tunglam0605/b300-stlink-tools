from __future__ import annotations

import unittest
from types import SimpleNamespace

from b300_core.gateway_lease_client import GatewayBusyError, GatewayLeaseClient, RemoteLeaseGrant


class FakeSession:
    def __init__(self):
        self.calls = []
        self.acquire_result = RemoteLeaseGrant(
            lease_id="lease-1", token="secret-token", generation=1,
            public={"active": True, "state": "ACTIVE", "reason_code": "LEASE_ACTIVE"},
        )
        self.renew_result = {"active": True, "state": "ACTIVE", "reason_code": "LEASE_ACTIVE"}
        self.release_result = {"active": False, "state": "IDLE", "reason_code": "CLIENT_RELEASED"}

    def ensure_gateway_agent(self):
        self.calls.append("agent-ensure")

    def acquire_gateway(self, request):
        self.calls.append(("acquire", request))
        if isinstance(self.acquire_result, Exception):
            raise self.acquire_result
        return self.acquire_result

    def renew_gateway(self, grant):
        self.calls.append(("renew", grant))
        return self.renew_result

    def release_gateway(self, grant):
        self.calls.append(("release", grant))
        return self.release_result


class GatewayLeaseClientTests(unittest.TestCase):
    def test_acquire_before_forward_and_release_after_close(self):
        session = FakeSession()
        client = GatewayLeaseClient(session, client_id="client-1", client_label="ENG-LAPTOP")
        grant = client.start("VSCODE_DEBUG", probe_serial="SAFE123")
        self.assertEqual(grant.token, "secret-token")
        self.assertEqual(session.calls[0], "agent-ensure")
        self.assertEqual(session.calls[1][0], "acquire")
        client.close()
        self.assertEqual(session.calls[-1][0], "release")
        client.close()
        self.assertEqual([item[0] if isinstance(item, tuple) else item for item in session.calls].count("release"), 1)

    def test_busy_owner_is_reported_without_opening_forward(self):
        session = FakeSession()
        session.acquire_result = GatewayBusyError(
            "Gateway busy", client_label="ENG-LAPTOP-02", mode="VSCODE_DEBUG",
            heartbeat_age_seconds=3,
        )
        client = GatewayLeaseClient(session, client_id="client-1", client_label="ENG-LAPTOP")
        with self.assertRaisesRegex(GatewayBusyError, "ENG-LAPTOP-02"):
            client.start("LIVE_WATCH")
        self.assertEqual([item[0] if isinstance(item, tuple) else item for item in session.calls], ["agent-ensure", "acquire"])

    def test_renew_failure_invalidates_local_grant_and_close_does_not_release_stale_token(self):
        session = FakeSession()
        session.renew_result = GatewayBusyError("lease lost")
        client = GatewayLeaseClient(session, client_id="client-1", client_label="ENG-LAPTOP")
        client.start("LIVE_WATCH")
        with self.assertRaises(GatewayBusyError):
            client.renew_once()
        client.close()
        self.assertEqual([item[0] if isinstance(item, tuple) else item for item in session.calls], ["agent-ensure", "acquire", "renew"])


if __name__ == "__main__":
    unittest.main()
