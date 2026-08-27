"""Verify Minisign metadata and parse the trusted updater release contract."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .versioning import SemVer


MAX_MANIFEST_BYTES = 256 * 1024
MAX_SIGNATURE_BYTES = 16 * 1024
MAX_NOTES_CHARS = 64 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
EXPECTED_UPDATE_FILENAMES = {
    "windows-x64": "B300-STLink-GUI-Windows-x64.exe",
    "linux-x64-appimage": "B300-STLink-GUI-Ubuntu-x64.AppImage",
    "linux-x64-deb": "b300-stlink-gui_amd64.deb",
    "linux-arm64-appimage": "B300-STLink-GUI-Ubuntu-arm64.AppImage",
    "linux-arm64-deb": "b300-stlink-gui_arm64.deb",
}


class SignatureError(ValueError):
    """The detached signature or public key is invalid."""


class ManifestError(ValueError):
    """Signed update metadata violates the release contract."""


@dataclass(frozen=True)
class ReleaseAsset:
    filename: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class LatestRelease:
    version: SemVer
    notes: str
    published_at: str
    release_page: str
    platforms: Dict[str, ReleaseAsset]

    def select(self, platform_name: str) -> ReleaseAsset:
        try:
            return self.platforms[str(platform_name)]
        except KeyError as error:
            raise ManifestError(
                "The release does not provide an update for %s." % platform_name
            ) from error


def _decode_base64(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise SignatureError("Invalid base64 in %s." % label) from error


def verify_minisign(message: bytes, signature: bytes, public_key: str) -> str:
    if len(signature) > MAX_SIGNATURE_BYTES:
        raise SignatureError("Minisign signature is too large.")
    try:
        lines = signature.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise SignatureError("Minisign signature is not UTF-8 text.") from error
    if len(lines) != 4:
        raise SignatureError("Minisign signature must contain exactly four lines.")
    if not lines[0].startswith("untrusted comment: "):
        raise SignatureError("Minisign untrusted comment is missing.")
    if not lines[2].startswith("trusted comment: "):
        raise SignatureError("Minisign trusted comment is missing.")
    trusted_comment = lines[2][len("trusted comment: "):]
    if not trusted_comment or len(trusted_comment.encode("utf-8")) > 8192:
        raise SignatureError("Minisign trusted comment is invalid.")

    public_packet = _decode_base64(public_key.strip(), "public key")
    signature_packet = _decode_base64(lines[1], "signature packet")
    global_signature = _decode_base64(lines[3], "comment signature")
    if len(public_packet) != 42 or public_packet[:2] != b"Ed":
        raise SignatureError("Unsupported Minisign public key.")
    if len(signature_packet) != 74 or signature_packet[:2] not in {b"Ed", b"ED"}:
        raise SignatureError("Unsupported Minisign signature packet.")
    if len(global_signature) != 64:
        raise SignatureError("Invalid Minisign comment signature length.")
    if signature_packet[2:10] != public_packet[2:10]:
        raise SignatureError("Minisign signature key id does not match the public key.")

    primary_signature = signature_packet[10:]
    signed_message = (
        hashlib.blake2b(message, digest_size=64).digest()
        if signature_packet[:2] == b"ED" else message
    )
    verifier = Ed25519PublicKey.from_public_bytes(public_packet[10:])
    try:
        verifier.verify(primary_signature, signed_message)
        verifier.verify(
            global_signature,
            primary_signature + trusted_comment.encode("utf-8"),
        )
    except InvalidSignature as error:
        raise SignatureError("Minisign verification failed.") from error
    return trusted_comment


def _expect_exact_keys(value: dict, expected: set, label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ManifestError(
            "%s fields do not match the signed schema (missing=%s, extra=%s)." %
            (label, sorted(expected - actual), sorted(actual - expected))
        )


def _validate_github_url(url: str, expected_path: str, label: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https" or parsed.netloc != "github.com" or
        parsed.path != expected_path or parsed.params or parsed.query or parsed.fragment
    ):
        raise ManifestError("%s must use an immutable GitHub release URL." % label)


def parse_latest_manifest(
        data: bytes, signature: bytes, public_key: str) -> LatestRelease:
    if len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError("Update manifest is too large.")
    verify_minisign(data, signature, public_key)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError("Signed update manifest is not valid UTF-8 JSON.") from error
    if not isinstance(value, dict):
        raise ManifestError("Signed update manifest must be a JSON object.")
    _expect_exact_keys(
        value,
        {"notes", "platforms", "product", "published_at", "release_page",
         "schema_version", "version"},
        "Manifest",
    )
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise ManifestError("Unsupported update manifest schema version.")
    if value["product"] != "B300 ST-Link Tools":
        raise ManifestError("Update manifest product does not match B300 ST-Link Tools.")
    try:
        version = SemVer.parse(value["version"])
    except (TypeError, ValueError) as error:
        raise ManifestError("Update manifest version is invalid.") from error
    notes = value["notes"]
    if not isinstance(notes, str) or len(notes) > MAX_NOTES_CHARS:
        raise ManifestError("Update release notes are invalid.")
    published_at = value["published_at"]
    if not isinstance(published_at, str) or UTC_RE.fullmatch(published_at) is None:
        raise ManifestError("Update publication timestamp is invalid.")
    release_page = value["release_page"]
    if not isinstance(release_page, str):
        raise ManifestError("Update release page is invalid.")
    repository_path = "/Tunglam0605/b300-stlink-tools"
    _validate_github_url(
        release_page, repository_path + "/releases/tag/v" + str(version), "Release page"
    )

    raw_platforms = value["platforms"]
    if not isinstance(raw_platforms, dict) or not raw_platforms:
        raise ManifestError("Update manifest must provide at least one platform.")
    if not set(raw_platforms).issubset(EXPECTED_UPDATE_FILENAMES):
        raise ManifestError("Update manifest contains an unsupported platform.")
    platforms: Dict[str, ReleaseAsset] = {}
    for platform_name, raw_asset in raw_platforms.items():
        if not isinstance(raw_asset, dict):
            raise ManifestError("Update asset must be an object.")
        _expect_exact_keys(raw_asset, {"file", "sha256", "size", "url"}, "Asset")
        filename = raw_asset["file"]
        if filename != EXPECTED_UPDATE_FILENAMES[platform_name]:
            raise ManifestError("Update asset filename does not match its platform.")
        digest = raw_asset["sha256"]
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise ManifestError("Update asset SHA-256 is invalid.")
        size = raw_asset["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ManifestError("Update asset size is invalid.")
        url = raw_asset["url"]
        if not isinstance(url, str):
            raise ManifestError("Update asset URL is invalid.")
        _validate_github_url(
            url,
            repository_path + "/releases/download/v%s/%s" % (version, filename),
            "Asset URL",
        )
        platforms[platform_name] = ReleaseAsset(filename, url, size, digest)
    return LatestRelease(version, notes, published_at, release_page, platforms)
