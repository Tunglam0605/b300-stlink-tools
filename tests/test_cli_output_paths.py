from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b300_cli.output_paths import validated_output_path


class CliOutputPathTests(unittest.TestCase):
    def test_new_output_in_existing_directory_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.jsonl"
            self.assertEqual(validated_output_path(output, False), output.resolve())

    def test_output_parent_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "missing" / "capture.jsonl"
            with self.assertRaisesRegex(ValueError, "existing directory"):
                validated_output_path(output, False)

    def test_existing_output_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.jsonl"
            output.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "--force"):
                validated_output_path(output, False)
            self.assertEqual(validated_output_path(output, True), output.resolve())

    def test_directory_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "must name a file"):
                validated_output_path(Path(directory), True)


if __name__ == "__main__":
    unittest.main()
