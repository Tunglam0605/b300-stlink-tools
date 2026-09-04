from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from b300_core.internal_remote import _internal_ssh_client_factory, create_internal_remote_session
from b300_core.remote_profile import RemoteGatewayProfile


class FakeAutoAddPolicy:
    pass


class FakeSshClient:
    def __init__(self):
        self.policy = None
        self.system_host_keys_loaded = False

    def load_system_host_keys(self):
        self.system_host_keys_loaded = True
        raise AssertionError("Internal operator SSH must not read system known_hosts")

    def set_missing_host_key_policy(self, policy):
        self.policy = policy


class InternalRemoteTests(unittest.TestCase):
    def test_internal_client_uses_ephemeral_auto_accept_without_system_known_hosts(self):
        module = types.SimpleNamespace(SSHClient=FakeSshClient, AutoAddPolicy=FakeAutoAddPolicy)
        with mock.patch.dict(sys.modules, {"paramiko": module}):
            client = _internal_ssh_client_factory()
        self.assertIsInstance(client, FakeSshClient)
        self.assertFalse(client.system_host_keys_loaded)
        self.assertIsInstance(client.policy, FakeAutoAddPolicy)

    def test_factory_builds_remote_session_for_validated_endpoint(self):
        profile = RemoteGatewayProfile("192.168.1.145", "Admin", 22)
        session = create_internal_remote_session(profile, keepalive_seconds=20)
        self.assertEqual(session.endpoint, "Admin@192.168.1.145:22")
        self.assertEqual(session.keepalive_seconds, 20)
        self.assertFalse(session.connected)


if __name__ == "__main__":
    unittest.main()
