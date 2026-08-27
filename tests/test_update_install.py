import tempfile
import unittest
from pathlib import Path

from b300_core.update_install import prepare_install
from b300_core.update_platform import UpdatePlatform


class UpdateInstallTests(unittest.TestCase):
    def test_windows_plan_launches_verified_per_user_installer_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "B300-STLink-GUI-Windows-x64.exe"
            package.write_bytes(b"installer")
            plan = prepare_install(package, UpdatePlatform.WINDOWS_X64)
            self.assertTrue(plan.managed)
            self.assertEqual(plan.program, package.resolve())
            self.assertEqual(plan.arguments, ("/CURRENTUSER", "/CLOSEAPPLICATIONS"))
            self.assertNotIn("cmd", str(plan.program).lower())

    def test_linux_appimage_and_deb_return_manual_verified_handoff(self) -> None:
        cases = (
            (
                UpdatePlatform.LINUX_X64_APPIMAGE,
                "B300-STLink-GUI-Ubuntu-x64.AppImage",
                "chmod +x",
            ),
            (
                UpdatePlatform.LINUX_ARM64_DEB,
                "b300-stlink-gui_arm64.deb",
                "sudo apt install",
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            for platform_name, filename, instruction in cases:
                with self.subTest(platform=platform_name):
                    package = Path(temp) / filename
                    package.write_bytes(b"package")
                    plan = prepare_install(package, platform_name)
                    self.assertFalse(plan.managed)
                    self.assertEqual(plan.open_directory, package.parent.resolve())
                    self.assertIn(instruction, plan.instructions)

    def test_rejects_relative_missing_and_wrong_platform_package(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute"):
            prepare_install(
                Path("B300-STLink-GUI-Windows-x64.exe"),
                UpdatePlatform.WINDOWS_X64,
            )
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "B300-STLink-GUI-Windows-x64.exe"
            with self.assertRaisesRegex(ValueError, "regular file"):
                prepare_install(missing, UpdatePlatform.WINDOWS_X64)
            wrong = Path(temp) / "b300-stlink-gui_amd64.deb"
            wrong.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "does not match"):
                prepare_install(wrong, UpdatePlatform.WINDOWS_X64)


if __name__ == "__main__":
    unittest.main()
