"""Focused checks for release-only Windows installer verification."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "release" / "verify_windows_installer.py"


def verifier_module():
    spec = importlib.util.spec_from_file_location("verify_windows_installer", SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
                path.chmod(path.stat().st_mode | stat.S_IREAD)

            module.remove_owned_installation(evidence, installed)

            self.assertFalse(installed.exists())


if __name__ == "__main__":
    unittest.main()
