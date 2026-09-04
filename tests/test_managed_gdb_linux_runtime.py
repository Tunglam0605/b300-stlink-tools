from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b300_core.managed_gdb_bundle import stage_managed_gdb_runtime


class ManagedGdbLinuxRuntimeTests(unittest.TestCase):
    def test_linux_staging_preserves_top_level_libexec_shared_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "xpack"
            destination = root / "managed"
            (source / "bin").mkdir(parents=True)
            (source / "libexec" / "gcc").mkdir(parents=True)
            (source / "distro-info" / "licenses").mkdir(parents=True)

            gdb = source / "bin" / "arm-none-eabi-gdb"
            gdb.write_bytes(b"gdb")
            (source / "libexec" / "libiconv.so.2").write_bytes(b"iconv")
            (source / "libexec" / "libexpat.so.1").write_bytes(b"expat")
            (source / "libexec" / "gcc" / "compiler-helper.so").write_bytes(b"gcc")
            (source / "distro-info" / "licenses" / "COPYING").write_text(
                "license", encoding="utf-8"
            )

            staged = stage_managed_gdb_runtime(source, destination, "linux-arm64")

            self.assertEqual(staged, destination / "bin" / "arm-none-eabi-gdb")
            self.assertEqual(
                (destination / "libexec" / "libiconv.so.2").read_bytes(), b"iconv"
            )
            self.assertEqual(
                (destination / "libexec" / "libexpat.so.1").read_bytes(), b"expat"
            )
            self.assertFalse(
                (destination / "libexec" / "gcc" / "compiler-helper.so").exists()
            )

    def test_linux_staging_does_not_copy_compiler_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "xpack"
            destination = root / "managed"
            (source / "bin").mkdir(parents=True)
            (source / "distro-info" / "licenses").mkdir(parents=True)
            (source / "bin" / "arm-none-eabi-gdb").write_bytes(b"gdb")
            (source / "bin" / "arm-none-eabi-gcc").write_bytes(b"gcc")
            (source / "distro-info" / "licenses" / "COPYING").write_text(
                "license", encoding="utf-8"
            )

            stage_managed_gdb_runtime(source, destination, "linux-x64")

            self.assertTrue((destination / "bin" / "arm-none-eabi-gdb").is_file())
            self.assertFalse((destination / "bin" / "arm-none-eabi-gcc").exists())


if __name__ == "__main__":
    unittest.main()
