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


    def test_download_guide_is_deterministic_for_users_and_agents(self) -> None:
        guide = (ROOT / "DOWNLOAD.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        for filename in EXPECTED_PACKAGE_ASSETS:
            with self.subTest(filename=filename):
                self.assertIn(LATEST + filename, guide)

        self.assertIn("default: stable", guide)
        self.assertIn("default: gui", guide)
        self.assertIn("windows_x64:", guide)
        self.assertIn("linux_x86_64:", guide)
        self.assertIn("linux_arm64:", guide)
        self.assertIn("uname -m", guide)
        self.assertIn("latest.json", guide)
        self.assertIn("Source code (zip)", guide)
        self.assertIn("Source code (tar.gz)", guide)
        self.assertIn("[DOWNLOAD.md](DOWNLOAD.md)", readme)
        self.assertIn("[DOWNLOAD.md](DOWNLOAD.md)", agents)

    def test_release_process_documents_version_tag_and_latest_validation(self) -> None:
        guide = (ROOT / "docs" / "09_RELEASE_PROCESS.md").read_text(encoding="utf-8")
        self.assertIn("bump_version", guide)
        self.assertIn("git tag v", guide)
        self.assertIn("releases/latest/download", guide)
        self.assertIn("không tạo lại cùng một tag", guide.lower())


    def test_operator_docs_and_agent_skill_do_not_describe_the_removed_marker(self) -> None:
        paths = (
            ROOT / "README.md",
            ROOT / "AGENTS.md",
            ROOT / "docs" / "03_FLASH_FIRMWARE.md",
            ROOT / "docs" / "05_TROUBLESHOOTING.md",
            ROOT / "docs" / "07_GUI_WINDOWS_UBUNTU.md",
            ROOT / ".agents" / "skills" / "b300-ota-stlink" / "SKILL.md",
            ROOT / ".agents" / "skills" / "b300-ota-stlink" / "references" / "safety.md",
        )
        for path in paths:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("53544C4B", text)
                self.assertNotIn("40002860", text)

    def test_normal_and_factory_workflows_are_documented_separately(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        flash_guide = (ROOT / "docs" / "03_FLASH_FIRMWARE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("provision-bootloader", readme)
        self.assertIn("Sector 0", readme)
        self.assertIn("flash erase_sector 0 3 7", flash_guide)
        self.assertIn("BKP1R", flash_guide)
        skill = (ROOT / ".agents" / "skills" / "b300-ota-stlink" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("provision-bootloader", skill)
        self.assertIn("PROVISION BOOTLOADER", skill)

    def test_canonical_docs_require_v65_stlink_metadata_lifecycle(self) -> None:
        paths = (
            ROOT / "README.md",
            ROOT / "AGENTS.md",
            ROOT / "docs" / "03_FLASH_FIRMWARE.md",
            ROOT / ".agents" / "skills" / "b300-ota-stlink" / "SKILL.md",
        )
        obsolete = (
            "uses its erased-metadata fallback",
            "dùng erased-metadata fallback",
            "Do not create metadata",
        )
        for path in paths:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                for phrase in obsolete:
                    self.assertNotIn(phrase, text)
                self.assertIn("STLM", text)
                self.assertIn("VERIFIED", text)
                self.assertIn("CONFIRMED", text)
                self.assertIn("0x0800C000", text)



if __name__ == "__main__":
    unittest.main()
