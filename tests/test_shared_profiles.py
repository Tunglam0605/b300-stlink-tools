from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b300_core.gateway_profiles import GatewayProfile, GatewayProfileStore
from b300_core.gateway_sessions import GatewaySessionManager, MemoryCredentialStore
from b300_core.project_profiles import ProjectProfile, ProjectProfileStore
from b300_core.remote_profile import RemoteGatewayProfile, load_remote_profile, save_remote_profile


class SharedProfileStoreTests(unittest.TestCase):
    def test_gateway_store_migrates_legacy_and_syncs_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "remote_gateway.json"
            store_path = root / "gateway_profiles.json"
            save_remote_profile(RemoteGatewayProfile("gw-a.local", "operator", 22), legacy)
            store = GatewayProfileStore(store_path, legacy_path=legacy)
            profiles = store.list()
            self.assertEqual(len(profiles), 1)
            self.assertEqual(store.default().endpoint.host, "gw-a.local")
            second = GatewayProfile.create("Gateway B", "10.0.0.8", "robot", 2222)
            store.upsert(second)
            store.set_default(second.profile_id)
            self.assertEqual(load_remote_profile(legacy), second.endpoint)
            raw = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertNotIn("password", json.dumps(raw).lower())

    def test_project_store_shares_workspace_and_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "project"; workspace.mkdir()
            symbols = workspace / "firmware.axf"; symbols.write_bytes(b"ELF")
            store = ProjectProfileStore(root / "projects.json")
            profile = ProjectProfile.create("B300 Main", workspace, symbols)
            store.upsert(profile)
            self.assertEqual(store.default_id(), profile.project_id)
            self.assertEqual(store.default().symbols, symbols.resolve())
            self.assertTrue(store.delete(profile.project_id))
            self.assertIsNone(store.default())


class _FakeSession:
    def __init__(self, profile, *, credential_store):
        self.profile = profile
        self.credential_store = credential_store
        self.connected = False
        self.disconnect_calls = 0
    def ensure_connected(self, password=None, *, remember=False, timeout_seconds=12.0):
        secret = password if password is not None else self.credential_store.load(self.profile)
        if not secret:
            raise RuntimeError("password required")
        if remember:
            self.credential_store.save(self.profile, secret)
        self.connected = True
        return type("State", (), {"authenticated": True})()
    def disconnect(self, *, forget_password=False):
        self.connected = False; self.disconnect_calls += 1
        if forget_password:
            self.credential_store.clear(self.profile)


class GatewaySessionManagerTests(unittest.TestCase):
    def test_password_is_reused_in_process_and_cleared_on_shutdown(self):
        store = MemoryCredentialStore()
        manager = GatewaySessionManager(credential_store=store, session_factory=_FakeSession)
        profile = RemoteGatewayProfile("gateway.local", "operator", 22)
        manager.connect(profile, "secret")
        self.assertTrue(manager.connected(profile))
        manager.disconnect(profile)
        self.assertTrue(manager.has_cached_password(profile))
        manager.connect(profile)
        self.assertTrue(manager.connected(profile))
        manager.disconnect_all()
        self.assertFalse(manager.has_cached_password(profile))


if __name__ == "__main__":
    unittest.main()
