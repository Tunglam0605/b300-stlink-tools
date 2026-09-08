from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b300_core.gateway_profiles import GatewayProfile, GatewayProfileStore
from b300_core.remote_profile import RemoteGatewayProfile, save_remote_profile


class GatewayProfileTests(unittest.TestCase):
    def test_legacy_migration_preserves_custom_cli_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.json"
            save_remote_profile(RemoteGatewayProfile(
                "gateway.local", "operator", 22, "/opt/b300/b300-stlink"
            ), legacy)
            store = GatewayProfileStore(root / "gateways.json", legacy_path=legacy)

            self.assertEqual(store.default().endpoint.cli_path, "/opt/b300/b300-stlink")

    def test_custom_cli_path_persists_with_a_named_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = GatewayProfileStore(root / "gateways.json", legacy_path=root / "legacy.json")
            profile = GatewayProfile.create(
                "Lab", "gateway.local", "operator", cli_path="/opt/b300/b300-stlink"
            )
            store.upsert(profile)
            loaded = store.get(profile.profile_id)
            self.assertEqual(loaded.endpoint.cli_path, "/opt/b300/b300-stlink")
            raw = json.loads((root / "gateways.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["profiles"][0]["cli_path"], "/opt/b300/b300-stlink")


if __name__ == "__main__":
    unittest.main()
