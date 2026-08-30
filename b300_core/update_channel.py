"""Single public update stream for B300 ST-Link Tools.

Development/RC artifacts are validation builds only. User-facing updaters always
consume the signed public latest manifests produced by a normal vX.Y.Z release.
Legacy ``stable``/``beta`` values remain accepted and normalize to ``release``.
"""

from __future__ import annotations

from enum import Enum
from typing import Tuple, Union


RELEASE_BASE_URL = "https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/"


class UpdateChannel(str, Enum):
    RELEASE = "release"
    STABLE = "stable"
    BETA = "beta"


def normalize_update_channel(channel: Union[UpdateChannel, str]) -> UpdateChannel:
    selected = UpdateChannel(channel)
    if selected in (UpdateChannel.STABLE, UpdateChannel.BETA):
        return UpdateChannel.RELEASE
    return selected


def channel_endpoints(channel: Union[UpdateChannel, str] = UpdateChannel.RELEASE) -> Tuple[str, str]:
    normalize_update_channel(channel)
    manifest = RELEASE_BASE_URL + "latest.json"
    return manifest, manifest + ".minisig"


def cli_channel_endpoints(channel: Union[UpdateChannel, str] = UpdateChannel.RELEASE) -> Tuple[str, str]:
    normalize_update_channel(channel)
    manifest = RELEASE_BASE_URL + "latest-cli.json"
    return manifest, manifest + ".minisig"
