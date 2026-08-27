import unittest
from pathlib import Path

from scripts.release.release_contract import EXPECTED_PACKAGE_ASSETS


ROOT = Path(__file__).resolve().parents[1]
LATEST = "https://github.com/Tunglam0605/b300-stlink-tools/releases/latest/download/"


class ReleaseDocumentationTests(unittest.TestCase):
    def test_readme_has_direct_download_for_every_user_package(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for filename in EXPECTED_PACKAGE_ASSETS:
            with self.subTest(filename=filename):
                self.assertIn(LATEST + filename, readme)

    def test_release_process_documents_version_tag_and_latest_validation(self) -> None:
        guide = (ROOT / "docs" / "09_RELEASE_PROCESS.md").read_text(encoding="utf-8")
        self.assertIn("bump_version", guide)
        self.assertIn("git tag v", guide)
        self.assertIn("releases/latest/download", guide)
        self.assertIn("không tạo lại cùng một tag", guide.lower())


if __name__ == "__main__":
    unittest.main()
