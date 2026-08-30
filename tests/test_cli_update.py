from __future__ import annotations

import hashlib
import importlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional
from unittest import mock

from b300_cli.parser import parse_args
from b300_core.update_channel import UpdateChannel, cli_channel_endpoints
from b300_core.updater import DEFAULT_MANIFEST_URL, DEFAULT_SIGNATURE_URL
from b300_version import __version__
from tests.test_release_manifest import TEST_PUBLIC_KEY, sign_message


PAYLOAD = b"verified CLI update archive"
WINDOWS_KEY = "windows-x64-cli"
WINDOWS_FILE = "B300-STLink-CLI-Windows-x64.zip"
LINUX_X64_KEY = "linux-x64-cli"
LINUX_X64_FILE = "B300-STLink-CLI-Linux-x64.tar.gz"

CLI_MANIFEST_URL, CLI_SIGNATURE_URL = cli_channel_endpoints(UpdateChannel.RELEASE)


def _next_patch(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return "%d.%d.%d" % (major, minor, patch + 1)


MAIN_NEXT_VERSION = _next_patch(__version__)


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class CancelAfterReadResponse(FakeResponse):
    def __init__(self, data: bytes, cancel: threading.Event):
        super().__init__(data)
        self.cancel = cancel

    def read(self, size=-1):
        data = super().read(size)
        if data:
            self.cancel.set()
        return data


class FakeOpener:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append(request.full_url)
        response = self.responses[request.full_url]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, FakeResponse):
            return response
        return FakeResponse(response)


def signed_manifest(
        *, version: str = "0.5.4", platform_key: str = WINDOWS_KEY,
        filename: str = WINDOWS_FILE, payload: bytes = PAYLOAD,
        sha256: Optional[str] = None,
        size: Optional[int] = None) -> tuple[bytes, bytes, str]:
    asset_url = (
        "https://github.com/Tunglam0605/b300-stlink-tools/releases/download/"
        "v%s/%s" % (version, filename)
    )
    value = {
        "notes": "Signed CLI fixture",
        "platforms": {
            platform_key: {
                "file": filename,
                "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
                "size": len(payload) if size is None else size,
                "url": asset_url,
            },
        },
        "product": "B300 ST-Link Tools",
        "published_at": "2026-08-28T12:00:00Z",
        "release_page": (
            "https://github.com/Tunglam0605/b300-stlink-tools/releases/tag/v%s" % version
        ),
        "schema_version": 1,
        "version": version,
    }
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    return data, sign_message(data), asset_url


class CliUpdateParserTests(unittest.TestCase):
    def test_parser_exposes_check_and_directory_download_commands(self) -> None:
        try:
            check = parse_args(["update", "check", "--json"])
            download = parse_args(["update", "download", "--dest", "cache", "--json"])
        except SystemExit as error:
            self.fail("update command is missing from the parser: %s" % error)

        self.assertEqual((check.command, check.update_command), ("update", "check"))
        self.assertTrue(check.json)
        self.assertEqual((download.command, download.update_command), ("update", "download"))
        self.assertEqual(str(download.dest), "cache")
        self.assertTrue(download.json)


class CliUpdateTests(unittest.TestCase):
    def _module(self, name: str):
        self.assertIsNotNone(
            importlib.util.find_spec(name), "%s has not been implemented" % name,
        )
        return importlib.import_module(name)

    def _runtime(self, manifest: bytes, signature: bytes, asset_url: str,
                 payload: bytes = PAYLOAD, *, system: str = "Windows",
                 machine: str = "AMD64"):
        core = self._module("b300_core.cli_update")
        opener = FakeOpener({
            CLI_MANIFEST_URL: manifest,
            CLI_SIGNATURE_URL: signature,
            asset_url: payload,
        })
        runtime = core.build_cli_update_runtime(
            system=system,
            machine=machine,
            public_key=TEST_PUBLIC_KEY,
            open_url=opener,
        )
        return runtime, opener

    def _run_handler(self, argv, runtime, **kwargs):
        commands = self._module("b300_cli.update_commands")
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = commands.run_update_command(
                parse_args(argv), "0.5.3", runtime=runtime, **kwargs
            )
        return code, json.loads(output.getvalue())

    def _run_main(self, argv, runtime):
        commands = self._module("b300_cli.update_commands")
        tool = importlib.import_module("b300_stlink")
        self.assertTrue(
            hasattr(tool, "run_update_command"),
            "b300_stlink.main does not dispatch update commands",
        )
        output = io.StringIO()
        with mock.patch.object(
            commands, "build_cli_update_runtime", return_value=runtime,
        ), redirect_stdout(output), redirect_stderr(output):
            code = tool.main(argv)
        return code, json.loads(output.getvalue())

    def test_runtime_uses_embedded_key_and_cli_only_platform(self) -> None:
        core = self._module("b300_core.cli_update")
        from b300_core.update_public_key import MINISIGN_PUBLIC_KEY

        runtime = core.build_cli_update_runtime(system="Windows", machine="x86_64")

        self.assertEqual(runtime.platform.value, WINDOWS_KEY)
        self.assertEqual(runtime.client.platform_name, WINDOWS_KEY)
        self.assertEqual(runtime.client.public_key, MINISIGN_PUBLIC_KEY)
        self.assertEqual(runtime.client.manifest_url, CLI_MANIFEST_URL)
        self.assertEqual(runtime.client.signature_url, CLI_SIGNATURE_URL)
        self.assertNotEqual(runtime.client.manifest_url, DEFAULT_MANIFEST_URL)
        self.assertNotEqual(runtime.client.signature_url, DEFAULT_SIGNATURE_URL)

    def test_default_cache_paths_are_platform_specific(self) -> None:
        core = self._module("b300_core.cli_update")
        self.assertEqual(
            core.default_cli_update_cache(
                WINDOWS_KEY, environ={"LOCALAPPDATA": "C:/Users/test/AppData/Local"},
                home=Path("C:/Users/test"),
            ),
            Path("C:/Users/test/AppData/Local/B300-STLink/updates"),
        )
        self.assertEqual(
            core.default_cli_update_cache(
                LINUX_X64_KEY, environ={"XDG_CACHE_HOME": "/cache"},
                home=Path("/home/test"),
            ),
            Path("/cache/b300-stlink/updates"),
        )
        self.assertEqual(
            core.default_cli_update_cache(
                "linux-arm64-cli", environ={}, home=Path("/home/test"),
            ),
            Path("/home/test/.cache/b300-stlink/updates"),
        )

    def test_check_reports_signed_current_latest_and_cli_asset_without_html(self) -> None:
        manifest, signature, asset_url = signed_manifest(version=MAIN_NEXT_VERSION)
        runtime, opener = self._runtime(manifest, signature, asset_url)

        code, value = self._run_main(["update", "check", "--json"], runtime)

        self.assertEqual(code, 0)
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["command"], "update check")
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["current_version"], __version__)
        self.assertEqual(value["latest_version"], MAIN_NEXT_VERSION)
        self.assertTrue(value["update_available"])
        self.assertEqual(value["platform"], WINDOWS_KEY)
        self.assertEqual(value["asset"]["filename"], WINDOWS_FILE)
        self.assertEqual(value["asset"]["size"], len(PAYLOAD))
        self.assertEqual(value["asset"]["sha256"], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(opener.requests, [CLI_MANIFEST_URL, CLI_SIGNATURE_URL])

    def test_check_returns_zero_for_current_and_newer_local_versions(self) -> None:
        for signed_version in ("0.5.3", "0.5.2"):
            with self.subTest(signed_version=signed_version):
                manifest, signature, asset_url = signed_manifest(version=signed_version)
                runtime, _opener = self._runtime(manifest, signature, asset_url)
                code, value = self._run_handler(
                    ["update", "check", "--json"], runtime,
                )
                self.assertEqual(code, 0)
                self.assertEqual(value["current_version"], "0.5.3")
                self.assertEqual(value["latest_version"], signed_version)
                self.assertFalse(value["update_available"])
                self.assertIsNone(value["asset"])

    def test_download_treats_dest_as_directory_and_preserves_signed_filename(self) -> None:
        manifest, signature, asset_url = signed_manifest(version=MAIN_NEXT_VERSION)
        runtime, opener = self._runtime(manifest, signature, asset_url)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "chosen-directory"

            code, value = self._run_main([
                "update", "download", "--dest", str(destination), "--json",
            ], runtime)

            final_path = destination / WINDOWS_FILE
            self.assertEqual(code, 0)
            self.assertEqual(final_path.read_bytes(), PAYLOAD)
            self.assertEqual(value["latest_version"], MAIN_NEXT_VERSION)
            self.assertEqual(value["asset"]["filename"], WINDOWS_FILE)
            self.assertEqual(value["asset"]["size"], len(PAYLOAD))
            self.assertEqual(value["asset"]["sha256"], hashlib.sha256(PAYLOAD).hexdigest())
            self.assertEqual(value["path"], str(final_path.resolve()))
            self.assertEqual(
                opener.requests,
                [CLI_MANIFEST_URL, CLI_SIGNATURE_URL, asset_url],
            )

    def test_download_defaults_to_standard_user_cache(self) -> None:
        manifest, signature, asset_url = signed_manifest()
        runtime, _opener = self._runtime(manifest, signature, asset_url)
        with tempfile.TemporaryDirectory() as temp:
            cache_root = Path(temp)
            code, value = self._run_handler(
                ["update", "download", "--json"], runtime,
                environ={"LOCALAPPDATA": str(cache_root)}, home=cache_root,
            )
            expected = (cache_root / "B300-STLink" / "updates" / WINDOWS_FILE).resolve()
            self.assertEqual(code, 0)
            self.assertEqual(Path(value["path"]), expected)
            self.assertEqual(expected.read_bytes(), PAYLOAD)

    def test_cancelled_download_removes_every_partial(self) -> None:
        manifest, signature, asset_url = signed_manifest()
        runtime, opener = self._runtime(manifest, signature, asset_url)
        cancel = threading.Event()
        opener.responses[asset_url] = CancelAfterReadResponse(PAYLOAD, cancel)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "cancelled"
            code, value = self._run_handler([
                "update", "download", "--dest", str(destination), "--json",
            ], runtime, cancel=cancel)
            self.assertNotEqual(code, 0)
            self.assertEqual(value["reason_code"], "UPDATE_CANCELLED")
            self.assertTrue(destination.is_dir())
            self.assertEqual(list(destination.iterdir()), [])
            self.assertEqual(
                opener.requests,
                [CLI_MANIFEST_URL, CLI_SIGNATURE_URL, asset_url],
            )

    def test_signed_unsafe_filename_is_rejected_before_asset_download(self) -> None:
        manifest, signature, asset_url = signed_manifest(filename="../unsafe.zip")
        runtime, opener = self._runtime(manifest, signature, asset_url)

        code, value = self._run_handler(["update", "download", "--json"], runtime)

        self.assertNotEqual(code, 0)
        self.assertEqual(value["reason_code"], "UPDATE_SECURITY_FAILURE")
        self.assertEqual(opener.requests, [CLI_MANIFEST_URL, CLI_SIGNATURE_URL])

    def test_signed_manifest_without_detected_platform_is_rejected(self) -> None:
        manifest, signature, asset_url = signed_manifest(
            platform_key=LINUX_X64_KEY, filename=LINUX_X64_FILE,
        )
        runtime, opener = self._runtime(manifest, signature, asset_url)

        code, value = self._run_handler(["update", "check", "--json"], runtime)

        self.assertNotEqual(code, 0)
        self.assertEqual(value["reason_code"], "UPDATE_SECURITY_FAILURE")
        self.assertEqual(opener.requests, [CLI_MANIFEST_URL, CLI_SIGNATURE_URL])

    def test_invalid_signature_is_rejected_before_manifest_fields_are_trusted(self) -> None:
        manifest, signature, asset_url = signed_manifest()
        untrusted_manifest = manifest.replace(b"0.5.4", b"9.5.4")
        runtime, opener = self._runtime(untrusted_manifest, signature, asset_url)

        code, value = self._run_handler(["update", "check", "--json"], runtime)

        self.assertNotEqual(code, 0)
        self.assertEqual(value["reason_code"], "UPDATE_SECURITY_FAILURE")
        self.assertEqual(opener.requests, [CLI_MANIFEST_URL, CLI_SIGNATURE_URL])

    def test_sha_mismatch_fails_closed_and_removes_partial(self) -> None:
        manifest, signature, asset_url = signed_manifest(sha256="0" * 64)
        runtime, _opener = self._runtime(manifest, signature, asset_url)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "bad-sha"
            code, value = self._run_handler([
                "update", "download", "--dest", str(destination), "--json",
            ], runtime)
            self.assertNotEqual(code, 0)
            self.assertEqual(value["reason_code"], "UPDATE_DOWNLOAD_FAILED")
            self.assertEqual(list(destination.iterdir()), [])

    def test_size_mismatch_fails_closed_and_removes_partial(self) -> None:
        manifest, signature, asset_url = signed_manifest(size=len(PAYLOAD) + 1)
        runtime, _opener = self._runtime(manifest, signature, asset_url)
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "bad-size"
            code, value = self._run_handler([
                "update", "download", "--dest", str(destination), "--json",
            ], runtime)
            self.assertNotEqual(code, 0)
            self.assertEqual(value["reason_code"], "UPDATE_DOWNLOAD_FAILED")
            self.assertEqual(list(destination.iterdir()), [])

    def test_unsupported_platform_is_one_stable_failure_snapshot(self) -> None:
        commands = self._module("b300_cli.update_commands")
        output = io.StringIO()
        with mock.patch(
            "b300_core.update_platform.platform.system", return_value="Darwin",
        ), mock.patch(
            "b300_core.update_platform.platform.machine", return_value="x86_64",
        ), redirect_stdout(output), redirect_stderr(output):
            code = commands.run_update_command(
                parse_args(["update", "check", "--json"]), "0.5.3",
            )
        value = json.loads(output.getvalue())
        self.assertNotEqual(code, 0)
        self.assertEqual(value["status"], "error")
        self.assertEqual(value["reason_code"], "UNSUPPORTED_UPDATE_PLATFORM")


if __name__ == "__main__":
    unittest.main()
