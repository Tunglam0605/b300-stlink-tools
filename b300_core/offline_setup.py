"""Install the pinned OpenOCD runtime from an authenticated offline package."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import struct
import sysconfig
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Dict, Iterable, Optional, Tuple


OPENOCD_VERSION = "0.12.0-7"
METADATA_NAME = "BUNDLE-METADATA.txt"
PACKAGE_PREFIX = "vendor/packages/"
XPACK_ROOT = "xpack-openocd-%s" % OPENOCD_VERSION
CHUNK_SIZE = 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
MAX_BUNDLE_BYTES = 768 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 64 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 120.0
TREE_MANIFEST_NAME = "OPENOCD-MANIFEST.sha256"

# Trust anchors are the SHA-256 values published beside xPack OpenOCD 0.12.0-7.
# Bundle metadata is descriptive only and cannot replace these values.
TRUSTED_OPENOCD_PACKAGES = {
    "windows-x64": (
        "xpack-openocd-0.12.0-7-win32-x64.zip",
        "6bfd3c97135aafef8affc9af1acf34fd0e2b9ca26044506f6abd7f95b7630052",
    ),
    "linux-x64": (
        "xpack-openocd-0.12.0-7-linux-x64.tar.gz",
        "94b3790983beaf8ed57e646c0620dd66d705fddae03d290823a6ed3b439468d6",
    ),
    "linux-arm64": (
        "xpack-openocd-0.12.0-7-linux-arm64.tar.gz",
        "db73a3ab91c556ecec2405a7e02d404b11139df6aba1031cad94a7e6766d06cc",
    ),
}
TRUSTED_TREE_MANIFESTS = {
    "windows-x64": "6f1855ad3f2bdfdad4d84e87e842e617a4d695d3072b514914e613df5048303a",
    "linux-x64": "54aed3d8b61193086df7e64873943a79ce956d57e5bba53a8396783605995825",
    "linux-arm64": "b676b7372aa4e493b3f103fa8701882f13114d3d60fb38efbcb2af58fb509405",
}

_PROCESS_INSTALL_LOCK = threading.RLock()
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *("COM%d" % number for number in range(1, 10)),
    *("LPT%d" % number for number in range(1, 10)),
}


def current_platform_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if not machine:
        machine = {
            "win-amd64": "x86_64",
            "linux-x86_64": "x86_64",
            "linux-aarch64": "aarch64",
        }.get(sysconfig.get_platform().lower(), "")
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "windows-x64"
    if system == "linux" and machine in {"amd64", "x86_64"}:
        return "linux-x64"
    if system == "linux" and machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    raise RuntimeError("Unsupported offline setup platform: %s/%s" % (system, machine))


def default_install_base() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return root / "B300-STLink" / "offline-runtime"
    return Path.home() / ".local" / "share" / "b300-stlink" / "offline-runtime"


def installed_openocd_path(install_base: Optional[Path] = None,
                           platform_name: Optional[str] = None) -> Path:
    selected_platform = platform_name or current_platform_name()
    executable = "openocd.exe" if selected_platform == "windows-x64" else "openocd"
    return (Path(install_base) if install_base is not None else default_install_base()) / \
        ("openocd-%s" % OPENOCD_VERSION) / "bin" / executable


def find_offline_bundle(directory: Path,
                        platform_name: Optional[str] = None) -> Optional[Path]:
    selected_platform = platform_name or current_platform_name()
    filename = {
        "windows-x64": "b300-stlink-windows-x64.zip",
        "linux-x64": "b300-stlink-linux-x64.tar.gz",
        "linux-arm64": "b300-stlink-linux-arm64.tar.gz",
    }[selected_platform]
    candidate = Path(directory).resolve() / filename
    return candidate if candidate.is_file() else None


def _parse_metadata(data: bytes) -> Dict[str, str]:
    values = {}
    for line in data.decode("utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _validate_bundle_metadata(metadata: Dict[str, str], selected_platform: str,
                              trusted_name: str, trusted_digest: str) -> None:
    if metadata.get("platform") != selected_platform:
        raise ValueError(
            "Offline bundle platform %s does not match %s." %
            (metadata.get("platform", "unknown"), selected_platform)
        )
    if metadata.get("openocd") != OPENOCD_VERSION:
        raise ValueError("Offline bundle contains an unsupported OpenOCD version.")
    if metadata.get("openocd_archive") != trusted_name:
        raise ValueError("Offline bundle names an untrusted OpenOCD package.")
    if metadata.get("openocd_sha256", "").lower() != trusted_digest.lower():
        raise ValueError("Offline bundle metadata does not match the trusted SHA-256.")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_tree_manifest(root: Path) -> bytes:
    """Build the canonical content manifest anchored in the executable."""
    selected_root = Path(root)
    manifest_path = selected_root / TREE_MANIFEST_NAME
    lines = []
    files = sorted(
        (item for item in selected_root.rglob("*")
         if item.is_file() and item != manifest_path),
        key=lambda item: item.relative_to(selected_root).as_posix(),
    )
    for source in files:
        relative = source.relative_to(selected_root).as_posix()
        lines.append("%s  vendor/openocd/%s" % (_hash_file(source), relative))
    if not lines:
        raise ValueError("OpenOCD runtime tree is empty.")
    return ("\n".join(lines) + "\n").encode("utf-8")


def verify_openocd_tree(root: Path, platform_name: Optional[str] = None) -> bool:
    """Verify every runtime file against a manifest whose digest is hardcoded."""
    selected_platform = platform_name or current_platform_name()
    selected_root = Path(root)
    manifest_path = selected_root / TREE_MANIFEST_NAME
    try:
        data = manifest_path.read_bytes()
        if len(data) > 1024 * 1024:
            return False
        expected_anchor = TRUSTED_TREE_MANIFESTS[selected_platform]
        if hashlib.sha256(data).hexdigest() != expected_anchor:
            return False
        expected = {}
        for line in data.decode("utf-8").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  vendor/openocd/(.+)", line)
            if not match:
                return False
            parts = _safe_parts(match.group(2), allow_trailing_slash=False)
            name = PurePosixPath(*parts).as_posix()
            if name in expected:
                return False
            expected[name] = match.group(1)
        actual_names = {
            item.relative_to(selected_root).as_posix()
            for item in selected_root.rglob("*")
            if item.is_file() and item != manifest_path
        }
        if actual_names != set(expected):
            return False
        return all(
            _hash_file(selected_root.joinpath(*PurePosixPath(name).parts)) == digest
            for name, digest in expected.items()
        )
    except (KeyError, OSError, UnicodeError, ValueError):
        return False


def _safe_parts(name: str, *, allow_trailing_slash: bool = True) -> Tuple[str, ...]:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name or \
            ":" in name or name.startswith("/"):
        raise ValueError("Offline archive contains an unsafe path: %r." % name)
    normalized = name[:-1] if allow_trailing_slash and name.endswith("/") else name
    parts = normalized.split("/")
    if not normalized or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Offline archive contains an unsafe path: %r." % name)
    for part in parts:
        if part.endswith((" ", ".")) or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise ValueError("Offline archive contains an unsafe path: %r." % name)
    return tuple(parts)


def _destination(root: Path, parts: Iterable[str]) -> Path:
    resolved_root = root.resolve()
    destination = resolved_root.joinpath(*parts).resolve()
    try:
        contained = os.path.commonpath((str(resolved_root), str(destination))) == str(resolved_root)
    except ValueError:
        contained = False
    if not contained or destination == resolved_root:
        raise ValueError("Offline archive contains an unsafe path.")
    return destination


def _check_limits(count: int, total: int, *, compressed: Optional[int] = None) -> None:
    if count > MAX_ARCHIVE_ENTRIES:
        raise ValueError("Offline archive has too many entries.")
    if total > MAX_EXPANDED_BYTES:
        raise ValueError("Offline archive exceeds the expanded size limit.")
    if compressed and total > 1024 * 1024 and total > compressed * MAX_COMPRESSION_RATIO:
        raise ValueError("Offline archive exceeds the compression ratio limit.")


def _copy_stream(source: BinaryIO, destination: Path, limit: int) -> Tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError("Offline archive entry exceeds its size limit.")
            digest.update(chunk)
            output.write(chunk)
    return digest.hexdigest(), total


def _validate_zip_members(archive: zipfile.ZipFile, compressed_size: int) -> None:
    infos = archive.infolist()
    total = 0
    for info in infos:
        _safe_parts(info.filename)
        if info.flag_bits & 0x1:
            raise ValueError("Encrypted offline archive entries are not supported.")
        if info.file_size > MAX_FILE_BYTES:
            raise ValueError("Offline archive entry exceeds its size limit.")
        total += info.file_size
        if info.compress_size and info.file_size > 1024 * 1024 and \
                info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
            raise ValueError("Offline archive exceeds the compression ratio limit.")
    _check_limits(len(infos), total, compressed=compressed_size)


def _preflight_zip(path: Path) -> None:
    """Bound the central directory before ZipFile allocates one object per entry."""
    size = path.stat().st_size
    if size > MAX_BUNDLE_BYTES:
        raise ValueError("Offline ZIP exceeds its compressed size limit.")
    tail_size = min(size, 65_557)
    with path.open("rb") as stream:
        stream.seek(size - tail_size)
        tail = stream.read(tail_size)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        raise ValueError("Offline ZIP has no valid end-of-central-directory record.")
    disk, central_disk, disk_entries, entries, central_size, central_offset, comment_size = \
        struct.unpack_from(
            "<HHHHIIH", tail, offset + 4
        )
    eocd_absolute = size - tail_size + offset
    if eocd_absolute + 22 + comment_size != size:
        raise ValueError("Offline ZIP has an invalid end record.")
    if central_offset + central_size > eocd_absolute:
        raise ValueError("Offline ZIP central directory is outside the archive.")
    if disk or central_disk or disk_entries != entries:
        raise ValueError("Multi-disk offline ZIP files are not supported.")
    if entries == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
        raise ValueError("ZIP64 offline bundles are not supported.")
    if central_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise ValueError("Offline ZIP central directory exceeds its size limit.")

    actual_entries = 0
    consumed = 0
    with path.open("rb") as stream:
        stream.seek(central_offset)
        while consumed < central_size:
            header = stream.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise ValueError("Offline ZIP central directory is malformed.")
            name_size, extra_size, entry_comment_size = struct.unpack_from(
                "<HHH", header, 28
            )
            record_size = 46 + name_size + extra_size + entry_comment_size
            if record_size > central_size - consumed:
                raise ValueError("Offline ZIP central directory is malformed.")
            stream.seek(record_size - 46, os.SEEK_CUR)
            consumed += record_size
            actual_entries += 1
            if actual_entries > MAX_ARCHIVE_ENTRIES:
                raise ValueError("Offline archive has too many entries.")
    if consumed != central_size or actual_entries != entries:
        raise ValueError("Offline ZIP entry count does not match its central directory.")


def _validate_tar_member(member: tarfile.TarInfo, count: int, total: int) -> int:
    _safe_parts(member.name)
    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
        raise ValueError("Offline archive contains an unsupported special entry.")
    if member.size > MAX_FILE_BYTES:
        raise ValueError("Offline archive entry exceeds its size limit.")
    total += member.size
    _check_limits(count, total)
    return total


def _zip_stream(archive: zipfile.ZipFile, name: str) -> BinaryIO:
    try:
        return archive.open(name, "r")
    except KeyError as error:
        raise ValueError("Offline bundle is missing %s." % name) from error


def _read_small(stream: BinaryIO, limit: int = 64 * 1024) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Offline bundle metadata exceeds its size limit.")
    return data


def _extract_zip_package(package: Path, staged_root: Path) -> None:
    _preflight_zip(package)
    with zipfile.ZipFile(package, "r") as archive:
        _validate_zip_members(archive, package.stat().st_size)
        for info in archive.infolist():
            parts = _safe_parts(info.filename)
            if parts[0] != XPACK_ROOT:
                raise ValueError("Trusted OpenOCD package has an unexpected root directory.")
            if len(parts) == 1 or info.is_dir():
                continue
            destination = _destination(staged_root, parts[1:])
            mode = info.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                raise ValueError("ZIP symlinks are not supported in the trusted package.")
            with archive.open(info, "r") as source:
                _copy_stream(source, destination, MAX_FILE_BYTES)


def _normalize_tar_link(member: tarfile.TarInfo) -> Tuple[str, ...]:
    target = member.linkname
    if not target or "\x00" in target or "\\" in target or ":" in target or \
            target.startswith("/"):
        raise ValueError("Trusted OpenOCD package contains an unsafe link target.")
    if member.issym():
        combined = list(PurePosixPath(member.name).parent.parts) + target.split("/")
    else:
        combined = target.split("/")
    normalized = []
    for part in combined:
        if part in {"", "."}:
            continue
        if part == "..":
            if not normalized:
                raise ValueError("Trusted OpenOCD package contains an unsafe link target.")
            normalized.pop()
        else:
            _safe_parts(part, allow_trailing_slash=False)
            normalized.append(part)
    if not normalized or normalized[0] != XPACK_ROOT:
        raise ValueError("Trusted OpenOCD package contains an unsafe link target.")
    return tuple(normalized[1:])


def _extract_tar_package(package: Path, staged_root: Path) -> None:
    with tarfile.open(package, "r:gz") as archive:
        links = []
        count = 0
        expanded_total = 0
        for member in archive:
            count += 1
            expanded_total = _validate_tar_member(member, count, expanded_total)
            parts = _safe_parts(member.name)
            if parts[0] != XPACK_ROOT:
                raise ValueError("Trusted OpenOCD package has an unexpected root directory.")
            if len(parts) == 1 or member.isdir():
                continue
            destination = _destination(staged_root, parts[1:])
            if member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Trusted OpenOCD package contains an unreadable file.")
                with source:
                    _copy_stream(source, destination, MAX_FILE_BYTES)
            elif member.issym() or member.islnk():
                links.append((destination, _normalize_tar_link(member)))
            else:
                raise ValueError("Trusted OpenOCD package contains an unsupported entry.")
        _check_limits(count, expanded_total, compressed=package.stat().st_size)
        for destination, target_parts in links:
            target = _destination(staged_root, target_parts)
            if not target.is_file():
                raise ValueError("Trusted OpenOCD package link does not target a regular file.")
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Offline archive entry exceeds its size limit.")
            expanded_total += target.stat().st_size
            _check_limits(count, expanded_total, compressed=package.stat().st_size)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)


def extract_trusted_openocd_package(package: Path, destination: Path,
                                    platform_name: str) -> Path:
    """Verify and safely extract one pinned original xPack archive."""
    source = Path(package).resolve()
    trusted_name, trusted_digest = TRUSTED_OPENOCD_PACKAGES[platform_name]
    if source.name != trusted_name or _hash_file(source) != trusted_digest:
        raise ValueError("OpenOCD package does not match the trusted SHA-256.")
    target = Path(destination).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("OpenOCD extraction destination must be empty.")
    target.mkdir(parents=True, exist_ok=True)
    if trusted_name.endswith(".zip"):
        _extract_zip_package(source, target)
    else:
        _extract_tar_package(source, target)
    executable_name = "openocd.exe" if platform_name == "windows-x64" else "openocd"
    executable = target / "bin" / executable_name
    if not executable.is_file():
        raise ValueError("Trusted OpenOCD package does not contain the executable.")
    if platform_name != "windows-x64":
        executable.chmod(executable.stat().st_mode | 0o111)
    tree_manifest = build_tree_manifest(target)
    tree_anchor = hashlib.sha256(tree_manifest).hexdigest()
    if tree_anchor != TRUSTED_TREE_MANIFESTS[platform_name]:
        raise ValueError("Extracted OpenOCD runtime does not match the trusted tree manifest.")
    (target / TREE_MANIFEST_NAME).write_bytes(tree_manifest)
    return executable


def _consume_outer_tar(source: Path, package_member: str,
                       package: Path) -> Tuple[bytes, Optional[str]]:
    metadata = None
    package_digest = None
    count = 0
    total = 0
    if source.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("Offline TAR exceeds its compressed size limit.")
    with tarfile.open(source, "r:gz") as archive:
        for member in archive:
            count += 1
            total = _validate_tar_member(member, count, total)
            if member.name not in {METADATA_NAME, package_member}:
                continue
            if not member.isfile():
                raise ValueError("Offline bundle entry is not a regular file: %s." % member.name)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Offline bundle cannot read %s." % member.name)
            with stream:
                if member.name == METADATA_NAME:
                    if metadata is not None:
                        raise ValueError("Offline bundle contains duplicate metadata.")
                    metadata = _read_small(stream)
                else:
                    if package_digest is not None:
                        raise ValueError("Offline bundle contains a duplicate OpenOCD package.")
                    package_digest, _ = _copy_stream(stream, package, MAX_PACKAGE_BYTES)
    _check_limits(count, total, compressed=source.stat().st_size)
    if metadata is None:
        raise ValueError("Offline bundle is missing %s." % METADATA_NAME)
    return metadata, package_digest


@contextmanager
def _installation_lock(base: Path):
    """Serialize setup in-process and across processes without touching hardware."""
    with _PROCESS_INSTALL_LOCK:
        lock_path = base / ".offline-setup.lock"
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0)
            if lock_file.tell() == 0 and lock_path.stat().st_size == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                _acquire_windows_lock(lock_file, LOCK_TIMEOUT_SECONDS)
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


def _acquire_windows_lock(lock_file, timeout_seconds: float) -> None:
    import msvcrt
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Timed out waiting for another offline setup process."
                ) from error
            time.sleep(0.1)


def _atomic_replace(staged_root: Path, final_root: Path) -> None:
    backup = final_root.parent / (".%s.backup-%s" % (final_root.name, uuid.uuid4().hex))
    previous_moved = False
    if final_root.exists():
        os.replace(str(final_root), str(backup))
        previous_moved = True
    try:
        os.replace(str(staged_root), str(final_root))
    except Exception:
        if previous_moved and backup.exists() and not final_root.exists():
            os.replace(str(backup), str(final_root))
        raise
    if backup.exists():
        shutil.rmtree(backup)


def install_offline_bundle(bundle: Path, install_base: Optional[Path] = None,
                           platform_name: Optional[str] = None) -> Path:
    """Authenticate the bundled xPack archive, safely extract it, and install atomically."""
    source = Path(bundle).resolve()
    selected_platform = platform_name or current_platform_name()
    base = (Path(install_base).resolve() if install_base is not None
            else default_install_base().resolve())
    final_root = base / ("openocd-%s" % OPENOCD_VERSION)
    executable = installed_openocd_path(base, selected_platform)
    base.mkdir(parents=True, exist_ok=True)
    try:
        trusted_name, trusted_digest = TRUSTED_OPENOCD_PACKAGES[selected_platform]
    except KeyError as error:
        raise RuntimeError("Unsupported offline setup platform: %s" % selected_platform) from error
    package_member = PACKAGE_PREFIX + trusted_name

    is_zip = source.suffix.lower() == ".zip"
    is_tar = source.name.lower().endswith(".tar.gz")
    if not is_zip and not is_tar:
        raise ValueError("Select the complete offline B300 ZIP or tar.gz bundle.")

    with _installation_lock(base):
        with tempfile.TemporaryDirectory(
                prefix="b300-offline-setup-", dir=str(base)) as temp:
            package = Path(temp) / trusted_name
            if is_zip:
                _preflight_zip(source)
                with zipfile.ZipFile(source, "r") as archive:
                    _validate_zip_members(archive, source.stat().st_size)
                    with _zip_stream(archive, METADATA_NAME) as metadata_stream:
                        metadata_data = _read_small(metadata_stream)
                    metadata = _parse_metadata(metadata_data)
                    _validate_bundle_metadata(
                        metadata, selected_platform, trusted_name, trusted_digest
                    )
                    with _zip_stream(archive, package_member) as package_stream:
                        actual_digest, _ = _copy_stream(
                            package_stream, package, MAX_PACKAGE_BYTES
                        )
            else:
                metadata_data, actual_digest = _consume_outer_tar(
                    source, package_member, package
                )
            metadata = _parse_metadata(metadata_data)
            _validate_bundle_metadata(
                metadata, selected_platform, trusted_name, trusted_digest
            )
            if actual_digest is None:
                raise ValueError("Offline bundle is missing %s." % package_member)
            if actual_digest.lower() != trusted_digest.lower():
                raise ValueError("OpenOCD package does not match the trusted SHA-256.")

            staged_root = Path(temp) / final_root.name
            extract_trusted_openocd_package(package, staged_root, selected_platform)
            _atomic_replace(staged_root, final_root)
    return executable
