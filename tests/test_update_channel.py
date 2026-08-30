from __future__ import annotations

import unittest

from b300_core.update_channel import UpdateChannel, channel_endpoints, cli_channel_endpoints
from b300_core.updater import UpdateClient


class UpdateChannelTests(unittest.TestCase):
    def test_release_is_the_single_public_update_stream(self) -> None:
        client = UpdateClient("public-key", "windows-x64")
        self.assertEqual(client.channel, UpdateChannel.RELEASE)
        self.assertTrue(client.manifest_url.endswith("/latest.json"))
        self.assertNotIn("beta", client.manifest_url.lower())

    def test_cli_uses_single_signed_latest_manifest(self) -> None:
        manifest, signature = cli_channel_endpoints(UpdateChannel.RELEASE)
        self.assertTrue(manifest.endswith("/latest-cli.json"))
        self.assertEqual(signature, manifest + ".minisig")

    def test_legacy_channel_aliases_cannot_select_a_different_feed(self) -> None:
        release = channel_endpoints(UpdateChannel.RELEASE)
        self.assertEqual(channel_endpoints(UpdateChannel.STABLE), release)
        self.assertEqual(channel_endpoints(UpdateChannel.BETA), release)
        self.assertEqual(channel_endpoints("stable"), release)
        self.assertEqual(channel_endpoints("beta"), release)
        client = UpdateClient("public-key", "linux-x64", channel="beta")
        self.assertEqual(client.channel, UpdateChannel.RELEASE)
        self.assertEqual((client.manifest_url, client.signature_url), release)


if __name__ == "__main__":
    unittest.main()
