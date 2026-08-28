from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import unittest
import hashlib
import tempfile
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

import b300_version
import b300_core
import b300_gui
import package_internal


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "build_native_bundle.py"


def builder():
    spec = importlib.util.spec_from_file_location("build_native_bundle", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeBundleTargetTests(unittest.TestCase):
    def test_windows_x64_uses_python_platform_when_machine_is_empty(self) -> None:
        selected = builder().target_for("windows", "", "win-amd64")
        self.assertEqual(selected[0], "windows-x64")
        self.assertEqual(selected[1], "win32-x64")

    def test_all_python_packaging_components_share_one_tool_version(self) -> None:
        module = builder()
        self.assertEqual(b300_core.__version__, b300_version.__version__)
        self.assertEqual(b300_gui.__version__, b300_version.__version__)
        self.assertEqual(module.TOOL_VERSION, b300_version.__version__)
        self.assertEqual(package_internal.TOOL_VERSION, b300_version.__version__)

    def test_version_checker_rejects_release_version_drift(self) -> None:
        accepted = subprocess.run(
            [sys.executable, "-m", "b300_version", "--check", b300_version.__version__],
            capture_output=True,
            text=True,
        )
        rejected = subprocess.run(
            [sys.executable, "-m", "b300_version", "--check", "9.9.9"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("does not match", rejected.stderr)

    def test_linux_packaging_script_runs_directly_from_repository_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "packaging/build_gui.py", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--version", result.stdout)

    def test_native_builder_rejects_upstream_checksum_outside_trust_anchor(self) -> None:
        module = builder()
        with self.assertRaisesRegex(RuntimeError, "trust anchor"):
            module.validate_trusted_package(
                "windows-x64",
                "xpack-openocd-0.12.0-7-win32-x64.zip",
                "0" * 64,
            )

    def test_release_names_separate_gui_and_cli_by_platform(self) -> None:
        module = builder()
        self.assertEqual(
            module.release_names("windows-x64"),
            (
                "B300-STLink-GUI-Windows-x64.zip",
                "B300-STLink-CLI-Windows-x64.zip",
            ),
        )
        self.assertEqual(
            module.release_names("linux-x64"),
            (
                "B300-STLink-GUI-Linux-x64.tar.gz",
                "B300-STLink-CLI-Linux-x64.tar.gz",
            ),
        )


    def test_native_builder_includes_trusted_bootloader_resources_for_every_bundle(self) -> None:
        module = builder()
        for platform_name in ("windows-x64", "linux-x64", "linux-arm64"):
            with self.subTest(platform=platform_name):
                names = {path.name for path in module.runtime_resources(platform_name)}
                self.assertIn("b300_bootloader_f407ze_com3_v00050001.hex", names)
                self.assertIn("b300_bootloader_manifest.json", names)

    def test_gdb_trust_anchors_match_the_pinned_xpack_archives(self) -> None:
        module = builder()
        self.assertEqual(module.TRUSTED_GDB_PACKAGES["windows-x64"], (
            "xpack-arm-none-eabi-gcc-15.2.1-1.1-win32-x64.zip",
            "bae6a3d1667697ce750c3b13d6d26d80973ecedc2cc87bf04869e83447fd93ea",
        ))
        self.assertEqual(module.TRUSTED_GDB_PACKAGES["linux-x64"][1],
                         "da6a49ad4003944b823c6c93702a8787c922ab34bd7e918ec0eaf6933a9b1ff6")
        self.assertEqual(module.TRUSTED_GDB_PACKAGES["linux-arm64"][1],
                         "67980c7990eba7bb7ffdf39699102effd70889f5ac427be19a8c8a6c5fab2972")
        with self.assertRaisesRegex(RuntimeError, "trust anchor"):
            module.validate_trusted_gdb_package("windows-x64", "wrong.zip", "0" * 64)

    def test_gdb_extraction_verifies_digest_and_preserves_license(self) -> None:
        module = builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "runtime.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("xpack-arm-none-eabi-gcc-test/bin/arm-none-eabi-gdb.exe", b"gdb")
                bundle.writestr("xpack-arm-none-eabi-gcc-test/LICENSE", b"upstream license")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with mock.patch.object(module, "TRUSTED_GDB_PACKAGES", {
                "windows-x64": (archive.name, digest),
            }):
                executable = module.extract_trusted_gdb_package(
                    archive, root / "gdb", "windows-x64"
                )
            self.assertEqual(executable, root / "gdb" / "bin" / "arm-none-eabi-gdb.exe")
            self.assertEqual((root / "gdb" / "LICENSE").read_bytes(), b"upstream license")

    def test_gdb_extraction_copies_safe_relative_tar_symlinks(self) -> None:
        module = builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "runtime.tar.gz"
            package_root = "xpack-arm-none-eabi-gcc-test"
            with tarfile.open(archive, "w:gz") as bundle:
                for name, data in (
                    ("bin/arm-none-eabi-gdb", b"gdb"),
                    ("lib/libgdb.so.1", b"library"),
                ):
                    info = tarfile.TarInfo(package_root + "/" + name)
                    info.size = len(data)
                    bundle.addfile(info, io.BytesIO(data))
                link = tarfile.TarInfo(package_root + "/lib/libgdb.so")
                link.type = tarfile.SYMTYPE
                link.linkname = "libgdb.so.1"
                bundle.addfile(link)
                hard_link = tarfile.TarInfo(package_root + "/lib/libgdb-hard.so")
                hard_link.type = tarfile.LNKTYPE
                hard_link.linkname = package_root + "/lib/libgdb.so.1"
                bundle.addfile(hard_link)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with mock.patch.object(module, "TRUSTED_GDB_PACKAGES", {
                "linux-x64": (archive.name, digest),
            }):
                module.extract_trusted_gdb_package(archive, root / "gdb", "linux-x64")
            self.assertEqual((root / "gdb" / "lib" / "libgdb.so").read_bytes(), b"library")
            self.assertEqual((root / "gdb" / "lib" / "libgdb-hard.so").read_bytes(), b"library")


if __name__ == "__main__":
    unittest.main()
