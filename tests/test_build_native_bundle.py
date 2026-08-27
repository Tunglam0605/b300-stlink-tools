from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
