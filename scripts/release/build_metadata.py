"""Generate deterministic checksums and updater manifests for a release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

from .release_contract import (
    EXPECTED_PACKAGE_ASSETS,
    METADATA_ASSETS,
    UPDATE_PLATFORM_FILES,
)
from .version_tools import parse_semver


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    data = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    path.write_bytes(data)


def _validate_url(url: str, suffix: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise ValueError("Release URLs must use https://github.com.")
    normalized = url.rstrip("/")
    if "/releases/latest/" in normalized or not normalized.endswith(suffix):
        raise ValueError("Release URLs must identify the immutable version tag.")
    return normalized


def _asset_record(path: Path, url: str | None = None) -> Dict[str, object]:
    record: Dict[str, object] = {
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }
    if url is not None:
        record["file"] = path.name
        record["url"] = url.rstrip("/") + "/" + path.name
    return record


def build_release_metadata(
        asset_dir: Path, version: str, commit: str, published_at: str,
        base_url: str, release_page: str, notes: str) -> None:
    parse_semver(version)
    if COMMIT_RE.fullmatch(commit) is None:
        raise ValueError("Commit must be a lowercase 40-character Git SHA.")
    if not published_at.endswith("Z") or "T" not in published_at:
        raise ValueError("published_at must be an ISO-8601 UTC timestamp.")
    base_url = _validate_url(base_url, "/releases/download/v" + version)
    release_page = _validate_url(release_page, "/releases/tag/v" + version)
    root = Path(asset_dir)
    existing = {path.name for path in root.iterdir() if path.is_file()}
    expected = set(EXPECTED_PACKAGE_ASSETS)
    missing = sorted(expected - existing)
    if missing:
        raise ValueError("Missing release assets: %s" % ", ".join(missing))
    unexpected = sorted(existing - expected - set(METADATA_ASSETS))
    if unexpected:
        raise ValueError("Unexpected release assets: %s" % ", ".join(unexpected))

    assets = {
        name: _asset_record(root / name)
        for name in sorted(EXPECTED_PACKAGE_ASSETS)
    }
    manifest = {
        "assets": assets,
        "commit": commit,
        "openocd": "0.12.0-7",
        "product": "B300 ST-Link Tools",
        "published_at": published_at,
        "schema_version": 1,
        "version": version,
    }
    latest_platforms = {
        platform_name: _asset_record(root / filename, base_url)
        for platform_name, filename in sorted(UPDATE_PLATFORM_FILES.items())
    }
    latest = {
        "notes": notes,
        "platforms": latest_platforms,
        "product": "B300 ST-Link Tools",
        "published_at": published_at,
        "release_page": release_page,
        "schema_version": 1,
        "version": version,
    }
    _write_json(root / "release-manifest.json", manifest)
    _write_json(root / "latest.json", latest)

    checksum_names = sorted(EXPECTED_PACKAGE_ASSETS) + [
        "latest.json", "release-manifest.json"
    ]
    checksum_text = "".join(
        "%s  %s\n" % (_sha256(root / name), name) for name in checksum_names
    )
    (root / "SHA256SUMS.txt").write_text(
        checksum_text, encoding="ascii", newline="\n"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--release-page", required=True)
    parser.add_argument("--notes-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        build_release_metadata(
            args.asset_dir, args.version, args.commit, args.published_at,
            args.base_url, args.release_page,
            args.notes_file.read_text(encoding="utf-8").strip(),
        )
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
