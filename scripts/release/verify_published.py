"""Verify the public signed updater state after a GitHub release is published."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable, Dict
from urllib.parse import urlparse

from .release_contract import UPDATE_PLATFORM_FILES
from .version_tools import parse_semver


FetchBytes = Callable[[str, float], bytes]
ProbeUrl = Callable[[str, float], None]
RunCommand = Callable[..., subprocess.CompletedProcess]


def _fetch_bytes(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "B300-STLink-Release-Verifier/1.0", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(512 * 1024 + 1)


def _probe_url(url: str, timeout: float) -> None:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "B300-STLink-Release-Verifier/1.0",
            "Range": "bytes=0-0",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status not in (200, 206):
            raise RuntimeError("Asset returned HTTP %s: %s" % (status, url))
        response.read(1)


def _validate_public_manifest(data: bytes, version: str) -> Dict[str, str]:
    if len(data) > 512 * 1024:
        raise ValueError("Published latest.json is unexpectedly large.")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Published latest.json is not valid UTF-8 JSON.") from error
    if not isinstance(value, dict):
        raise ValueError("Published latest.json must be a JSON object.")
    if value.get("version") != version:
        raise ValueError(
            "Published updater version mismatch: expected %s, got %s."
            % (version, value.get("version"))
        )
    parse_semver(version)
    if value.get("product") != "B300 ST-Link Tools" or value.get("schema_version") != 1:
        raise ValueError("Published updater product/schema is invalid.")
    platforms = value.get("platforms")
    if not isinstance(platforms, dict):
        raise ValueError("Published updater platforms are missing.")
    expected_keys = set(UPDATE_PLATFORM_FILES)
    if set(platforms) != expected_keys:
        raise ValueError(
            "Published updater platform set mismatch: missing=%s extra=%s"
            % (sorted(expected_keys - set(platforms)), sorted(set(platforms) - expected_keys))
        )

    expected_prefix = (
        "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v%s/" % version
    )
    urls: Dict[str, str] = {}
    for platform, filename in UPDATE_PLATFORM_FILES.items():
        record = platforms.get(platform)
        if not isinstance(record, dict) or record.get("file") != filename:
            raise ValueError("Published updater filename mismatch for %s." % platform)
        url = record.get("url")
        if not isinstance(url, str) or url != expected_prefix + filename:
            raise ValueError("Published updater URL is not immutable for %s." % platform)
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "github.com":
            raise ValueError("Published updater URL host is invalid for %s." % platform)
        digest = record.get("sha256")
        size = record.get("size")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Published updater SHA-256 is invalid for %s." % platform)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("Published updater size is invalid for %s." % platform)
        urls[platform] = url
    return urls


def verify_once(
        *, version: str, manifest_url: str, signature_url: str,
        minisign: Path, public_key: str, timeout: float,
        fetch: FetchBytes = _fetch_bytes, probe: ProbeUrl = _probe_url,
        run_command: RunCommand = subprocess.run) -> None:
    manifest = fetch(manifest_url, timeout)
    signature = fetch(signature_url, timeout)
    if len(signature) > 64 * 1024:
        raise ValueError("Published latest.json.minisig is unexpectedly large.")

    with tempfile.TemporaryDirectory(prefix="b300-release-verify-") as directory:
        root = Path(directory)
        manifest_path = root / "latest.json"
        signature_path = root / "latest.json.minisig"
        manifest_path.write_bytes(manifest)
        signature_path.write_bytes(signature)
        result = run_command(
            [
                str(minisign), "-Vm", str(manifest_path),
                "-x", str(signature_path), "-P", public_key,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "minisign verification failed").strip()
            raise ValueError("Published updater signature is invalid: %s" % detail)

    asset_urls = _validate_public_manifest(manifest, version)
    for platform, url in sorted(asset_urls.items()):
        try:
            probe(url, timeout)
        except Exception as error:
            raise RuntimeError("Published asset is unreachable for %s: %s" % (platform, error)) from error


def verify_with_retry(**kwargs) -> None:
    attempts = int(kwargs.pop("attempts"))
    delay = float(kwargs.pop("delay"))
    if attempts <= 0 or delay < 0:
        raise ValueError("Retry attempts/delay are invalid.")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            verify_once(**kwargs)
            return
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break
            print(
                "Published updater verification attempt %d/%d failed: %s"
                % (attempt, attempts, error),
                flush=True,
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--signature-url", required=True)
    parser.add_argument("--minisign", required=True, type=Path)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--attempts", type=int, default=8)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if not args.minisign.is_file():
        parser.error("minisign executable does not exist: %s" % args.minisign)
    try:
        verify_with_retry(
            version=args.version,
            manifest_url=args.manifest_url,
            signature_url=args.signature_url,
            minisign=args.minisign,
            public_key=args.public_key,
            timeout=args.timeout,
            attempts=args.attempts,
            delay=args.delay,
        )
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))
    print("Published signed updater state verified for v%s" % args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
