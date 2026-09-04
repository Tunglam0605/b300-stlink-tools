from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core.managed_gdb_bundle import (
    NOTICE_NAME,
    smoke_test_managed_gdb,
    stage_managed_gdb_runtime,
)


class ManagedGdbBundleTests(unittest.TestCase):
    def _source(self, root: Path, platform_name: str) -> Path:
        source = root / "source"
        bin_root = source / "bin"
        bin_root.mkdir(parents=True)
        gdb_name = "arm-none-eabi-gdb.exe" if platform_name == "windows-x64" else "arm-none-eabi-gdb"
        gcc_name = "arm-none-eabi-gcc.exe" if platform_name == "windows-x64" else "arm-none-eabi-gcc"
        (bin_root / gdb_name).write_bytes(b"gdb")
        (bin_root / gcc_name).write_bytes(b"compiler-must-not-ship")
        (source / "README.md").write_text("upstream readme", encoding="utf-8")
        license_root = source / "distro-info" / "licenses"
        license_root.mkdir(parents=True)
        (license_root / "gdb.txt").write_text("license", encoding="utf-8")
        return source

    def test_windows_runtime_keeps_gdb_dlls_and_not_compiler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root, "windows-x64")
            (source / "bin" / "libhost.dll").write_bytes(b"dll")
            dlls = source / "bin" / "DLLs"
            dlls.mkdir()
            (dlls / "helper.pyd").write_bytes(b"pyd")
            staged = stage_managed_gdb_runtime(source, root / "runtime", "windows-x64")
            runtime = root / "runtime"
            self.assertEqual(staged, runtime / "bin" / "arm-none-eabi-gdb.exe")
            self.assertTrue((runtime / "bin" / "libhost.dll").is_file())
            self.assertTrue((runtime / "bin" / "DLLs" / "helper.pyd").is_file())
            self.assertFalse((runtime / "bin" / "arm-none-eabi-gcc.exe").exists())
            self.assertTrue((runtime / "distro-info" / "licenses" / "gdb.txt").is_file())
            self.assertTrue((runtime / NOTICE_NAME).is_file())

    def test_linux_runtime_keeps_host_shared_objects_and_not_gcc_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root, "linux-x64")
            (source / "bin" / "libpython3.13.so.1.0").write_bytes(b"so")
            lib_root = source / "lib"
            lib_root.mkdir()
            (lib_root / "libcc1.so.0").write_bytes(b"so")
            gcc_root = lib_root / "gcc" / "arm-none-eabi" / "15.2.1"
            gcc_root.mkdir(parents=True)
            (gcc_root / "libgcc.a").write_bytes(b"huge compiler data")
            stage_managed_gdb_runtime(source, root / "runtime", "linux-x64")
            runtime = root / "runtime"
            self.assertTrue((runtime / "bin" / "libpython3.13.so.1.0").is_file())
            self.assertTrue((runtime / "lib" / "libcc1.so.0").is_file())
            self.assertFalse((runtime / "lib" / "gcc").exists())
            self.assertFalse((runtime / "bin" / "arm-none-eabi-gcc").exists())

    def test_gdb_specific_data_is_preserved_without_gcc_share_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root, "linux-arm64")
            gdb_data = source / "share" / "gdb"
            gdb_data.mkdir(parents=True)
            (gdb_data / "system-gdbinit").write_text("set confirm off", encoding="utf-8")
            gcc_data = source / "share" / "gcc-15.2.1"
            gcc_data.mkdir(parents=True)
            (gcc_data / "python.py").write_text("gcc", encoding="utf-8")
            stage_managed_gdb_runtime(source, root / "runtime", "linux-arm64")
            runtime = root / "runtime"
            self.assertTrue((runtime / "share" / "gdb" / "system-gdbinit").is_file())
            self.assertFalse((runtime / "share" / "gcc-15.2.1").exists())

    def test_destination_must_be_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root, "linux-x64")
            destination = root / "runtime"
            destination.mkdir()
            (destination / "unexpected").write_text("data", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be empty"):
                stage_managed_gdb_runtime(source, destination, "linux-x64")

    def test_smoke_test_requires_successful_architecture_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "arm-none-eabi-gdb"
            executable.write_bytes(b"gdb")
            architecture = mock.Mock(returncode=0, stdout="The target architecture is set to auto.\n", stderr="")
            version = mock.Mock(returncode=0, stdout="GNU gdb 15.2.1\n", stderr="")
            with mock.patch("b300_core.managed_gdb_bundle.subprocess.run", side_effect=[architecture, version]) as run:
                label = smoke_test_managed_gdb(executable)
            self.assertEqual(label, "GNU gdb 15.2.1")
            self.assertEqual(run.call_count, 2)
            self.assertFalse(run.call_args_list[0].kwargs["shell"])

    def test_smoke_test_fails_closed_when_gdb_cannot_start_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "arm-none-eabi-gdb"
            executable.write_bytes(b"gdb")
            failed = mock.Mock(returncode=1, stdout="", stderr="missing runtime")
            with mock.patch("b300_core.managed_gdb_bundle.subprocess.run", return_value=failed):
                with self.assertRaisesRegex(RuntimeError, "smoke test"):
                    smoke_test_managed_gdb(executable)


if __name__ == "__main__":
    unittest.main()
