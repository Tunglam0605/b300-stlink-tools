from __future__ import annotations

import errno
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.update_helper import (
    apply_verified_update,
    install_appimage,
    install_deb,
    wait_for_parent_exit,
)
from b300_core.update_platform import UpdatePlatform


class UpdateHelperTests(unittest.TestCase):
    def test_wait_for_parent_exit_returns_when_parent_disappears(self) -> None:
        states = iter((True, True, False))
        with mock.patch("b300_core.update_helper._process_exists", side_effect=lambda pid: next(states)):
            wait_for_parent_exit(1234, timeout_seconds=1.0, sleeper=lambda _value: None)

    def test_deb_uses_pkexec_apt_get_and_relaunches_on_success(self) -> None:
        launched = []
        calls = []
        package = Path("/tmp/b300-stlink-gui_amd64.deb")
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0)
        with mock.patch("b300_core.update_helper.shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                mock.patch("b300_core.update_helper._deb_launcher", return_value="/usr/local/bin/b300-stlink-gui"):
            result = install_deb(package, runner=runner, launcher=launched.append)
        self.assertEqual(result, 0)
        self.assertEqual(calls[0][0], [
            "/usr/bin/pkexec", "/usr/bin/apt-get", "install", "-y", str(package)
        ])
        self.assertFalse(calls[0][1]["shell"])
        self.assertEqual(launched, [["/usr/local/bin/b300-stlink-gui"]])

    def test_deb_relaunches_old_gui_when_authentication_is_cancelled(self) -> None:
        launched = []
        with mock.patch("b300_core.update_helper.shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                mock.patch("b300_core.update_helper._deb_launcher", return_value="/usr/local/bin/b300-stlink-gui"):
            result = install_deb(
                Path("/tmp/b300-stlink-gui_amd64.deb"),
                runner=lambda *args, **kwargs: SimpleNamespace(returncode=126),
                launcher=launched.append,
            )
        self.assertEqual(result, 126)
        self.assertEqual(launched, [["/usr/local/bin/b300-stlink-gui"]])

    def test_deb_runner_exception_still_relaunches_old_gui(self) -> None:
        launched = []
        with mock.patch("b300_core.update_helper.shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                mock.patch("b300_core.update_helper._deb_launcher", return_value="/usr/local/bin/b300-stlink-gui"):
            with self.assertRaisesRegex(OSError, "apt failed"):
                install_deb(
                    Path("/tmp/b300-stlink-gui_amd64.deb"),
                    runner=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("apt failed")),
                    launcher=launched.append,
                )
        self.assertEqual(launched, [["/usr/local/bin/b300-stlink-gui"]])

    def test_appimage_replaces_target_atomically_and_relaunches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "B300-STLink-GUI-Ubuntu-x64.AppImage"
            package.write_bytes(b"new-appimage")
            target = root / "B300-STLink.AppImage"
            target.write_bytes(b"old-appimage")
            launched = []
            result = install_appimage(package, target, launcher=launched.append)
            self.assertEqual(result, 0)
            self.assertEqual(target.read_bytes(), b"new-appimage")
            if os.name != "nt":
                self.assertTrue(target.stat().st_mode & stat.S_IXUSR)
            self.assertEqual(launched, [[str(target.resolve())]])
            self.assertEqual(list(root.glob(".*.b300-update-*")), [])

    def test_appimage_copy_failure_relaunches_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "B300-STLink-GUI-Ubuntu-x64.AppImage"
            package.write_bytes(b"new")
            target = root / "B300-STLink.AppImage"
            target.write_bytes(b"old")
            launched = []
            with mock.patch(
                "b300_core.update_helper._copy_appimage_atomically",
                side_effect=OSError(errno.ENOSPC, "disk full"),
            ):
                with self.assertRaises(OSError):
                    install_appimage(package, target, launcher=launched.append)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(launched, [[str(target.resolve())]])

    def test_apply_update_waits_for_parent_before_deb_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "b300-stlink-gui_amd64.deb"
            package.write_bytes(b"deb")
            events = []
            with mock.patch("b300_core.update_helper.install_deb", side_effect=lambda path: events.append(("install", path)) or 0):
                result = apply_verified_update(
                    UpdatePlatform.LINUX_X64_DEB,
                    package,
                    4444,
                    wait_parent=lambda pid: events.append(("wait", pid)),
                )
            self.assertEqual(result, 0)
            self.assertEqual(events, [("wait", 4444), ("install", package.resolve())])

    def test_helper_rejects_wrong_signed_platform_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "B300-STLink-GUI-Ubuntu-x64.AppImage"
            package.write_bytes(b"app")
            with self.assertRaisesRegex(ValueError, "does not match"):
                apply_verified_update(
                    UpdatePlatform.LINUX_X64_DEB,
                    package,
                    4444,
                    wait_parent=lambda _pid: None,
                )


if __name__ == "__main__":
    unittest.main()
