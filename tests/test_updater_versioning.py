import unittest
from unittest import mock
from pathlib import Path

from b300_core.update_platform import UpdatePlatform, detect_update_platform
from b300_core.versioning import SemVer


class UpdaterVersioningTests(unittest.TestCase):
    def test_semver_compares_numeric_components(self) -> None:
        self.assertLess(SemVer.parse("0.9.9"), SemVer.parse("0.10.0"))
        self.assertLess(SemVer.parse("0.10.0"), SemVer.parse("1.0.0"))
        self.assertEqual(str(SemVer.parse("12.34.56")), "12.34.56")

    def test_semver_rejects_noncanonical_input(self) -> None:
        for value in ("v0.3.0", "01.0.0", "1.0", "1.0.0-rc1", " 1.0.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SemVer.parse(value)

    def test_detects_windows_and_appimage_platforms(self) -> None:
        self.assertEqual(
            detect_update_platform(Path("B300.exe"), "Windows", "AMD64"),
            UpdatePlatform.WINDOWS_X64,
        )
        self.assertEqual(
            detect_update_platform(Path("B300.AppImage"), "Linux", "x86_64"),
            UpdatePlatform.LINUX_X64_APPIMAGE,
        )
        self.assertEqual(
            detect_update_platform(Path("B300.AppImage"), "Linux", "aarch64"),
            UpdatePlatform.LINUX_ARM64_APPIMAGE,
        )
        with mock.patch("b300_core.update_platform.platform.machine", return_value="AMD64"):
            self.assertEqual(
                detect_update_platform(Path("B300.exe"), "Windows", ""),
                UpdatePlatform.WINDOWS_X64,
            )

    def test_linux_installed_package_defaults_to_deb_identity(self) -> None:
        self.assertEqual(
            detect_update_platform(Path("/opt/b300-stlink/b300-stlink-gui"), "Linux", "x86_64"),
            UpdatePlatform.LINUX_X64_DEB,
        )
        with self.assertRaisesRegex(RuntimeError, "Unsupported update platform"):
            detect_update_platform(Path("tool"), "Linux", "riscv64")


if __name__ == "__main__":
    unittest.main()
