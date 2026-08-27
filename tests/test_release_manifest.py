import base64
import hashlib
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from b300_core.release_manifest import (
    ManifestError,
    SignatureError,
    parse_latest_manifest,
    verify_minisign,
)
from b300_core.update_platform import UpdatePlatform


TEST_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
TEST_KEY_ID = bytes.fromhex("0102030405060708")
TEST_PUBLIC_KEY = "RWQBAgMEBQYHCAOhB7/zzhC+HXDdGOdLwJln5NYwm6UNXx3chmQSVTG4"
MESSAGE = base64.b64decode(
    "eyJub3RlcyI6IlNhZmUgdXBkYXRlIiwicGxhdGZvcm1zIjp7IndpbmRvd3MteDY0Ijp7ImZpbGUiOiJCMzAwLVNUTGluay1HVUktV2luZG93cy14NjQuZXhlIiwic2hhMjU2IjoiYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYSIsInNpemUiOjEyMywidXJsIjoiaHR0cHM6Ly9naXRodWIuY29tL1R1bmdsYW0wNjA1L2IzMDAtc3RsaW5rLXRvb2xzL3JlbGVhc2VzL2Rvd25sb2FkL3YwLjMuMS9CMzAwLVNUTGluay1HVUktV2luZG93cy14NjQuZXhlIn19LCJwcm9kdWN0IjoiQjMwMCBTVC1MaW5rIFRvb2xzIiwicHVibGlzaGVkX2F0IjoiMjAyNi0wOC0yN1QxMjowMDowMFoiLCJyZWxlYXNlX3BhZ2UiOiJodHRwczovL2dpdGh1Yi5jb20vVHVuZ2xhbTA2MDUvYjMwMC1zdGxpbmstdG9vbHMvcmVsZWFzZXMvdGFnL3YwLjMuMSIsInNjaGVtYV92ZXJzaW9uIjoxLCJ2ZXJzaW9uIjoiMC4zLjEifQo="
)
SIGNATURE = base64.b64decode(
    "dW50cnVzdGVkIGNvbW1lbnQ6IHRlc3Qgc2lnbmF0dXJlClJVUUJBZ01FQlFZSENNcW1EQmpqUHdhTGUrZElvZ3EzWXVmb2hVdDY0NU9KKzlRQ1lkSkVDY0M1ZmlyaFVwNmdzelpHdWdEazdIbzRhd0ZtRWx0Mm1LSXBlVXczekZQSWpRQT0KdHJ1c3RlZCBjb21tZW50OiB0aW1lc3RhbXA6MTc4NzgzMjAwMCBmaWxlOmxhdGVzdC5qc29uCjhVTTJYMi9FR2RDK1dsaDQxL0xic2JrWWhzMVNBWVhRRndWekcrVjVCd0pZL2VBZXIxbFN0MVJvWW5FWFhDcTVLRktFNGhzRG51WkpRRnplNmQ3R0JnPT0K"
)


def sign_message(message: bytes, trusted_comment: bytes = b"fixture") -> bytes:
    primary = TEST_PRIVATE_KEY.sign(hashlib.blake2b(message, digest_size=64).digest())
    global_signature = TEST_PRIVATE_KEY.sign(primary + trusted_comment)
    return (
        b"untrusted comment: test fixture\n"
        + base64.b64encode(b"ED" + TEST_KEY_ID + primary) + b"\n"
        + b"trusted comment: " + trusted_comment + b"\n"
        + base64.b64encode(global_signature) + b"\n"
    )


class ReleaseManifestTests(unittest.TestCase):
    def test_verifies_official_minisign_packet_and_parses_release(self) -> None:
        verify_minisign(MESSAGE, SIGNATURE, TEST_PUBLIC_KEY)
        release = parse_latest_manifest(MESSAGE, SIGNATURE, TEST_PUBLIC_KEY)
        self.assertEqual(str(release.version), "0.3.1")
        asset = release.platforms["windows-x64"]
        self.assertEqual(asset.filename, "B300-STLink-GUI-Windows-x64.exe")
        self.assertEqual(asset.size, 123)
        self.assertEqual(asset.sha256, "a" * 64)
        self.assertEqual(
            release.select(UpdatePlatform.WINDOWS_X64).filename,
            "B300-STLink-GUI-Windows-x64.exe",
        )

    def test_rejects_modified_message_and_trusted_comment(self) -> None:
        with self.assertRaises(SignatureError):
            verify_minisign(MESSAGE.replace(b"0.3.1", b"9.9.9"), SIGNATURE, TEST_PUBLIC_KEY)
        modified = SIGNATURE.replace(b"file:latest.json", b"file:unsafe.json")
        with self.assertRaises(SignatureError):
            verify_minisign(MESSAGE, modified, TEST_PUBLIC_KEY)

    def test_rejects_wrong_key_id_and_malformed_signature(self) -> None:
        wrong_key = base64.b64encode(base64.b64decode(TEST_PUBLIC_KEY)[:2] + b"12345678" + base64.b64decode(TEST_PUBLIC_KEY)[10:]).decode()
        with self.assertRaises(SignatureError):
            verify_minisign(MESSAGE, SIGNATURE, wrong_key)
        with self.assertRaises(SignatureError):
            verify_minisign(MESSAGE, b"not a minisign file", TEST_PUBLIC_KEY)

    def test_rejects_signed_manifest_with_mutable_download_url(self) -> None:
        value = json.loads(MESSAGE)
        value["platforms"]["windows-x64"]["url"] = (
            "https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/"
            "B300-STLink-GUI-Windows-x64.exe"
        )
        message = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        with self.assertRaisesRegex(ManifestError, "immutable"):
            parse_latest_manifest(message, sign_message(message), TEST_PUBLIC_KEY)

    def test_rejects_oversized_or_unknown_schema(self) -> None:
        with self.assertRaisesRegex(ManifestError, "too large"):
            parse_latest_manifest(b" " * (256 * 1024 + 1), b"signature", TEST_PUBLIC_KEY)
        value = json.loads(MESSAGE)
        value["schema_version"] = 2
        message = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        with self.assertRaisesRegex(ManifestError, "schema"):
            parse_latest_manifest(message, sign_message(message), TEST_PUBLIC_KEY)


if __name__ == "__main__":
    unittest.main()
