from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import package_internal


ROOT = Path(__file__).resolve().parents[1]


def builder():
    source = ROOT / "build_native_bundle.py"
    spec = importlib.util.spec_from_file_location("build_native_bundle_v018_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class V018ManagedGdbReleaseTests(unittest.TestCase):
    def test_prepare_managed_gdb_verifies_pin_stages_compact_runtime_and_smokes(self) -> None:
        module = builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_bytes = b"trusted-xpack-archive"
            digest = hashlib.sha256(archive_bytes).hexdigest()
            filename = "xpack-arm-none-eabi-gcc-test-win32-x64.zip"

            def fake_fetch(url: str, output: Path) -> None:
                if url.endswith(".sha"):
                    output.write_text(digest + "  " + filename + "\n", encoding="utf-8")
                else:
                    output.write_bytes(archive_bytes)

            def fake_extract(_archive: Path, destination: Path, _platform: str):
                executable = destination / "bin" / "arm-none-eabi-gdb.exe"
                executable.parent.mkdir(parents=True)
                executable.write_bytes(b"full gdb")
                return executable

            def fake_stage(_source: Path, destination: Path, _platform: str):
                executable = destination / "bin" / "arm-none-eabi-gdb.exe"
                executable.parent.mkdir(parents=True)
                executable.write_bytes(b"compact gdb")
                return executable

            with mock.patch.object(module, "TRUSTED_GDB_PACKAGES", {
                "windows-x64": (filename, digest),
            }), mock.patch.object(module, "fetch", side_effect=fake_fetch), \
                 mock.patch.object(module, "extract_trusted_gdb_package", side_effect=fake_extract), \
                 mock.patch.object(module, "stage_managed_gdb_runtime", side_effect=fake_stage) as stage, \
                 mock.patch.object(module, "smoke_test_managed_gdb", return_value="GNU gdb test") as smoke:
                archive, actual, runtime = module.prepare_managed_gdb(root, "windows-x64")

            self.assertEqual(archive.name, filename)
            self.assertEqual(actual, digest)
            self.assertTrue((runtime / "bin" / "arm-none-eabi-gdb.exe").is_file())
            stage.assert_called_once()
            smoke.assert_called_once_with(runtime / "bin" / "arm-none-eabi-gdb.exe")

    def test_prepare_managed_gdb_fails_closed_on_upstream_checksum_mismatch(self) -> None:
        module = builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            filename = "runtime.zip"
            actual = hashlib.sha256(b"archive").hexdigest()

            def fake_fetch(url: str, output: Path) -> None:
                if url.endswith(".sha"):
                    output.write_text("0" * 64 + "  runtime.zip\n", encoding="utf-8")
                else:
                    output.write_bytes(b"archive")

            with mock.patch.object(module, "TRUSTED_GDB_PACKAGES", {
                "windows-x64": (filename, actual),
            }), mock.patch.object(module, "fetch", side_effect=fake_fetch):
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    module.prepare_managed_gdb(root, "windows-x64")

    def test_package_internal_embeds_compact_gdb_tree_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "b300-stlink-gui.exe"
            executable.write_bytes(b"app")
            bootstrap = root / "install.ps1"
            bootstrap.write_text("Write-Host ok", encoding="utf-8")
            openocd = root / "openocd"
            (openocd / "bin").mkdir(parents=True)
            (openocd / "bin" / "openocd.exe").write_bytes(b"openocd")
            openocd_package = root / "openocd.zip"
            openocd_package.write_bytes(b"package")
            gdb = root / "gdb"
            (gdb / "bin").mkdir(parents=True)
            (gdb / "bin" / "arm-none-eabi-gdb.exe").write_bytes(b"gdb")
            (gdb / "B300-MANAGED-GDB.txt").write_text("notice", encoding="utf-8")
            resource = root / "LICENSE"
            resource.write_text("B300 license", encoding="utf-8")
            output = root / "bundle.zip"
            openocd_manifest = b"openocd manifest\n"
            manifest_digest = hashlib.sha256(openocd_manifest).hexdigest()

            args = [
                "--flavor", "gui",
                "--executable", str(executable),
                "--resource", str(resource),
                "--openocd-root", str(openocd),
                "--bootstrap", str(bootstrap),
                "--output", str(output),
                "--platform", "windows-x64",
                "--version", "0.18.0-test",
                "--openocd-archive", openocd_package.name,
                "--openocd-sha256", "1" * 64,
                "--openocd-package", str(openocd_package),
                "--gdb-root", str(gdb),
                "--gdb-archive", "xpack-arm-none-eabi-gcc-test.zip",
                "--gdb-sha256", "2" * 64,
                "--internal-distribution-approved",
            ]
            with mock.patch.object(package_internal, "openocd_manifest", return_value=openocd_manifest), \
                 mock.patch.dict(package_internal.TRUSTED_TREE_MANIFESTS, {
                     "windows-x64": manifest_digest,
                 }, clear=False):
                self.assertEqual(package_internal.main(args), 0)

            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                metadata = archive.read("BUNDLE-METADATA.txt").decode("ascii")
            self.assertIn("vendor/gdb/bin/arm-none-eabi-gdb.exe", names)
            self.assertIn("vendor/gdb/B300-MANAGED-GDB.txt", names)
            self.assertNotIn("vendor/gdb/bin/arm-none-eabi-gcc.exe", names)
            self.assertIn("gdb=15.2.1-1.1", metadata)
            self.assertIn("gdb_sha256=" + "2" * 64, metadata.lower())


if __name__ == "__main__":
    unittest.main()
