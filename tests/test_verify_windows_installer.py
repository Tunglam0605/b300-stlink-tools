"""Focused checks for release-only Windows installer verification."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from ctypes import wintypes
import ctypes


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "release" / "verify_windows_installer.py"
FILE_ATTRIBUTE_READONLY = 0x1
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def verifier_module():
    spec = importlib.util.spec_from_file_location("verify_windows_installer", SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def windows_attributes(path: Path) -> int:
    value = ctypes.windll.kernel32.GetFileAttributesW(wintypes.LPCWSTR(str(path)))
    if value == INVALID_FILE_ATTRIBUTES:
        raise OSError("GetFileAttributesW failed for " + str(path))
    return value


@unittest.skipUnless(os.name == "nt", "Windows-only file attribute behavior")
class WindowsInstallerVerifierTests(unittest.TestCase):
    def test_cleanup_removes_owned_tree_with_readonly_directories(self) -> None:
        module = verifier_module()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence"
            installed = evidence / "installed"
            firmware = installed / "resources" / "firmware"
            firmware.mkdir(parents=True)
            (firmware / "image.hex").write_bytes(b"fixture")
            for path in (firmware.parent, firmware):
                path.chmod(stat.S_IREAD)
                self.assertTrue(windows_attributes(path) & FILE_ATTRIBUTE_READONLY)

            module.remove_owned_installation(evidence, installed)

            self.assertFalse(installed.exists())

    def test_cleanup_rejects_path_outside_evidence_root(self) -> None:
        module = verifier_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            evidence.mkdir()
            outside = root / "outside"
            outside.mkdir()

            with self.assertRaisesRegex(RuntimeError, "Unsafe cleanup path"):
                module.remove_owned_installation(evidence, outside)

            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
