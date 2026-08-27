from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
