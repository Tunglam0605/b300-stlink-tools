from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.release.check_size_budget import check_size


class ReleaseSizeBudgetTests(unittest.TestCase):
    def test_artifact_under_budget_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bundle.zip"
            artifact.write_bytes(b"x" * 1024)
            size, actual = check_size(artifact, 1.0)
        self.assertEqual(size, 1024)
        self.assertGreater(actual, 0)

    def test_artifact_over_budget_fails_with_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bundle.zip"
            artifact.write_bytes(b"x" * 2048)
            with self.assertRaisesRegex(RuntimeError, "size regression.*budget"):
                check_size(artifact, 0.001)

    def test_missing_artifact_is_not_silently_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                check_size(Path(directory) / "missing.zip", 1.0)


if __name__ == "__main__":
    unittest.main()
