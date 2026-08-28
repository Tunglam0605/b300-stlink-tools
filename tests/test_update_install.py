import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core.update_install import launch_install_plan, prepare_install
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

    def test_linux_appimage_and_deb_are_managed_without_terminal_instructions(self) -> None:
        cases = (
            (UpdatePlatform.LINUX_X64_APPIMAGE, "B300-STLink-GUI-Ubuntu-x64.AppImage"),
            (UpdatePlatform.LINUX_ARM64_APPIMAGE, "B300-STLink-GUI-Ubuntu-arm64.AppImage"),
            (UpdatePlatform.LINUX_X64_DEB, "b300-stlink-gui_amd64.deb"),
            (UpdatePlatform.LINUX_ARM64_DEB, "b300-stlink-gui_arm64.deb"),
        )
        with tempfile.TemporaryDirectory() as temp:
            for platform_name, filename in cases:
                with self.subTest(platform=platform_name):
                    package = Path(temp) / filename
                    package.write_bytes(b"package")
                    plan = prepare_install(package, platform_name)
                    self.assertTrue(plan.managed)
                    self.assertEqual(plan.instructions, "")
                    self.assertIsNone(plan.open_directory)

    def test_linux_deb_launches_detached_helper_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "b300-stlink-gui_amd64.deb"
            package.write_bytes(b"package")
            plan = prepare_install(package, UpdatePlatform.LINUX_X64_DEB)
            process = mock.Mock()
            with mock.patch("b300_core.update_install.shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                    mock.patch("b300_core.update_install.subprocess.Popen", return_value=process) as popen, \
                    mock.patch("b300_core.update_install.os.getpid", return_value=4321):
                launch_install_plan(plan)
            command = popen.call_args.args[0]
            self.assertIn("--apply-verified-update", command)
            self.assertIn("linux-x64-deb", command)
            self.assertIn(str(package.resolve()), command)
            self.assertIn("4321", command)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertFalse(popen.call_args.kwargs["shell"])
            self.assertEqual(popen.call_args.kwargs["cwd"], "/")

    def test_linux_appimage_helper_receives_running_appimage_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "B300-STLink-GUI-Ubuntu-x64.AppImage"
            package.write_bytes(b"package")
            current = Path(temp) / "B300-STLink.AppImage"
            current.write_bytes(b"old")
            plan = prepare_install(package, UpdatePlatform.LINUX_X64_APPIMAGE)
            with mock.patch.dict(os.environ, {"APPIMAGE": str(current)}, clear=False), \
                    mock.patch("b300_core.update_install.subprocess.Popen") as popen:
                launch_install_plan(plan)
            command = popen.call_args.args[0]
            target_index = command.index("--appimage-target") + 1
            self.assertEqual(command[target_index], str(current.resolve()))

    def test_linux_appimage_requires_running_appimage_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "B300-STLink-GUI-Ubuntu-x64.AppImage"
            package.write_bytes(b"package")
            plan = prepare_install(package, UpdatePlatform.LINUX_X64_APPIMAGE)
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "AppImage path"):
                    launch_install_plan(plan)

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
