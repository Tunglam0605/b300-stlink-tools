import unittest
from unittest import mock
from pathlib import Path

import b300_core.update_platform as update_platform
from b300_core.update_platform import UpdatePlatform, detect_update_platform
from b300_core.versioning import SemVer


class UpdaterVersioningTests(unittest.TestCase):
    def _detect_cli(self, system: str, machine: str):
        detector = getattr(update_platform, "detect_cli_update_platform", None)
        self.assertIsNotNone(detector, "CLI update platform detector is missing")
        return detector(system, machine)

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

    def test_detects_cli_platform_from_os_and_cpu_only(self) -> None:
        cases = (
            ("Windows", "AMD64", "windows-x64-cli"),
            ("Windows", "x86_64", "windows-x64-cli"),
            ("Linux", "AMD64", "linux-x64-cli"),
            ("Linux", "x86_64", "linux-x64-cli"),
            ("Linux", "ARM64", "linux-arm64-cli"),
            ("Linux", "aarch64", "linux-arm64-cli"),
        )
        for system, machine, expected in cases:
            with self.subTest(system=system, machine=machine):
                self.assertEqual(self._detect_cli(system, machine).value, expected)

    def test_cli_empty_machine_uses_stable_platform_fallback(self) -> None:
        with mock.patch(
            "b300_core.update_platform.platform.machine", return_value=""
        ), mock.patch(
            "b300_core.update_platform.sysconfig.get_platform", return_value="linux-aarch64"
        ):
            self.assertEqual(
                self._detect_cli("Linux", "").value,
                "linux-arm64-cli",
            )

    def test_cli_detection_is_independent_of_gui_package_mode(self) -> None:
        with mock.patch.dict("os.environ", {"APPIMAGE": "/tmp/B300.AppImage"}):
            self.assertEqual(
                self._detect_cli("Linux", "x86_64").value,
                "linux-x64-cli",
            )

    def test_cli_rejects_unsupported_os_or_cpu(self) -> None:
        for system, machine in (("Darwin", "x86_64"), ("Linux", "riscv64")):
            with self.subTest(system=system, machine=machine), self.assertRaisesRegex(
                RuntimeError, "Unsupported CLI update platform"
            ):
                self._detect_cli(system, machine)


if __name__ == "__main__":
    unittest.main()
