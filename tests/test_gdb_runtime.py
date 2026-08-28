from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core.gdb_runtime import GdbRuntimeInfo, gdb_runtime_info, resolve_gdb


class GdbRuntimeTests(unittest.TestCase):
    def test_explicit_safe_path_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "arm-none-eabi-gdb"
            executable.write_bytes(b"gdb")
            self.assertEqual(resolve_gdb(str(executable)), str(executable))

    def test_environment_gdb_wins_before_bundled_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "configured-gdb"
            executable.write_bytes(b"gdb")
            with mock.patch.dict(os.environ, {"B300_GDB": str(executable)}, clear=False):
                self.assertEqual(resolve_gdb(), str(executable))

    def test_bundled_runtime_is_found_before_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "arm-none-eabi-gdb.exe" if os.name == "nt" else "arm-none-eabi-gdb"
            bundled = root / "vendor" / "gdb" / "bin" / name
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"gdb")
            with mock.patch("b300_core.gdb_runtime._runtime_roots", return_value=[root]), \
                 mock.patch("b300_core.gdb_runtime.shutil.which", return_value="/usr/bin/gdb"):
                self.assertEqual(resolve_gdb(), str(bundled))

    def test_path_arm_gdb_is_used_when_no_runtime_exists(self) -> None:
        with mock.patch("b300_core.gdb_runtime._runtime_roots", return_value=[]), \
             mock.patch("b300_core.gdb_runtime.shutil.which", side_effect=lambda name: "/opt/gdb" if name == "arm-none-eabi-gdb" else None):
            self.assertEqual(resolve_gdb(), "/opt/gdb")

    def test_linux_falls_back_to_gdb_multiarch(self) -> None:
        with mock.patch("b300_core.gdb_runtime._runtime_roots", return_value=[]), \
             mock.patch("b300_core.gdb_runtime.platform.system", return_value="Linux"), \
             mock.patch("b300_core.gdb_runtime.shutil.which", side_effect=lambda name: "/usr/bin/gdb-multiarch" if name == "gdb-multiarch" else None):
            self.assertEqual(resolve_gdb(), "/usr/bin/gdb-multiarch")

    def test_missing_gdb_has_actionable_error(self) -> None:
        with mock.patch("b300_core.gdb_runtime._runtime_roots", return_value=[]), \
             mock.patch("b300_core.gdb_runtime.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "GDB.*B300_GDB"):
                resolve_gdb()

    def test_runtime_info_normalizes_platform_and_reports_availability(self) -> None:
        info = GdbRuntimeInfo.from_path("/opt/gdb", platform_name="Windows")
        self.assertEqual(info.platform, "windows")
        self.assertEqual(info.path, "/opt/gdb")
        self.assertTrue(info.available)

    def test_runtime_info_reports_the_resolved_gdb_version(self) -> None:
        captured = {}
        completed = mock.Mock(stdout="GNU gdb (xPack ARM Embedded GCC) 15.2.1\n", returncode=0)
        with mock.patch("b300_core.gdb_runtime.resolve_gdb", return_value="/opt/gdb"), \
             mock.patch("b300_core.gdb_runtime.subprocess.run", \
                        side_effect=lambda command, **kwargs: captured.update(kwargs) or completed), \
             mock.patch("b300_core.process_startup.subprocess.CREATE_NO_WINDOW", 0x08000000,
                        create=True):
            info = gdb_runtime_info(platform_name="windows")
        self.assertTrue(info.available)
        self.assertEqual(info.version, "GNU gdb (xPack ARM Embedded GCC) 15.2.1")
        self.assertEqual(info.platform, "windows")
        self.assertTrue(captured["creationflags"] & 0x08000000)
        self.assertTrue(captured["capture_output"])
        self.assertTrue(captured["text"])
        self.assertEqual(captured["timeout"], 5.0)
        self.assertFalse(captured["shell"])

    def test_runtime_info_reports_unavailable_when_version_probe_cannot_start(self) -> None:
        with mock.patch("b300_core.gdb_runtime.resolve_gdb", return_value="/opt/gdb"), \
             mock.patch("b300_core.gdb_runtime.subprocess.run", side_effect=OSError("blocked")):
            info = gdb_runtime_info()
        self.assertFalse(info.available)
        self.assertIn("blocked", info.reason)


if __name__ == "__main__":
    unittest.main()
