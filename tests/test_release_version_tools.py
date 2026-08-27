import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.release.version_tools import (
    parse_semver,
    read_source_version,
    replace_source_version,
)


class ReleaseVersionToolsTests(unittest.TestCase):
    def test_semver_order_uses_numeric_components(self) -> None:
        self.assertLess(parse_semver("0.9.9"), parse_semver("0.10.0"))
        self.assertEqual(parse_semver("12.34.56"), (12, 34, 56))

    def test_semver_rejects_noncanonical_versions(self) -> None:
        for value in ("v0.3.0", "00.3.0", "0.03.0", "0.3", "0.3.0-rc1", " 0.3.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_semver(value)

    def test_replace_source_version_changes_only_version_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "b300_version.py"
            source.write_text(
                '"""Version."""\n\n__version__ = "0.2.0"\n\nVALUE = "0.2.0"\n',
                encoding="utf-8",
            )
            replace_source_version(source, "0.3.0")
            self.assertEqual(read_source_version(source), "0.3.0")
            self.assertIn('VALUE = "0.2.0"', source.read_text(encoding="utf-8"))

    def test_replace_rejects_ambiguous_source_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "b300_version.py"
            source.write_text(
                '__version__ = "0.1.0"\n__version__ = "0.2.0"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exactly one"):
                replace_source_version(source, "0.3.0")

    def test_validate_cli_requires_tag_and_source_to_match(self) -> None:
        root = Path(__file__).resolve().parents[1]
        current = read_source_version(root / "b300_version.py")
        passed = subprocess.run(
            [sys.executable, "-m", "scripts.release.validate_version", "--check-tag", "v" + current],
            cwd=str(root), capture_output=True, text=True, check=False,
        )
        failed = subprocess.run(
            [sys.executable, "-m", "scripts.release.validate_version", "--check-tag", "v9.9.9"],
            cwd=str(root), capture_output=True, text=True, check=False,
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("does not match", failed.stderr)


if __name__ == "__main__":
    unittest.main()
