from __future__ import annotations

import unittest

from b300_core.update_channel import UpdateChannel, channel_endpoints
from b300_core.updater import UpdateClient


class UpdateChannelTests(unittest.TestCase):
    def test_stable_is_default_release_channel(self) -> None:
        client = UpdateClient("public-key", "windows-x64")
        self.assertEqual(client.channel, UpdateChannel.STABLE)
        self.assertTrue(client.manifest_url.endswith("/latest.json"))

    def test_beta_uses_its_own_signed_manifest_endpoint(self) -> None:
        manifest, signature = channel_endpoints(UpdateChannel.BETA)
        client = UpdateClient("public-key", "linux-x64", channel=UpdateChannel.BETA)
        self.assertEqual(client.channel, UpdateChannel.BETA)
        self.assertEqual(client.manifest_url, manifest)
        self.assertEqual(client.signature_url, signature)
        self.assertTrue(manifest.endswith("/latest-beta.json"))


if __name__ == "__main__":
    unittest.main()
