"""Verified, per-user installation of signed native CLI bundles."""

from __future__ import annotations

import hashlib
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from .offline_setup import (
    MAX_ARCHIVE_ENTRIES as OFFLINE_MAX_ARCHIVE_ENTRIES,
    MAX_BUNDLE_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_EXPANDED_BYTES as OFFLINE_MAX_EXPANDED_BYTES,
    MAX_FILE_BYTES,
    _destination,
    _preflight_zip,
    _safe_parts,
)
from .release_manifest import EXPECTED_UPDATE_FILENAMES, ReleaseAsset
from .update_helper import wait_for_parent_exit
from .updater import UpdateClient


MAX_ARCHIVE_ENTRIES = OFFLINE_MAX_ARCHIVE_ENTRIES
MAX_EXPANDED_BYTES = OFFLINE_MAX_EXPANDED_BYTES
CHUNK_SIZE = 1024 * 1024
LOCK_TIMEOUT_SECONDS = 120.0
_CLI_PLATFORMS = {
    "windows-x64-cli",
    "linux-x64-cli",
    "linux-arm64-cli",
}
_PROCESS_INSTALL_LOCK = threading.RLock()


class ManagedInstallUnsupported(RuntimeError):
    """The active executable is not the fixed per-user managed installation."""

    reason_code = "MANAGED_INSTALL_UNSUPPORTED"


@dataclass(frozen=True)
class ManagedInstallPaths:
    root: Path
    launcher: Path
    executable: Path
    staging_base: Path
    result_log: Path


@dataclass(frozen=True)
class StagedCliBundle:
    root: Path
    executable: Path
    bootstrap: Path
    tree_sha256: str


@dataclass(frozen=True)
class CliInstallHandoff:
    staged: StagedCliBundle
    command: tuple[str, ...]
    result_log: Path


def _platform_value(platform_name) -> str:
    value = str(getattr(platform_name, "value", platform_name))
    if value not in _CLI_PLATFORMS:
        raise ValueError("Unsupported managed CLI platform: %s" % value)
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_cli_package(package: Path, asset: ReleaseAsset, platform_name) -> Path:
    """Recheck the exact signed filename, platform, size, and SHA-256 contract."""
    selected = _platform_value(platform_name)
    source = Path(package).expanduser()
    if not source.is_absolute():
        raise ValueError("Verified CLI package path must be absolute.")
    source = source.resolve()
    if not source.is_file():
        raise ValueError("Verified CLI package must be a regular file.")
    expected_name = EXPECTED_UPDATE_FILENAMES[selected]
    if source.name != expected_name or asset.filename != expected_name:
        raise ValueError("CLI package filename does not match the selected platform.")
    UpdateClient._validate_download_asset(asset)
    if source.stat().st_size != asset.size:
        raise ValueError("CLI package size does not match the signed asset contract.")
    if _hash_file(source) != asset.sha256:
        raise ValueError("CLI package SHA-256 does not match the signed asset contract.")
    return source


def _check_limits(count: int, expanded: int, compressed: int) -> None:
    if count > MAX_ARCHIVE_ENTRIES:
        raise ValueError("CLI archive has too many entries.")
    if expanded > MAX_EXPANDED_BYTES:
        raise ValueError("CLI archive exceeds the expanded size limit.")
    if compressed and expanded > 1024 * 1024 and \
            expanded > compressed * MAX_COMPRESSION_RATIO:
        raise ValueError("CLI archive exceeds the compression ratio limit.")


def _copy_regular_file(source, destination: Path, expected_size: int) -> None:
    if expected_size < 0 or expected_size > MAX_FILE_BYTES:
        raise ValueError("CLI archive entry exceeds its size limit.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with destination.open("xb") as output:
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size or total > MAX_FILE_BYTES:
                raise ValueError("CLI archive entry exceeds its declared size.")
            output.write(chunk)
        if total != expected_size:
            raise ValueError("CLI archive entry is shorter than its declared size.")
        output.flush()
        os.fsync(output.fileno())


def _member_key(parts, case_insensitive: bool) -> str:
    value = "/".join(parts)
    return value.casefold() if case_insensitive else value


def _extract_zip(source: Path, destination: Path) -> None:
    _preflight_zip(source)
    compressed = source.stat().st_size
    count = 0
    expanded = 0
    names = set()
    with zipfile.ZipFile(source, "r") as archive:
        infos = archive.infolist()
        for info in infos:
            count += 1
            parts = _safe_parts(info.filename)
            key = _member_key(parts, True)
            if key in names:
                raise ValueError("CLI archive contains duplicate paths.")
            names.add(key)
            if info.flag_bits & 0x1:
                raise ValueError("Encrypted CLI archive entries are not supported.")
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if info.is_dir():
                if kind not in (0, stat.S_IFDIR):
                    raise ValueError("CLI archive directory is not a regular directory.")
            elif kind not in (0, stat.S_IFREG):
                if kind == stat.S_IFLNK:
                    raise ValueError("CLI archive symlinks are not supported.")
                raise ValueError("CLI archive contains a non-regular entry.")
            expanded += info.file_size
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError("CLI archive entry exceeds its size limit.")
            if info.compress_size and info.file_size > 1024 * 1024 and \
                    info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
                raise ValueError("CLI archive exceeds the compression ratio limit.")
            _check_limits(count, expanded, compressed)
        for info in infos:
            parts = _safe_parts(info.filename)
            target = _destination(destination, parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            with archive.open(info, "r") as stream:
                _copy_regular_file(stream, target, info.file_size)


def _extract_tar(source: Path, destination: Path) -> None:
    compressed = source.stat().st_size
    if compressed > MAX_BUNDLE_BYTES:
        raise ValueError("CLI TAR exceeds its compressed size limit.")
    count = 0
    expanded = 0
    names = set()
    with tarfile.open(source, "r:gz") as archive:
        for member in archive:
            count += 1
            parts = _safe_parts(member.name)
            key = _member_key(parts, False)
            if key in names:
                raise ValueError("CLI archive contains duplicate paths.")
            names.add(key)
            if member.isdir():
                pass
            elif not member.isfile():
                if member.issym() or member.islnk():
                    raise ValueError("CLI archive links are not supported.")
                raise ValueError("CLI archive contains an unsupported special entry.")
            if member.size > MAX_FILE_BYTES:
                raise ValueError("CLI archive entry exceeds its size limit.")
            expanded += member.size
            _check_limits(count, expanded, compressed)
            target = _destination(destination, parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("CLI archive contains an unreadable regular file.")
            with stream:
                _copy_regular_file(stream, target, member.size)


def _required_bundle_paths(root: Path, platform_name: str):
    if platform_name == "windows-x64-cli":
        executable = root / "b300-stlink.exe"
        bootstrap = root / "install.ps1"
        if not executable.is_file():
            raise ValueError("CLI bundle is missing its executable.")
        if not bootstrap.is_file():
            raise ValueError("CLI bundle is missing its bootstrap.")
        if not (root / "_internal").is_dir():
            raise ValueError("Windows CLI bundle is missing its onedir runtime.")
        openocd = root / "vendor" / "openocd" / "bin" / "openocd.exe"
    else:
        executable = root / "b300-stlink"
        bootstrap = root / "install.sh"
        if not executable.is_file():
            raise ValueError("CLI bundle is missing its executable.")
        if not bootstrap.is_file():
            raise ValueError("CLI bundle is missing its bootstrap.")
        openocd = root / "vendor" / "openocd" / "bin" / "openocd"
    if not openocd.is_file():
        raise ValueError("CLI bundle is missing its bundled OpenOCD executable.")
    if platform_name != "windows-x64-cli":
        executable.chmod(0o755)
        openocd.chmod(0o755)
    return executable, bootstrap


def hash_staged_tree(root: Path) -> str:
    """Hash names, modes, and bytes while rejecting post-extraction link changes."""
    selected = Path(root).resolve()
    digest = hashlib.sha256()
    items = sorted(selected.rglob("*"), key=lambda path: path.relative_to(selected).as_posix())
    if not items:
        raise ValueError("Staged CLI application tree is empty.")
    for item in items:
        relative = item.relative_to(selected).as_posix().encode("utf-8")
        item_stat = item.lstat()
        if stat.S_ISLNK(item_stat.st_mode):
            raise ValueError("Staged CLI application contains a symlink.")
        mode = stat.S_IMODE(item_stat.st_mode)
        if item.is_dir():
            digest.update(b"D\0" + relative + b"\0" + oct(mode).encode("ascii") + b"\0")
            continue
        if not stat.S_ISREG(item_stat.st_mode):
            raise ValueError("Staged CLI application contains a special file.")
        digest.update(b"F\0" + relative + b"\0")
        digest.update(oct(mode).encode("ascii") + b"\0")
        digest.update(str(item_stat.st_size).encode("ascii") + b"\0")
        with item.open("rb") as stream:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    return digest.hexdigest()


def extract_verified_cli_bundle(
        package: Path, asset: ReleaseAsset, platform_name, destination: Path) -> StagedCliBundle:
    selected = _platform_value(platform_name)
    source = verify_cli_package(package, asset, selected)
    target = Path(destination).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("CLI extraction destination must be empty.")
    target.mkdir(parents=True, exist_ok=True)
    if selected == "windows-x64-cli":
        if source.suffix.lower() != ".zip":
            raise ValueError("Windows CLI update must be the signed ZIP asset.")
        _extract_zip(source, target)
    else:
        if not source.name.lower().endswith(".tar.gz"):
            raise ValueError("Linux CLI update must be the signed tar.gz asset.")
        _extract_tar(source, target)
    executable, bootstrap = _required_bundle_paths(target, selected)
    return StagedCliBundle(target, executable, bootstrap, hash_staged_tree(target))


def validate_managed_root(target: Path, user_home: Path) -> Path:
    """Accept only an absolute managed root contained beneath the canonical user home."""
    candidate = Path(target)
    if not candidate.is_absolute():
        raise ValueError("Managed CLI installation requires an absolute per-user root.")
    raw = candidate.as_posix().casefold()
    if any(raw == prefix or raw.startswith(prefix + "/") for prefix in (
            "/", "/bin", "/boot", "/dev", "/etc", "/lib", "/opt", "/proc",
            "/root", "/run", "/sbin", "/sys", "/usr", "/var")):
        raise ValueError("Managed CLI installation refuses a system directory.")
    resolved = candidate.resolve()
    home = _validated_user_home(user_home)
    if home == Path(home.anchor) or resolved == Path(resolved.anchor):
        raise ValueError("Managed CLI installation refuses a system root.")
    lowered = str(resolved).replace("\\", "/").casefold()
    if any(marker in lowered for marker in (
            "/windows/", "/program files/", "/program files (x86)/", "/usr/", "/opt/")):
        raise ValueError("Managed CLI installation refuses a system directory.")
    try:
        relative = resolved.relative_to(home)
    except ValueError as error:
        raise ValueError("Managed CLI installation root must remain beneath the user home.") from error
    if not relative.parts:
        raise ValueError("Managed CLI installation refuses the entire user home.")
    return resolved


def _validated_user_home(user_home: Path) -> Path:
    home = Path(user_home)
    if not home.is_absolute():
        raise ValueError("Managed CLI user home must be absolute.")
    resolved = home.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("Managed CLI installation refuses a system root as user home.")
    return resolved


def _managed_environment_base(value: str, variable_name: str, user_home: Path) -> Path:
    base = Path(value)
    if not base.is_absolute():
        raise ValueError("%s must name an absolute user directory." % variable_name)
    resolved = base.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("Managed CLI installation refuses a system root.")
    try:
        resolved.relative_to(user_home)
    except ValueError as error:
        raise ValueError("%s must remain beneath the user home." % variable_name) from error
    return resolved


def _validate_launcher_path(launcher: Path, user_home: Path) -> Path:
    """Keep launcher writes lexical and reject existing symlinks in every component."""
    selected = Path(launcher)
    home = _validated_user_home(user_home)
    if not selected.is_absolute():
        raise ValueError("Managed CLI launcher path must be absolute.")
    try:
        relative = selected.relative_to(home)
    except ValueError as error:
        raise ValueError("Managed CLI launcher must remain beneath the user home.") from error
    if not relative.parts:
        raise ValueError("Managed CLI launcher cannot replace the user home.")
    current = home
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Managed CLI launcher path contains an unsafe symlink.")
    return selected


def managed_install_paths(
        platform_name, *, environ: Optional[Mapping[str, str]] = None,
        home: Optional[Path] = None) -> ManagedInstallPaths:
    selected = _platform_value(platform_name)
    selected_environ = os.environ if environ is None else environ
    selected_home = _validated_user_home(Path.home() if home is None else Path(home))
    if selected == "windows-x64-cli":
        local_app_data = selected_environ.get("LOCALAPPDATA")
        user_data = (
            _managed_environment_base(local_app_data, "LOCALAPPDATA", selected_home)
            if local_app_data else selected_home / "AppData" / "Local"
        )
        root = validate_managed_root(user_data / "B300-STLink", selected_home)
        staging_base = validate_managed_root(user_data / "B300-STLink-updates", selected_home)
        launcher = root / "bin" / "b300-stlink.cmd"
        executable = root / "b300-stlink.exe"
    else:
        root = validate_managed_root(
            selected_home / ".local" / "share" / "b300-stlink", selected_home,
        )
        cache_home = selected_environ.get("XDG_CACHE_HOME")
        cache = (
            _managed_environment_base(cache_home, "XDG_CACHE_HOME", selected_home)
            if cache_home else selected_home / ".cache"
        )
        staging_base = validate_managed_root(cache / "b300-stlink" / "updates", selected_home)
        launcher = selected_home / ".local" / "bin" / "b300-stlink"
        executable = root / "b300-stlink"
    _validate_launcher_path(launcher, selected_home)
    return ManagedInstallPaths(
        root=root,
        launcher=launcher,
        executable=executable,
        staging_base=staging_base,
        result_log=staging_base / "install-result.json",
    )


def stage_verified_cli_bundle(
        package: Path, asset: ReleaseAsset, platform_name, *, staging_base: Path) -> StagedCliBundle:
    base = Path(staging_base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    stage_directory = Path(tempfile.mkdtemp(prefix="cli-install-", dir=str(base)))
    if os.name != "nt":
        stage_directory.chmod(0o700)
    try:
        return extract_verified_cli_bundle(
            package, asset, platform_name, stage_directory / "application",
        )
    except BaseException:
        shutil.rmtree(stage_directory, ignore_errors=True)
        raise


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (".%s.tmp-%s" % (path.name, uuid.uuid4().hex))
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if os.name != "nt":
            temporary.chmod(mode)
        os.replace(str(temporary), str(path))
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _launcher_bytes(platform_name: str) -> bytes:
    if platform_name == "windows-x64-cli":
        return b'@echo off\r\n"%~dp0..\\b300-stlink.exe" %*\r\n'
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'runner_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'tool_root=$(CDPATH= cd -- "$runner_dir/../share/b300-stlink" && pwd)\n'
        'exec "$tool_root/b300-stlink" "$@"\n'
    ).encode("utf-8")


@contextmanager
def _installation_lock(base: Path):
    base.mkdir(parents=True, exist_ok=True)
    with _PROCESS_INSTALL_LOCK:
        lock_path = base / ".cli-install.lock"
        with lock_path.open("a+b") as lock_file:
            if lock_path.stat().st_size == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as error:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Timed out waiting for another CLI install.") from error
                        time.sleep(0.1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock_file.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _validate_stage_location(staged_root: Path, staging_base: Path) -> Path:
    staged = Path(staged_root).resolve()
    base = Path(staging_base).resolve()
    try:
        relative = staged.relative_to(base)
    except ValueError as error:
        raise ValueError("Staged CLI application is outside the private update cache.") from error
    if len(relative.parts) != 2 or relative.parts[1] != "application" or \
            not relative.parts[0].startswith("cli-install-"):
        raise ValueError("Staged CLI application has an invalid private-cache location.")
    return staged


def _write_result(paths: ManagedInstallPaths, platform_name: str,
                  status_value: str, message: str) -> None:
    payload = json.dumps({
        "schema_version": 1,
        "status": status_value,
        "platform": platform_name,
        "message": message,
    }, sort_keys=True).encode("utf-8") + b"\n"
    _atomic_write(paths.result_log, payload)


def apply_staged_cli_install(
        staged_root: Path, platform_name, parent_pid: int, *,
        expected_tree_sha256: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None, home: Optional[Path] = None,
        wait_parent: Callable[[int], None] = wait_for_parent_exit,
        replace: Callable[[str, str], None] = os.replace) -> int:
    """Detached-helper body: wait, copy, atomically publish, and recreate launcher."""
    selected = _platform_value(platform_name)
    selected_home = _validated_user_home(Path.home() if home is None else Path(home))
    paths = managed_install_paths(selected, environ=environ, home=home)
    _validate_launcher_path(paths.launcher, selected_home)
    staged = _validate_stage_location(staged_root, paths.staging_base)
    _required_bundle_paths(staged, selected)
    actual_tree = hash_staged_tree(staged)
    if expected_tree_sha256 is not None and actual_tree != expected_tree_sha256:
        raise ValueError("Staged CLI application changed after verified extraction.")
    wait_parent(parent_pid)
    paths.root.parent.mkdir(parents=True, exist_ok=True)
    paths.staging_base.mkdir(parents=True, exist_ok=True)
    backup = paths.root.parent / (".%s.backup-%s" % (paths.root.name, uuid.uuid4().hex))
    publish = paths.root.parent / (".%s.publish-%s" % (paths.root.name, uuid.uuid4().hex))
    previous_moved = False
    published = False
    try:
        with _installation_lock(paths.staging_base):
            shutil.copytree(staged, publish, symlinks=False)
            if hash_staged_tree(publish) != actual_tree:
                raise ValueError("Copied CLI application tree failed verification.")
            _fsync_directory(publish)
            if paths.root.exists():
                replace(str(paths.root), str(backup))
                previous_moved = True
            try:
                replace(str(publish), str(paths.root))
                published = True
                _fsync_directory(paths.root.parent)
                _validate_launcher_path(paths.launcher, selected_home)
                _atomic_write(
                    paths.launcher, _launcher_bytes(selected),
                    mode=0o755 if selected != "windows-x64-cli" else 0o600,
                )
                _write_result(paths, selected, "ok", "Managed CLI update installed.")
            except BaseException:
                if published and paths.root.exists():
                    shutil.rmtree(paths.root)
                    published = False
                if previous_moved and backup.exists() and not paths.root.exists():
                    replace(str(backup), str(paths.root))
                raise
            if backup.exists():
                shutil.rmtree(backup)
        return 0
    except BaseException as error:
        try:
            _write_result(paths, selected, "error", str(error))
        except OSError:
            pass
        raise
    finally:
        if publish.exists():
            shutil.rmtree(publish, ignore_errors=True)


def _spawn_detached(command: list[str], platform_name: str, cwd: Path):
    kwargs = {
        "close_fds": True,
        "shell": False,
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform_name == "windows-x64-cli":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008) |
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) |
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def launch_managed_cli_install(
        package: Path, asset: ReleaseAsset, platform_name, *,
        environ: Optional[Mapping[str, str]] = None, home: Optional[Path] = None,
        current_executable: Optional[Path] = None, frozen: Optional[bool] = None,
        parent_pid: Optional[int] = None, spawner: Optional[Callable] = None) -> CliInstallHandoff:
    """Verify/extract a CLI bundle, then detach a staged helper using argv only."""
    selected = _platform_value(platform_name)
    paths = managed_install_paths(selected, environ=environ, home=home)
    active = Path(sys.executable if current_executable is None else current_executable).resolve()
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if not is_frozen or active != paths.executable:
        raise ManagedInstallUnsupported(
            "Managed self-update is available only from the standard per-user installation; "
            "download the signed CLI archive and run its install bootstrap manually."
        )
    staged = stage_verified_cli_bundle(
        package, asset, selected, staging_base=paths.staging_base,
    )
    command = [
        str(staged.executable),
        "--apply-cli-update",
        "--platform", selected,
        "--staged-root", str(staged.root),
        "--parent-pid", str(os.getpid() if parent_pid is None else parent_pid),
        "--tree-sha256", staged.tree_sha256,
    ]
    if spawner is None:
        _spawn_detached(command, selected, staged.root.parent)
    else:
        kwargs = {
            "close_fds": True,
            "shell": False,
            "cwd": str(staged.root.parent),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if selected == "windows-x64-cli":
            kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
        else:
            kwargs["start_new_session"] = True
        spawner(command, **kwargs)
    return CliInstallHandoff(staged, tuple(command), paths.result_log)


def main(argv=None) -> int:
    """Run only inside the detached staged native CLI helper."""
    parser = argparse.ArgumentParser(description="Apply a verified staged B300 CLI update.")
    parser.add_argument("--platform", required=True, choices=sorted(_CLI_PLATFORMS))
    parser.add_argument("--staged-root", required=True, type=Path)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--tree-sha256", required=True)
    args = parser.parse_args(argv)
    if not __import__("re").fullmatch(r"[0-9a-f]{64}", args.tree_sha256):
        parser.error("--tree-sha256 must be a lowercase SHA-256 digest")
    return apply_staged_cli_install(
        args.staged_root,
        args.platform,
        args.parent_pid,
        expected_tree_sha256=args.tree_sha256,
    )


if __name__ == "__main__":
    raise SystemExit(main())
