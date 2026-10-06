"""Bounded HTTP range downloads with signed-size/hash verification."""

from __future__ import annotations

import hashlib
import os
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event, Lock
from urllib.error import HTTPError


CHUNK_BYTES = 64 * 1024
PARALLEL_MIN_BYTES = 8 * 1024 * 1024


class UpdateDownloadError(RuntimeError):
    """Network or package verification failed."""


class DownloadCancelled(UpdateDownloadError):
    """The operator cancelled a package download."""


class _RangesUnavailable(Exception):
    pass


def _check_cancel(cancel, abort=None):
    if cancel.is_set() or (abort is not None and abort.is_set()):
        raise DownloadCancelled("Update download was cancelled.")


def _matches_signed_file(path, asset, cancel):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size != asset.size:
            return False
        digest = hashlib.sha256()
        received = 0
        with path.open("rb") as source:
            while True:
                _check_cancel(cancel)
                chunk = source.read(min(1024 * 1024, asset.size - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > asset.size:
                    return False
                digest.update(chunk)
        return received == asset.size and digest.hexdigest() == asset.sha256
    except OSError:
        return False


def _check_encoding(response):
    encoding = response.headers.get("Content-Encoding", "identity").lower()
    if encoding != "identity":
        raise UpdateDownloadError("Update response must use identity encoding.")


def _check_range(response, start, end, size):
    if response.status == 200:
        raise _RangesUnavailable()
    if response.status != 206:
        raise UpdateDownloadError("Update range response must have HTTP status 206.")
    expected = "bytes %d-%d/%d" % (start, end, size)
    if response.headers.get("Content-Range") != expected:
        raise UpdateDownloadError("Update Content-Range does not match the requested signed span.")
    length = response.headers.get("Content-Length")
    if length is not None and int(length) != end - start + 1:
        raise UpdateDownloadError("Update range Content-Length does not match the requested span.")
    _check_encoding(response)


class _Progress:
    """Only the caller thread reports progress; 100% follows verification."""

    def __init__(self, callback, size):
        self.callback = callback
        self.size = size
        self.last = -1

    def report(self, received):
        # A fallback can redownload earlier bytes; never move the UI backwards.
        value = min(max(self.last, received), self.size - 1)
        if value > self.last:
            self.last = value
            self.callback(value, self.size)

    def finish(self):
        self.callback(self.size, self.size)


def _stream_response(response, path, asset, progress, cancel):
    if getattr(response, "status", 200) != 200 or response.headers.get("Content-Range") is not None:
        raise UpdateDownloadError("Full update response must have HTTP status 200 without Content-Range.")
    _check_encoding(response)
    length = response.headers.get("Content-Length")
    if length is not None and int(length) != asset.size:
        raise UpdateDownloadError("Update Content-Length does not match manifest.")
    received = 0
    with path.open("wb") as output:
        while True:
            _check_cancel(cancel)
            chunk = response.read(min(CHUNK_BYTES, asset.size - received + 1))
            if not chunk:
                break
            received += len(chunk)
            if received > asset.size:
                raise UpdateDownloadError("Update is larger than the signed size.")
            output.write(chunk)
            progress.report(received)
        output.flush()
        os.fsync(output.fileno())
    if received != asset.size:
        raise UpdateDownloadError("Update is smaller than the signed size.")


def _parallel_ranges(request, path, asset, progress, cancel, workers):
    abort, lock = Event(), Lock()
    received = [0] * workers
    errors = []

    def fetch(index):
        start = asset.size * index // workers
        end = asset.size * (index + 1) // workers - 1
        _check_cancel(cancel, abort)
        try:
            response = request(asset.url, {"Range": "bytes=%d-%d" % (start, end)})
        except HTTPError as error:
            if error.code == 416:
                error.close()
                raise _RangesUnavailable() from error
            raise
        with response:
            _check_range(response, start, end, asset.size)
            remaining = end - start + 1
            with path.open("r+b") as output:
                output.seek(start)
                while True:
                    _check_cancel(cancel, abort)
                    chunk = response.read(min(CHUNK_BYTES, remaining + 1))
                    if not chunk:
                        break
                    if len(chunk) > remaining:
                        raise UpdateDownloadError("Update range exceeds its signed span.")
                    output.write(chunk)
                    remaining -= len(chunk)
                    with lock:
                        received[index] += len(chunk)
                if remaining:
                    raise UpdateDownloadError("Update range ended before its signed span.")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(fetch, index) for index in range(workers)}
        try:
            while pending:
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        future.result()
                    except Exception as error:
                        errors.append(error)
                        abort.set()
                _check_cancel(cancel)
                if not errors:
                    with lock:
                        count = sum(received)
                    progress.report(count)
        finally:
            abort.set()
    # Malformed data never triggers a successful fallback that hides the error.
    for error in errors:
        if not isinstance(error, (_RangesUnavailable, DownloadCancelled)):
            raise error
    for error in errors:
        if isinstance(error, _RangesUnavailable):
            raise error
    if errors:
        raise errors[0]
    with path.open("r+b") as output:
        output.flush()
        os.fsync(output.fileno())


def download_verified_package(request, asset, destination, callback, cancel, workers=4):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    final_path = destination / asset.filename
    progress = _Progress(callback, asset.size)
    _check_cancel(cancel)
    if _matches_signed_file(final_path, asset, cancel):
        _check_cancel(cancel)
        progress.finish()
        return final_path
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, dir=str(destination),
                prefix=asset.filename + ".", suffix=".part") as output:
            temporary_path = Path(output.name)
        parallel = workers > 1 and asset.size >= PARALLEL_MIN_BYTES
        if parallel:
            try:
                with request(asset.url, {"Range": "bytes=0-0"}) as response:
                    if getattr(response, "status", 200) == 200:
                        # A server ignoring Range already returned the full body.
                        _stream_response(response, temporary_path, asset, progress, cancel)
                        parallel = False
                    else:
                        _check_range(response, 0, 0, asset.size)
                        if len(response.read(2)) != 1:
                            raise UpdateDownloadError("Update range probe has an invalid size.")
                if parallel:
                    _parallel_ranges(request, temporary_path, asset, progress, cancel, workers)
            except HTTPError as error:
                if error.code != 416:
                    raise
                error.close()
                with request(asset.url) as response:
                    _stream_response(response, temporary_path, asset, progress, cancel)
            except _RangesUnavailable:
                _check_cancel(cancel)
                with request(asset.url) as response:
                    _stream_response(response, temporary_path, asset, progress, cancel)
        else:
            with request(asset.url) as response:
                _stream_response(response, temporary_path, asset, progress, cancel)
        _check_cancel(cancel)
        if not _matches_signed_file(temporary_path, asset, cancel):
            raise UpdateDownloadError("Update SHA-256 does not match the signed manifest.")
        _check_cancel(cancel)
        os.replace(str(temporary_path), str(final_path))
        temporary_path = None
        progress.finish()
        return final_path
    except UpdateDownloadError:
        raise
    except Exception as error:
        raise UpdateDownloadError("Unable to download update package: %s" % error) from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
