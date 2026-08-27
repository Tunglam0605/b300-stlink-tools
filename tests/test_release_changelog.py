import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.release.changelog import extract_release_notes, main


CHANGELOG = """# Changelog

## [Unreleased]

## [0.3.0] - 2026-08-27

### Added

- Direct downloads.

### Security

- Signed manifests.

## [0.2.0] - 2026-08-26

- Previous release.
"""


class ReleaseChangelogTests(unittest.TestCase):
    def test_extracts_only_requested_release_body(self) -> None:
        notes = extract_release_notes(CHANGELOG, "0.3.0")
        self.assertEqual(
            notes,
            "### Added\n\n- Direct downloads.\n\n"
            "### Security\n\n- Signed manifests.",
        )

    def test_rejects_missing_duplicate_and_empty_release(self) -> None:
        with self.assertRaisesRegex(ValueError, "not found"):
            extract_release_notes(CHANGELOG, "9.9.9")
        duplicated = CHANGELOG + "\n## [0.3.0] - 2026-08-28\n\n- Duplicate.\n"
        with self.assertRaisesRegex(ValueError, "more than once"):
            extract_release_notes(duplicated, "0.3.0")
        with self.assertRaisesRegex(ValueError, "empty"):
            extract_release_notes("# Changelog\n\n## [0.3.0] - 2026-08-27\n", "0.3.0")

    def test_unreleased_heading_is_not_a_release(self) -> None:
        with self.assertRaises(ValueError):
            extract_release_notes(CHANGELOG, "Unreleased")

    def test_cli_output_is_compatible_with_python_39_pathlib(self) -> None:
        original_write_text = Path.write_text

        def python39_write_text(path, data, encoding=None, errors=None):
            return original_write_text(path, data, encoding=encoding, errors=errors)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            changelog, output = root / "CHANGELOG.md", root / "notes.md"
            changelog.write_text(CHANGELOG, encoding="utf-8")
            with mock.patch.object(Path, "write_text", python39_write_text):
                self.assertEqual(main([
                    "0.3.0", "--changelog", str(changelog), "--output", str(output),
                ]), 0)
            self.assertEqual(output.read_text(encoding="utf-8"),
                             extract_release_notes(CHANGELOG, "0.3.0") + "\n")


if __name__ == "__main__":
    unittest.main()
