"""Detached Linux update helper: wait for GUI exit, install, then relaunch."""

from __future__ import annotations

import argparse
import errno
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from .release_manifest import EXPECTED_UPDATE_FILENAMES
from .update_platform import UpdatePlatform


_APPIMAGE_PLATFORMS = {
    UpdatePlatform.LINUX_X64_APPIMAGE,
    UpdatePlatform.LINUX_ARM64_APPIMAGE,
}
_DEB_PLATFORMS = {
    UpdatePlatform.LINUX_X64_DEB,
    UpdatePlatform.LINUX_ARM64_DEB,
}


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_parent_exit(parent_pid: int, timeout_seconds: float = 30.0,
                         sleeper: Callable[[float], None] = time.sleep) -> None:
    if parent_pid <= 1 or parent_pid == os.getpid():
        raise ValueError("Invalid parent PID for update handoff.")
    deadline = time.monotonic() + timeout_seconds
    while _process_exists(parent_pid):
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for the previous B300 GUI to exit.")
        sleeper(0.1)


def _validate_package(package: Path, platform_name: UpdatePlatform) -> Path:
    selected = UpdatePlatform(platform_name)
    source = Path(package).expanduser()
    if not source.is_absolute():
        raise ValueError("Verified update package path must be absolute.")
    source = source.resolve()
    if not source.is_file():
        raise ValueError("Verified update package is no longer available.")
    if source.name != EXPECTED_UPDATE_FILENAMES[selected.value]:
        raise ValueError("Update package filename does not match the selected platform.")
    return source


def _launch_detached(command: list[str]) -> None:
    subprocess.Popen(
        command,
        close_fds=True,
        shell=False,
        start_new_session=True,
        cwd="/",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _deb_launcher() -> str:
    for candidate in ("/usr/local/bin/b300-stlink-gui", "/opt/b300-stlink/b300-stlink-gui"):
        if Path(candidate).is_file():
            return candidate
    return "/usr/local/bin/b300-stlink-gui"


def install_deb(package: Path, runner: Callable = subprocess.run,
                launcher: Callable[[list[str]], None] = _launch_detached) -> int:
    pkexec = shutil.which("pkexec")
    apt_get = shutil.which("apt-get")
    if not pkexec or not apt_get:
        raise FileNotFoundError("Ubuntu graphical installer requires pkexec and apt-get.")
    try:
        result = runner(
            [pkexec, apt_get, "install", "-y", str(package)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
    except BaseException:
        # apt/pkexec failed to launch after the previous GUI exited. Restore the
        # user-facing application before propagating the helper failure.
        launcher([_deb_launcher()])
        raise
    # Relaunch even on cancelled/failed authentication so the user is not left
    # without a GUI. The installed version is new on success and old on failure.
    launcher([_deb_launcher()])
    return int(result.returncode)


def _copy_appimage_atomically(package: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / (".%s.b300-update-%d" % (target.name, os.getpid()))
    try:
        with package.open("rb") as source, temporary.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        temporary.chmod(0o755)
        os.replace(str(temporary), str(target))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def install_appimage(package: Path, target: Path, runner: Callable = subprocess.run,
                     launcher: Callable[[list[str]], None] = _launch_detached) -> int:
    destination = Path(target).expanduser()
    if not destination.is_absolute():
        raise ValueError("AppImage target must be absolute.")
    destination = destination.resolve()
    try:
        try:
            _copy_appimage_atomically(package, destination)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
                raise
            pkexec = shutil.which("pkexec")
            install = shutil.which("install")
            if not pkexec or not install:
                raise FileNotFoundError(
                    "Replacing this AppImage requires pkexec and install."
                ) from error
            result = runner(
                [pkexec, install, "-m", "0755", str(package), str(destination)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
            )
            if result.returncode != 0:
                launcher([str(destination)])
                return int(result.returncode)
    except BaseException:
        # Atomic replacement leaves the old AppImage untouched on ordinary copy
        # failures. Relaunch it before surfacing the helper error.
        if destination.is_file():
            launcher([str(destination)])
        raise
    launcher([str(destination)])
    return 0


def apply_verified_update(platform_name: UpdatePlatform, package: Path, parent_pid: int,
                          appimage_target: Optional[Path] = None,
                          wait_parent: Callable[[int], None] = wait_for_parent_exit) -> int:
    selected = UpdatePlatform(platform_name)
    if selected == UpdatePlatform.WINDOWS_X64:
        raise ValueError("The detached update helper is Linux-only.")
    source = _validate_package(package, selected)
    wait_parent(parent_pid)
    if selected in _DEB_PLATFORMS:
        return install_deb(source)
    if selected in _APPIMAGE_PLATFORMS:
        if appimage_target is None:
            raise ValueError("AppImage update target is required.")
        return install_appimage(source, Path(appimage_target))
    raise ValueError("Unsupported Linux update platform: %s" % selected.value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=[item.value for item in UpdatePlatform])
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--appimage-target", type=Path)
    args = parser.parse_args(argv)
    return apply_verified_update(
        UpdatePlatform(args.platform), args.package, args.parent_pid, args.appimage_target
    )


if __name__ == "__main__":
    raise SystemExit(main())
