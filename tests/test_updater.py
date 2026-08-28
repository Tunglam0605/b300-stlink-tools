import hashlib
import io
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from b300_core.release_manifest import ReleaseAsset
from b300_core.updater import (
    DownloadCancelled,
    UpdateClient,
    UpdateDownloadError,
    should_auto_check,
)
from tests.test_release_manifest import MESSAGE, SIGNATURE, TEST_PUBLIC_KEY


MANIFEST_URL = "https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/latest.json"
SIGNATURE_URL = MANIFEST_URL + ".minisig"


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, content_length=None):
        super().__init__(data)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeOpener:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request.full_url, timeout, request.headers))
        response = self.responses[request.full_url]
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response, len(response))


class KeywordOnlyTimeoutOpener(FakeOpener):
    def __call__(self, request, *, timeout):
        return super().__call__(request, timeout=timeout)


class UpdaterTests(unittest.TestCase):
    def test_urlopen_timeout_is_passed_as_keyword_not_request_body(self) -> None:
        opener = KeywordOnlyTimeoutOpener({MANIFEST_URL: MESSAGE, SIGNATURE_URL: SIGNATURE})
        client = UpdateClient(
            TEST_PUBLIC_KEY, "windows-x64", open_url=opener, timeout_seconds=3.25
        )
        result = client.check("0.3.0")
        self.assertTrue(result.available)
        self.assertEqual([item[1] for item in opener.requests], [3.25, 3.25])

    def test_check_returns_new_release_only_when_version_is_newer(self) -> None:
        opener = FakeOpener({MANIFEST_URL: MESSAGE, SIGNATURE_URL: SIGNATURE})
        client = UpdateClient(
            public_key=TEST_PUBLIC_KEY,
            platform_name="windows-x64",
            open_url=opener,
        )
        available = client.check("0.3.0")
        current = client.check("0.3.1")
        newer_local = client.check("0.4.0")
        self.assertTrue(available.available)
        self.assertEqual(available.asset.filename, "B300-STLink-GUI-Windows-x64.exe")
        self.assertFalse(current.available)
        self.assertFalse(newer_local.available)
        self.assertEqual(len(opener.requests), 6)

    def test_check_wraps_network_failure_without_trusting_partial_data(self) -> None:
        opener = FakeOpener({MANIFEST_URL: OSError("offline")})
        client = UpdateClient(TEST_PUBLIC_KEY, "windows-x64", open_url=opener)
        with self.assertRaisesRegex(UpdateDownloadError, "latest.json"):
            client.check("0.3.0")

    def test_download_verifies_size_hash_and_atomically_publishes_file(self) -> None:
        payload = b"verified installer bytes"
        url = "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v0.3.1/B300.exe"
        asset = ReleaseAsset(
            "B300.exe", url, len(payload), hashlib.sha256(payload).hexdigest()
        )
        opener = FakeOpener({url: payload})
        progress = []
        with tempfile.TemporaryDirectory() as temp:
            result = UpdateClient(TEST_PUBLIC_KEY, "windows-x64", open_url=opener).download(
                asset, Path(temp), lambda done, total: progress.append((done, total)),
                threading.Event(),
            )
            self.assertEqual(result, Path(temp) / "B300.exe")
            self.assertEqual(result.read_bytes(), payload)
            self.assertEqual(progress[-1], (len(payload), len(payload)))
            self.assertEqual(list(Path(temp).glob("*.part")), [])

    def test_download_rejects_wrong_hash_and_removes_partial_file(self) -> None:
        payload = b"corrupt"
        url = "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v0.3.1/B300.exe"
        asset = ReleaseAsset("B300.exe", url, len(payload), "0" * 64)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(UpdateDownloadError, "SHA-256"):
                UpdateClient(
                    TEST_PUBLIC_KEY, "windows-x64",
                    open_url=FakeOpener({url: payload}),
                ).download(asset, root, lambda done, total: None, threading.Event())
            self.assertEqual(list(root.iterdir()), [])

    def test_download_honors_cancellation_before_publishing(self) -> None:
        payload = b"content"
        url = "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v0.3.1/B300.exe"
        asset = ReleaseAsset(
            "B300.exe", url, len(payload), hashlib.sha256(payload).hexdigest()
        )
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(DownloadCancelled):
                UpdateClient(
                    TEST_PUBLIC_KEY, "windows-x64",
                    open_url=FakeOpener({url: payload}),
                ).download(asset, root, lambda done, total: None, cancel)
            self.assertEqual(list(root.iterdir()), [])

    def test_auto_check_is_due_after_24_hours_or_invalid_cache(self) -> None:
        now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(should_auto_check("2026-08-26T13:00:01Z", now))
        self.assertTrue(should_auto_check("2026-08-26T12:00:00Z", now))
        self.assertTrue(should_auto_check("invalid", now))
        self.assertTrue(should_auto_check(None, now, timedelta(hours=24)))


if __name__ == "__main__":
    unittest.main()
