import unittest
from unittest import mock

from b300_core.release_notes import current_release_notes


class RuntimeReleaseNotesTests(unittest.TestCase):
    def test_v0150_has_bundled_whats_new_fallback(self) -> None:
        with mock.patch(
            "b300_core.release_notes.bundled_changelog_path",
            side_effect=OSError("bundled changelog unavailable"),
        ):
            notes = current_release_notes("0.15.0")
        self.assertIn("Engineering Debug Workstation", notes)
        self.assertIn("One-login Client SSH", notes)
        self.assertIn("Safety", notes)

    def test_unknown_release_still_fails_without_changelog(self) -> None:
        with mock.patch(
            "b300_core.release_notes.bundled_changelog_path",
            side_effect=OSError("bundled changelog unavailable"),
        ):
            with self.assertRaises(OSError):
                current_release_notes("9.9.9")


if __name__ == "__main__":
    unittest.main()
