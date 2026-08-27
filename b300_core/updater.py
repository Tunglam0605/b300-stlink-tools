"""Signed update discovery and verified atomic package downloads."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Callable, Optional
from urllib.parse import urlparse

from .release_manifest import (
    LatestRelease,
    ManifestError,
    ReleaseAsset,
    parse_latest_manifest,
)
from .versioning import SemVer


DEFAULT_MANIFEST_URL = (
    "https://github.com/Tunglam0605/b300-stlink-tools/"
    "releases/latest/download/latest.json"
)
DEFAULT_SIGNATURE_URL = DEFAULT_MANIFEST_URL + ".minisig"
USER_AGENT = "B300-STLink-Tools-Updater/0.3"


class UpdateDownloadError(RuntimeError):
    """Network or package verification failed."""


class DownloadCancelled(UpdateDownloadError):
    """The operator cancelled a package download."""


@dataclass(frozen=True)
class UpdateCheckResult:
    available: bool
    release: LatestRelease
    asset: Optional[ReleaseAsset]


def should_auto_check(
        last_check_utc: Optional[str], now: Optional[datetime] = None,
        interval: timedelta = timedelta(hours=24)) -> bool:
    if not last_check_utc:
        return True
    selected_now = now or datetime.now(timezone.utc)
    try:
        previous = datetime.strptime(last_check_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return True
    return selected_now - previous >= interval


class UpdateClient:
    def __init__(
            self, public_key: str, platform_name: str,
            manifest_url: str = DEFAULT_MANIFEST_URL,
            signature_url: str = DEFAULT_SIGNATURE_URL,
            open_url: Callable = urllib.request.urlopen,
            timeout_seconds: float = 8.0) -> None:
        self.public_key = public_key
        self.platform_name = platform_name
        self.manifest_url = manifest_url
        self.signature_url = signature_url
        self.open_url = open_url
        self.timeout_seconds = timeout_seconds

    def _request(self, url: str):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        return self.open_url(request, self.timeout_seconds)

    def _fetch_limited(self, url: str, limit: int, label: str) -> bytes:
        try:
            with self._request(url) as response:
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > limit:
                    raise UpdateDownloadError("%s response is too large." % label)
                data = response.read(limit + 1)
        except UpdateDownloadError:
            raise
        except Exception as error:
            raise UpdateDownloadError("Unable to download %s: %s" % (label, error)) from error
        if len(data) > limit:
            raise UpdateDownloadError("%s response is too large." % label)
        return data

    def check(self, current_version: str) -> UpdateCheckResult:
        try:
            current = SemVer.parse(current_version)
        except ValueError as error:
            raise ManifestError("Current application version is invalid.") from error
        manifest = self._fetch_limited(
            self.manifest_url, 256 * 1024, "latest.json"
        )
        signature = self._fetch_limited(
            self.signature_url, 16 * 1024, "latest.json.minisig"
        )
        release = parse_latest_manifest(manifest, signature, self.public_key)
        if release.version <= current:
            return UpdateCheckResult(False, release, None)
        return UpdateCheckResult(
            True, release, release.select(self.platform_name)
        )

    @staticmethod
    def _validate_download_asset(asset: ReleaseAsset) -> None:
        if (
            not asset.filename or Path(asset.filename).name != asset.filename or
            "/" in asset.filename or "\\" in asset.filename
        ):
            raise UpdateDownloadError("Update filename is unsafe.")
        parsed = urlparse(asset.url)
        if (
            parsed.scheme != "https" or parsed.netloc != "github.com" or
            "/releases/download/v" not in parsed.path or
            not parsed.path.endswith("/" + asset.filename) or
            parsed.params or parsed.query or parsed.fragment
        ):
            raise UpdateDownloadError("Update URL is not an immutable GitHub asset.")
        if asset.size <= 0 or asset.size > 1024 * 1024 * 1024:
            raise UpdateDownloadError("Update size is outside the supported range.")

    def download(
            self, asset: ReleaseAsset, destination_dir: Path,
            progress: Callable[[int, int], None], cancel: Event) -> Path:
        self._validate_download_asset(asset)
        if cancel.is_set():
            raise DownloadCancelled("Update download was cancelled.")
        destination = Path(destination_dir)
        destination.mkdir(parents=True, exist_ok=True)
        final_path = destination / asset.filename
        temporary_path: Optional[Path] = None
        digest = hashlib.sha256()
        received = 0
        try:
            with self._request(asset.url) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) != asset.size:
                    raise UpdateDownloadError("Update Content-Length does not match manifest.")
                with tempfile.NamedTemporaryFile(
                        mode="wb", delete=False, dir=str(destination),
                        prefix=asset.filename + ".", suffix=".part") as output:
                    temporary_path = Path(output.name)
                    while True:
                        if cancel.is_set():
                            raise DownloadCancelled("Update download was cancelled.")
                        chunk = response.read(min(64 * 1024, asset.size - received + 1))
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > asset.size:
                            raise UpdateDownloadError("Update is larger than the signed size.")
                        output.write(chunk)
                        digest.update(chunk)
                        progress(received, asset.size)
                    output.flush()
                    os.fsync(output.fileno())
            if received != asset.size:
                raise UpdateDownloadError("Update is smaller than the signed size.")
            if digest.hexdigest() != asset.sha256:
                raise UpdateDownloadError("Update SHA-256 does not match the signed manifest.")
            os.replace(str(temporary_path), str(final_path))
            temporary_path = None
            return final_path
        except (UpdateDownloadError, DownloadCancelled):
            raise
        except Exception as error:
            raise UpdateDownloadError("Unable to download update package: %s" % error) from error
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
