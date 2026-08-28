"""Release-channel endpoints; each channel keeps the same signed-manifest trust model."""

from __future__ import annotations

from enum import Enum
from typing import Tuple


RELEASE_BASE_URL = "https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/"


class UpdateChannel(str, Enum):
    STABLE = "stable"
    BETA = "beta"


def channel_endpoints(channel: UpdateChannel) -> Tuple[str, str]:
    selected = UpdateChannel(channel)
    manifest_name = "latest.json" if selected == UpdateChannel.STABLE else "latest-beta.json"
    manifest = RELEASE_BASE_URL + manifest_name
    return manifest, manifest + ".minisig"


def cli_channel_endpoints(channel: UpdateChannel) -> Tuple[str, str]:
    selected = UpdateChannel(channel)
    manifest_name = (
        "latest-cli.json" if selected == UpdateChannel.STABLE else "latest-beta-cli.json"
    )
    manifest = RELEASE_BASE_URL + manifest_name
    return manifest, manifest + ".minisig"
