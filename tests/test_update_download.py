"""Exercise the updater against a real local HTTP range server."""

import hashlib
import tempfile
import threading
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from b300_core.release_manifest import ReleaseAsset
from b300_core.updater import DownloadCancelled, UpdateClient, UpdateDownloadError


PAYLOAD = bytes(range(256)) * (32 * 1024)  # Eight MiB; all four parts are nonempty.
URL = "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/v0.24.3/B300.exe"


@contextmanager
def range_server(mode="ranges", payload=PAYLOAD, cancel=None, reject_probe=False):
    state = {"active": 0, "peak": 0, "full": 0, "ranges": [], "requests": 0}
    lock = threading.Lock()
    parallel = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            value = self.headers.get("Range")
            with lock:
                state["requests"] += 1
            ranged = value is not None and mode != "ignore"
            if ranged:
                start, end = (int(v) for v in value.removeprefix("bytes=").split("-"))
                probe = (start, end) == (0, 0)
                with lock:
                    state["ranges"].append((start, end))
                if mode == "ignore_after_probe" and not probe:
                    ranged = False
                elif (mode == "reject" or reject_probe) and probe:
                    self.send_error(416)
                    return
            if not ranged:
                with lock:
                    state["full"] += 1
                start, end = 0, len(payload) - 1
                probe = False
            body = payload[start:end + 1]
            if mode == "corrupt" and ranged and not probe:
                body = b"!" + body[1:]
            if mode == "short" and ranged and not probe:
                body = body[:-1]
            if mode == "long" and ranged and not probe:
                body += b"!"
            unexpected_partial = not ranged and mode == "partial_full"
            self.send_response(206 if ranged or unexpected_partial else 200)
            if ranged:
                content_range = "bytes %d-%d/%d" % (start, end, len(payload))
                if mode == "bad_range" and not probe:
                    content_range = "bytes %d-%d/%d" % (start + 1, end, len(payload))
                self.send_header("Content-Range", content_range)
            elif mode in {"partial_full", "range_header_full"}:
                self.send_header("Content-Range", "bytes 0-%d/%d" % (end, len(payload)))
            if mode not in {"short", "long"}:
                length = len(body)
                if mode == "bad_length" and ranged and not probe:
                    length += 1
                self.send_header("Content-Length", str(length))
            if mode == "encoded" and ranged and not probe:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            if ranged and not probe:
                with lock:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                    if state["active"] >= 2:
                        parallel.set()
                parallel.wait(2)
            try:
                if cancel is not None and ranged and not probe:
                    cancel.set()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                if ranged and not probe:
                    with lock:
                        state["active"] -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def opener(request, *, timeout):
        local = urllib.request.Request(
            "http://127.0.0.1:%d/package" % server.server_port,
            headers=dict(request.header_items()),
        )
        return urllib.request.urlopen(local, timeout=timeout)

    try:
        yield opener, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


class UpdateDownloadTests(unittest.TestCase):
    def asset(self, payload=PAYLOAD):
        return ReleaseAsset("B300.exe", URL, len(payload), hashlib.sha256(payload).hexdigest())

    def test_large_download_overlaps_ranges_and_publishes_verified_bytes(self):
        with range_server() as (opener, state), tempfile.TemporaryDirectory() as temp:
            progress, callback_threads = [], []
            caller = threading.get_ident()

            def report(done, total):
                progress.append((done, total))
                callback_threads.append(threading.get_ident())

            path = UpdateClient("unused", "windows-x64", open_url=opener).download(
                self.asset(), Path(temp), report, threading.Event())
            self.assertEqual(path.read_bytes(), PAYLOAD)
            self.assertGreaterEqual(state["peak"], 2, "Package ranges did not overlap")
            self.assertLessEqual(state["peak"], 4)
            self.assertEqual(state["full"], 0)
            self.assertEqual(list(Path(temp).iterdir()), [path])
            self.assertEqual(progress[-1], (len(PAYLOAD), len(PAYLOAD)))
            self.assertEqual(progress, sorted(progress))
            self.assertEqual(set(callback_threads), {caller})

    def test_unsupported_ranges_fall_back_to_verified_full_download(self):
        for mode in ("ignore", "reject", "ignore_after_probe"):
            with self.subTest(mode=mode), range_server(mode) as (opener, state), tempfile.TemporaryDirectory() as temp:
                path = UpdateClient("unused", "windows-x64", open_url=opener).download(
                    self.asset(), Path(temp), lambda *args: None, threading.Event())
                self.assertEqual(path.read_bytes(), PAYLOAD)
                self.assertEqual(list(Path(temp).iterdir()), [path])
                self.assertGreaterEqual(state["full"], 1)

    def test_invalid_ranges_never_replace_existing_package_or_leave_partials(self):
        for mode in ("bad_range", "bad_length", "encoded", "short", "long", "corrupt"):
            with self.subTest(mode=mode), range_server(mode) as (opener, _), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                old = root / "B300.exe"
                old.write_bytes(b"previous verified release")
                with self.assertRaises(UpdateDownloadError):
                    UpdateClient("unused", "windows-x64", open_url=opener).download(
                        self.asset(), root, lambda *args: None, threading.Event())
                self.assertEqual(old.read_bytes(), b"previous verified release")
                self.assertEqual(list(root.iterdir()), [old])

    def test_cancellation_during_parallel_download_cleans_up_and_preserves_old_package(self):
        cancel = threading.Event()
        with range_server(cancel=cancel) as (opener, _), tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "B300.exe"
            old.write_bytes(b"previous release")
            with self.assertRaises(DownloadCancelled):
                UpdateClient("unused", "windows-x64", open_url=opener).download(
                    self.asset(), root, lambda *args: None, cancel)
            self.assertEqual(old.read_bytes(), b"previous release")
            self.assertEqual(list(root.iterdir()), [old])

    def test_verified_cache_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "B300.exe"
            path.write_bytes(PAYLOAD)

            def offline(*args, **kwargs):
                raise OSError("offline")

            progress = []
            client = UpdateClient("unused", "windows-x64", open_url=offline)
            result = client.download(self.asset(), path.parent,
                                     lambda *args: progress.append(args), threading.Event())
            self.assertEqual(result, path)
            self.assertEqual(progress[-1], (len(PAYLOAD), len(PAYLOAD)))

    def test_same_size_corrupt_cache_is_replaced_by_fresh_verified_download(self):
        with range_server() as (opener, state), tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "B300.exe"
            path.write_bytes(b"!" * len(PAYLOAD))
            result = UpdateClient("unused", "windows-x64", open_url=opener).download(
                self.asset(), path.parent, lambda *args: None, threading.Event())
            self.assertEqual(result.read_bytes(), PAYLOAD)
            self.assertGreater(state["requests"], 0)

    def test_small_packages_use_one_request(self):
        payload = b"small verified package"
        with range_server(payload=payload) as (opener, state), tempfile.TemporaryDirectory() as temp:
            path = UpdateClient("unused", "windows-x64", open_url=opener).download(
                self.asset(payload), Path(temp), lambda *args: None, threading.Event())
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(state["requests"], 1)
            self.assertEqual(state["ranges"], [])

    def test_full_download_rejects_partial_status_or_range_header(self):
        for mode in ("partial_full", "range_header_full"):
            for payload, workers in ((b"small package", 1), (PAYLOAD, 1), (PAYLOAD, 4)):
                with self.subTest(mode=mode, size=len(payload), workers=workers), range_server(mode, payload, reject_probe=True) as (opener, _), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    with self.assertRaises(UpdateDownloadError):
                        UpdateClient("unused", "windows-x64", open_url=opener,
                                     download_workers=workers).download(
                            self.asset(payload), root, lambda *args: None, threading.Event())
                    self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
