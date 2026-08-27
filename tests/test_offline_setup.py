from __future__ import annotations

import hashlib
import io
import os
import struct
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple
from unittest import mock

from b300_core import offline_setup
from b300_core.offline_setup import find_offline_bundle, install_offline_bundle


WINDOWS_ARCHIVE = "xpack-openocd-0.12.0-7-win32-x64.zip"
LINUX_ARCHIVE = "xpack-openocd-0.12.0-7-linux-x64.tar.gz"
XPACK_ROOT = "xpack-openocd-0.12.0-7"


def zip_xpack(entries=None) -> bytes:
    content = io.BytesIO()
    selected = entries or {"bin/openocd.exe": b"openocd"}
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in selected.items():
            archive.writestr("%s/%s" % (XPACK_ROOT, name), payload)
    return content.getvalue()


def tar_xpack(*, symlink: bool = False) -> bytes:
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w:gz") as archive:
        entries = {
            "%s/bin/openocd" % XPACK_ROOT: b"linux-openocd",
            "%s/scripts/target.cfg" % XPACK_ROOT: b"target",
        }
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        if symlink:
            info = tarfile.TarInfo("%s/scripts/alias.cfg" % XPACK_ROOT)
            info.type = tarfile.SYMTYPE
            info.linkname = "target.cfg"
            archive.addfile(info)
    return content.getvalue()


def write_bundle(root: Path, *, platform_name: str = "windows-x64",
                 xpack: Optional[bytes] = None, metadata_digest: Optional[str] = None,
                 extra_entries=None) -> Tuple[Path, str]:
    is_windows = platform_name == "windows-x64"
    archive_name = WINDOWS_ARCHIVE if is_windows else LINUX_ARCHIVE
    payload = xpack if xpack is not None else (zip_xpack() if is_windows else tar_xpack())
    digest = hashlib.sha256(payload).hexdigest()
    metadata = (
        "platform=%s\nversion=0.2.0\nopenocd=0.12.0-7\n"
        "openocd_archive=%s\nopenocd_sha256=%s\n" %
        (platform_name, archive_name, metadata_digest or digest)
    ).encode("ascii")
    bundle = root / (
        "b300-stlink-windows-x64.zip" if is_windows else
        "b300-stlink-linux-x64.tar.gz"
    )
    entries = {
        "BUNDLE-METADATA.txt": metadata,
        "vendor/packages/%s" % archive_name: payload,
    }
    entries.update(extra_entries or {})
    if is_windows:
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
    else:
        with tarfile.open(bundle, "w:gz") as archive:
            for name, data in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return bundle, digest


def fixture_tree_digest(platform_name: str, payload: bytes) -> str:
    files = {}
    if platform_name == "windows-x64":
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    relative = PurePosixPath(info.filename).relative_to(XPACK_ROOT).as_posix()
                    files[relative] = archive.read(info)
    else:
        links = []
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive:
                relative = PurePosixPath(member.name).relative_to(XPACK_ROOT).as_posix()
                if member.isfile():
                    stream = archive.extractfile(member)
                    files[relative] = stream.read() if stream is not None else b""
                elif member.issym():
                    links.append((relative, member.linkname))
        for relative, target in links:
            target_name = (PurePosixPath(relative).parent / target).as_posix()
            files[relative] = files[target_name]
    lines = [
        "%s  vendor/openocd/%s" % (hashlib.sha256(files[name]).hexdigest(), name)
        for name in sorted(files)
    ]
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


class OfflineSetupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows locking behavior")
    def test_windows_install_lock_retries_contention_until_available(self) -> None:
        lock_file = mock.Mock()
        lock_file.fileno.return_value = 42
        with mock.patch("msvcrt.locking", side_effect=[OSError(36, "busy"), None]) as locking, \
                mock.patch.object(time, "sleep", return_value=None):
            offline_setup._acquire_windows_lock(lock_file, timeout_seconds=1.0)
        self.assertEqual(locking.call_count, 2)

    def install_fixture(self, root: Path, *, platform_name="windows-x64", xpack=None):
        payload = xpack if xpack is not None else (
            zip_xpack() if platform_name == "windows-x64" else tar_xpack()
        )
        bundle, digest = write_bundle(root, platform_name=platform_name, xpack=payload)
        archive_name = WINDOWS_ARCHIVE if platform_name == "windows-x64" else LINUX_ARCHIVE
        trusted = {platform_name: (archive_name, digest)}
        tree_anchors = {platform_name: fixture_tree_digest(platform_name, payload)}
        with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted), \
                mock.patch.object(offline_setup, "TRUSTED_TREE_MANIFESTS", tree_anchors):
            executable = install_offline_bundle(
                bundle, root / "installed", platform_name=platform_name
            )
        return executable

    def test_windows_platform_falls_back_when_machine_is_empty(self) -> None:
        with mock.patch.object(offline_setup.platform, "system", return_value="Windows"), \
                mock.patch.object(offline_setup.platform, "machine", return_value=""), \
                mock.patch.object(
                    offline_setup.sysconfig, "get_platform", return_value="win-amd64"
                ):
            self.assertEqual(offline_setup.current_platform_name(), "windows-x64")

    def test_installs_openocd_only_from_trusted_xpack_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = self.install_fixture(Path(directory))
            self.assertEqual(executable.read_bytes(), b"openocd")

    def test_installs_linux_tar_and_dereferences_safe_file_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = self.install_fixture(
                root, platform_name="linux-x64", xpack=tar_xpack(symlink=True)
            )
            self.assertEqual(executable.read_bytes(), b"linux-openocd")
            alias = executable.parent.parent / "scripts" / "alias.cfg"
            self.assertEqual(alias.read_bytes(), b"target")
            self.assertFalse(alias.is_symlink())

    def test_tar_symlink_dereference_counts_against_expanded_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / LINUX_ARCHIVE
            package.write_bytes(tar_xpack(symlink=True))
            staged = root / "staged"
            staged.mkdir()
            with mock.patch.object(offline_setup, "MAX_EXPANDED_BYTES", 20):
                with self.assertRaisesRegex(ValueError, "expanded size"):
                    offline_setup._extract_tar_package(package, staged)

    def test_metadata_cannot_authorize_a_tampered_xpack_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trusted_payload = zip_xpack()
            trusted_digest = hashlib.sha256(trusted_payload).hexdigest()
            tampered = zip_xpack({"bin/openocd.exe": b"malware"})
            bundle, _ = write_bundle(root, xpack=tampered)
            trusted = {"windows-x64": (WINDOWS_ARCHIVE, trusted_digest)}
            with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted):
                with self.assertRaisesRegex(ValueError, "trusted SHA-256"):
                    install_offline_bundle(
                        bundle, root / "installed", platform_name="windows-x64"
                    )
            self.assertFalse((root / "installed" / "openocd-0.12.0-7").exists())

    def test_tree_manifest_anchor_rejects_tampering_even_if_manifest_is_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "openocd"
            executable = root / "bin" / "openocd.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"trusted")
            manifest = offline_setup.build_tree_manifest(root)
            trusted_digest = hashlib.sha256(manifest).hexdigest()
            (root / offline_setup.TREE_MANIFEST_NAME).write_bytes(manifest)
            anchors = {"windows-x64": trusted_digest}
            with mock.patch.object(offline_setup, "TRUSTED_TREE_MANIFESTS", anchors):
                self.assertTrue(offline_setup.verify_openocd_tree(root, "windows-x64"))
                executable.write_bytes(b"tampered")
                rewritten = offline_setup.build_tree_manifest(root)
                (root / offline_setup.TREE_MANIFEST_NAME).write_bytes(rewritten)
                self.assertFalse(offline_setup.verify_openocd_tree(root, "windows-x64"))

    def test_tree_verification_rejects_nested_file_named_like_root_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "openocd"
            executable = root / "bin" / "openocd.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"trusted")
            manifest = offline_setup.build_tree_manifest(root)
            anchor = hashlib.sha256(manifest).hexdigest()
            (root / offline_setup.TREE_MANIFEST_NAME).write_bytes(manifest)
            nested = root / "scripts" / offline_setup.TREE_MANIFEST_NAME
            nested.parent.mkdir()
            nested.write_bytes(b"unexpected")
            with mock.patch.object(
                offline_setup, "TRUSTED_TREE_MANIFESTS", {"windows-x64": anchor}
            ):
                self.assertFalse(offline_setup.verify_openocd_tree(root, "windows-x64"))

    def test_rejects_windows_backslash_drive_unc_and_posix_traversal(self) -> None:
        unsafe_names = (
            r"vendor\packages\..\escape.bin",
            r"C:\escape.bin",
            r"\\server\share\escape.bin",
            "vendor/packages/../../escape.bin",
        )
        for unsafe_name in unsafe_names:
            with self.subTest(unsafe_name=unsafe_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle, digest = write_bundle(root, extra_entries={unsafe_name: b"escape"})
                trusted = {"windows-x64": (WINDOWS_ARCHIVE, digest)}
                with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted):
                    with self.assertRaisesRegex(ValueError, "unsafe path"):
                        install_offline_bundle(
                            bundle, root / "installed", platform_name="windows-x64"
                        )
                self.assertFalse((root / "escape.bin").exists())

    def test_rejects_unsafe_path_inside_trusted_xpack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = zip_xpack({"../escape.bin": b"escape", "bin/openocd.exe": b"ok"})
            bundle, digest = write_bundle(root, xpack=payload)
            trusted = {"windows-x64": (WINDOWS_ARCHIVE, digest)}
            with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted):
                with self.assertRaisesRegex(ValueError, "unsafe path"):
                    install_offline_bundle(
                        bundle, root / "installed", platform_name="windows-x64"
                    )
            self.assertFalse((root / "escape.bin").exists())

    def test_rejects_posix_traversal_in_outer_linux_tar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, digest = write_bundle(
                root,
                platform_name="linux-x64",
                extra_entries={"vendor/packages/../../escape.bin": b"escape"},
            )
            trusted = {"linux-x64": (LINUX_ARCHIVE, digest)}
            with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted):
                with self.assertRaisesRegex(ValueError, "unsafe path"):
                    install_offline_bundle(bundle, root / "installed", "linux-x64")
            self.assertFalse((root / "escape.bin").exists())

    def test_rejects_archive_entry_count_and_expanded_size_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = zip_xpack({
                "bin/openocd.exe": b"ok", "one": b"1", "two": b"2"
            })
            bundle, digest = write_bundle(root, xpack=payload)
            trusted = {"windows-x64": (WINDOWS_ARCHIVE, digest)}
            with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted), \
                    mock.patch.object(offline_setup, "MAX_ARCHIVE_ENTRIES", 2):
                with self.assertRaisesRegex(ValueError, "too many entries"):
                    install_offline_bundle(bundle, root / "count", "windows-x64")
            with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted), \
                    mock.patch.object(offline_setup, "MAX_EXPANDED_BYTES", 3):
                with self.assertRaisesRegex(ValueError, "expanded size"):
                    install_offline_bundle(bundle, root / "size", "windows-x64")

    def test_zip_preflight_counts_central_headers_instead_of_trusting_eocd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "many.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for index in range(11):
                    archive.writestr("entry-%02d" % index, b"")
            data = bytearray(archive_path.read_bytes())
            eocd = data.rfind(b"PK\x05\x06")
            struct.pack_into("<HH", data, eocd + 8, 1, 1)
            archive_path.write_bytes(data)
            with mock.patch.object(offline_setup, "MAX_ARCHIVE_ENTRIES", 10):
                with self.assertRaisesRegex(ValueError, "too many entries"):
                    offline_setup._preflight_zip(archive_path)

    def test_failed_atomic_replace_restores_previous_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final_root = root / "installed" / "openocd-0.12.0-7"
            (final_root / "bin").mkdir(parents=True)
            old_executable = final_root / "bin" / "openocd.exe"
            old_executable.write_bytes(b"old-runtime")
            bundle, digest = write_bundle(root)
            trusted = {"windows-x64": (WINDOWS_ARCHIVE, digest)}
            tree_anchors = {"windows-x64": fixture_tree_digest("windows-x64", zip_xpack())}
            real_replace = os.replace
            calls = []

            def fail_second_replace(source, destination):
                calls.append((Path(source), Path(destination)))
                if len(calls) == 2:
                    raise OSError("simulated staged install failure")
                return real_replace(source, destination)

            with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted), \
                    mock.patch.object(offline_setup, "TRUSTED_TREE_MANIFESTS", tree_anchors), \
                    mock.patch.object(offline_setup.os, "replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(OSError, "simulated"):
                    install_offline_bundle(bundle, root / "installed", "windows-x64")
            self.assertEqual(old_executable.read_bytes(), b"old-runtime")

    def test_concurrent_setup_calls_leave_one_complete_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, digest = write_bundle(root)
            trusted = {"windows-x64": (WINDOWS_ARCHIVE, digest)}
            tree_anchors = {"windows-x64": fixture_tree_digest("windows-x64", zip_xpack())}
            results = []
            failures = []

            def run_install():
                try:
                    results.append(install_offline_bundle(
                        bundle, root / "installed", "windows-x64"
                    ))
                except Exception as error:
                    failures.append(error)

            with mock.patch.object(offline_setup, "TRUSTED_OPENOCD_PACKAGES", trusted), \
                    mock.patch.object(offline_setup, "TRUSTED_TREE_MANIFESTS", tree_anchors):
                threads = [threading.Thread(target=run_install) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)
            self.assertEqual(failures, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].read_bytes(), b"openocd")
            self.assertEqual(results[1].read_bytes(), b"openocd")

    def test_rejects_bundle_for_another_platform(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, _ = write_bundle(root)
            with self.assertRaisesRegex(ValueError, "platform"):
                install_offline_bundle(bundle, root / "installed", "linux-x64")

    def test_finds_platform_bundle_next_to_standalone_gui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _ = write_bundle(root)
            self.assertEqual(find_offline_bundle(root, "windows-x64"), expected)


if __name__ == "__main__":
    unittest.main()
