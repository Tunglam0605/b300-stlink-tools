#!/usr/bin/env python3
"""Build a native self-contained B300 ST-Link release on its target OS."""
from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import struct
import sys
import sysconfig
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Optional

from b300_version import __version__ as TOOL_VERSION
from b300_core.offline_setup import (
    TRUSTED_OPENOCD_PACKAGES,
    extract_trusted_openocd_package,
)


ROOT = Path(__file__).resolve().parent
VERSION = "0.12.0-7"
BASE = "https://github.com/xpack-dev-tools/openocd-xpack/releases/download/v%s" % VERSION
GDB_VERSION = "15.2.1-1.1"
GDB_BASE = "https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/v%s" % GDB_VERSION
TRUSTED_GDB_PACKAGES = {
    "windows-x64": (
        "xpack-arm-none-eabi-gcc-15.2.1-1.1-win32-x64.zip",
        "bae6a3d1667697ce750c3b13d6d26d80973ecedc2cc87bf04869e83447fd93ea",
    ),
    "linux-x64": (
        "xpack-arm-none-eabi-gcc-15.2.1-1.1-linux-x64.tar.gz",
        "da6a49ad4003944b823c6c93702a8787c922ab34bd7e918ec0eaf6933a9b1ff6",
    ),
    "linux-arm64": (
        "xpack-arm-none-eabi-gcc-15.2.1-1.1-linux-arm64.tar.gz",
        "67980c7990eba7bb7ffdf39699102effd70889f5ac427be19a8c8a6c5fab2972",
    ),
}
HASH_CHUNK_BYTES = 1024 * 1024
MAX_GDB_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_GDB_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_GDB_FILE_BYTES = 512 * 1024 * 1024
MAX_GDB_ENTRIES = 20_000
MAX_GDB_COMPRESSION_RATIO = 500
MAX_GDB_CENTRAL_DIRECTORY_BYTES = 64 * 1024 * 1024


def target_for(system: str, machine: str, python_platform: str):
    system, machine, python_platform = system.lower(), machine.lower(), python_platform.lower()
    if not machine:
        machine = {
            "win-amd64": "x86_64",
            "linux-x86_64": "x86_64",
            "linux-aarch64": "aarch64",
        }.get(python_platform, "")
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "windows-x64", "win32-x64", ".zip", "install.ps1", "b300-stlink.exe"
    if system == "linux" and machine in {"amd64", "x86_64"}:
        return "linux-x64", "linux-x64", ".tar.gz", "install.sh", "b300-stlink"
    if system == "linux" and machine in {"aarch64", "arm64"}:
        return "linux-arm64", "linux-arm64", ".tar.gz", "install.sh", "b300-stlink"
    raise RuntimeError("Unsupported target: %s/%s" % (system, machine))


def target():
    return target_for(platform.system(), platform.machine(), sysconfig.get_platform())


def release_names(platform_name: str):
    if platform_name == "windows-x64":
        return (
            "B300-STLink-GUI-Windows-x64.zip",
            "B300-STLink-CLI-Windows-x64.zip",
        )
    if platform_name == "linux-x64":
        return (
            "B300-STLink-GUI-Linux-x64.tar.gz",
            "B300-STLink-CLI-Linux-x64.tar.gz",
        )
    if platform_name == "linux-arm64":
        return (
            "B300-STLink-GUI-Linux-arm64.tar.gz",
            "B300-STLink-CLI-Linux-arm64.tar.gz",
        )
    raise RuntimeError("Unsupported release platform: %s" % platform_name)


def gui_resources(platform_name: str):
    resources = [
        ROOT / "LICENSE",
        ROOT / "branding" / "b300-stlink-icon.png",
        ROOT / "branding" / "b300-stlink-icon.ico",
        ROOT / "branding" / "b300-stlink-wordmark.png",
    ]
    if platform_name.startswith("linux-"):
        resources.extend([
            ROOT / "packaging" / "linux" / "b300-stlink-gui.desktop",
            ROOT / "packaging" / "linux" / "b300-stlink-gui.svg",
        ])
    return resources


def runtime_resources(platform_name: str):
    """Immutable resources required by both frozen entry points."""
    del platform_name
    return [
        ROOT / "resources" / "firmware" / "b300_bootloader_f407ze_com3_v00050001.hex",
        ROOT / "resources" / "firmware" / "b300_bootloader_manifest.json",
    ]


def pyinstaller_data_argument(source: Path) -> str:
    separator = ";" if platform.system().lower() == "windows" else ":"
    return "%s%sresources/firmware" % (source, separator)


@dataclass(frozen=True)
class CliPyinstallerPlan:
    command: tuple[str, ...]
    executable: Path
    application_root: Optional[Path]


def cli_pyinstaller_plan(
        platform_name: str, output_dir: Path, work_dir: Path) -> CliPyinstallerPlan:
    """Keep Windows CLI onedir and Linux CLI onefile without changing GUI packaging."""
    output = Path(output_dir)
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir" if platform_name == "windows-x64" else "--onefile",
        "--name", "b300-stlink", "--distpath", str(output),
        "--workpath", str(work_dir),
        "--icon", str(ROOT / "branding" / "b300-stlink-icon.ico"),
    ]
    for resource in runtime_resources(platform_name):
        command.extend(["--add-data", pyinstaller_data_argument(resource)])
    command.append(str(ROOT / "b300_stlink.py"))
    if platform_name == "windows-x64":
        return CliPyinstallerPlan(
            tuple(command),
            Path("b300-stlink") / "b300-stlink.exe",
            output / "b300-stlink",
        )
    return CliPyinstallerPlan(tuple(command), Path("b300-stlink"), None)


def fetch(url: str, output: Path) -> None:
    with urllib.request.urlopen(url) as source, output.open("wb") as destination:
        shutil.copyfileobj(source, destination)


def validate_trusted_package(platform_name: str, filename: str, digest: str) -> None:
    trusted_name, trusted_digest = TRUSTED_OPENOCD_PACKAGES[platform_name]
    if filename != trusted_name or digest.lower() != trusted_digest.lower():
        raise RuntimeError(
            "Downloaded OpenOCD package does not match the built-in trust anchor."
        )


def validate_trusted_gdb_package(platform_name: str, filename: str, digest: str) -> None:
    trusted_name, trusted_digest = TRUSTED_GDB_PACKAGES[platform_name]
    if filename != trusted_name or digest.lower() != trusted_digest.lower():
        raise RuntimeError("Downloaded GDB package does not match the built-in trust anchor.")


def hash_file(path: Path, maximum_bytes: int = None) -> str:
    """Hash a bounded archive without holding it in memory."""
    source = Path(path)
    maximum_bytes = MAX_GDB_PACKAGE_BYTES if maximum_bytes is None else maximum_bytes
    if source.stat().st_size > maximum_bytes:
        raise ValueError("GDB archive exceeds the compressed size limit.")
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as stream:
        while True:
            chunk = stream.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError("GDB archive exceeds the compressed size limit.")
            digest.update(chunk)
    return digest.hexdigest()


def _archive_parts(name: str):
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or ":" in name:
        raise ValueError("GDB archive contains an unsafe path.")
    parts = PurePosixPath(name.rstrip("/")).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("GDB archive contains an unsafe path.")
    if not parts[0].startswith("xpack-arm-none-eabi-gcc-"):
        raise ValueError("GDB archive has an unexpected root directory.")
    return parts


def _gdb_destination(destination: Path, parts) -> Path:
    target = destination.joinpath(*parts[1:]).resolve()
    root = destination.resolve()
    if target == root or root not in target.parents:
        raise ValueError("GDB archive contains an unsafe path.")
    return target


def _check_gdb_expanded(total: int, compressed_size: int) -> None:
    if total > MAX_GDB_EXPANDED_BYTES:
        raise ValueError("GDB archive exceeds the expanded size limit.")
    if compressed_size <= 0 or total > compressed_size * MAX_GDB_COMPRESSION_RATIO:
        raise ValueError("GDB archive exceeds the compression ratio limit.")


def _preflight_gdb_zip(source: Path) -> None:
    """Bound ZIP metadata before ZipFile allocates one object per member."""
    size = source.stat().st_size
    tail_size = min(size, 65_557)
    with source.open("rb") as stream:
        stream.seek(size - tail_size)
        tail = stream.read(tail_size)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        raise ValueError("GDB ZIP has no valid end-of-central-directory record.")
    disk, central_disk, disk_entries, entries, central_size, central_offset, comment_size = \
        struct.unpack_from("<HHHHIIH", tail, offset + 4)
    eocd_offset = size - tail_size + offset
    if eocd_offset + 22 + comment_size != size or central_offset + central_size > eocd_offset:
        raise ValueError("GDB ZIP central directory is malformed.")
    if disk or central_disk or disk_entries != entries or entries == 0xFFFF or \
            central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
        raise ValueError("GDB ZIP multi-disk or ZIP64 archives are not supported.")
    if entries > MAX_GDB_ENTRIES:
        raise ValueError("GDB archive has too many entries.")
    if central_size > MAX_GDB_CENTRAL_DIRECTORY_BYTES:
        raise ValueError("GDB ZIP central directory exceeds its size limit.")


def _preflight_gdb_tar(source: Path, destination: Path, compressed_size: int) -> None:
    """Validate TAR metadata and all materialized links before writing files."""
    count = 0
    expanded_total = 0
    files = {}
    links = []
    with tarfile.open(source, "r:gz") as archive:
        for member in archive:
            count += 1
            if count > MAX_GDB_ENTRIES:
                raise ValueError("GDB archive has too many entries.")
            if member.size > MAX_GDB_FILE_BYTES:
                raise ValueError("GDB archive entry exceeds its size limit.")
            expanded_total += member.size
            _check_gdb_expanded(expanded_total, compressed_size)
            parts = _archive_parts(member.name)
            if len(parts) == 1 or member.isdir():
                continue
            member_destination = _gdb_destination(destination, parts)
            if member.issym() or member.islnk():
                links.append((
                    member_destination,
                    _tar_link_destination(member.name, member.linkname, destination, member.islnk()),
                ))
            elif member.isfile():
                files[member_destination] = member.size
            else:
                raise ValueError("GDB archive contains an unsupported entry.")
    while links:
        unresolved = []
        copied = False
        for link_destination, link_target in links:
            if link_target in files:
                size = files[link_target]
                expanded_total += size
                _check_gdb_expanded(expanded_total, compressed_size)
                files[link_destination] = size
                copied = True
            else:
                unresolved.append((link_destination, link_target))
        if not copied:
            raise ValueError("GDB archive contains an unresolved link target.")
        links = unresolved


def _copy_gdb_stream(source, target: Path, expected_bytes: int) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with target.open("wb") as output:
        while True:
            chunk = source.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_bytes or total > MAX_GDB_FILE_BYTES:
                raise ValueError("GDB archive entry exceeds its size limit.")
            output.write(chunk)
    if total != expected_bytes:
        raise ValueError("GDB archive entry size does not match its header.")
    return total


def _tar_link_destination(member_name: str, link_name: str, destination: Path,
                          hard_link: bool = False) -> Path:
    member_parts = _archive_parts(member_name)
    if not link_name or "\x00" in link_name or "\\" in link_name or ":" in link_name or \
            link_name.startswith("/"):
        raise ValueError("GDB archive contains an unsafe link target.")
    if hard_link:
        parts = list(_archive_parts(link_name))
        return _gdb_destination(destination, parts)
    parts = list(member_parts[:-1])
    for part in link_name.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if len(parts) <= 1:
                raise ValueError("GDB archive contains an unsafe link target.")
            parts.pop()
        else:
            parts.append(part)
    return _gdb_destination(destination, _archive_parts("/".join(parts)))


def extract_trusted_gdb_package(package: Path, destination: Path, platform_name: str) -> Path:
    """Verify one pinned xPack archive and extract its portable runtime safely."""
    source = Path(package)
    compressed_size = source.stat().st_size
    digest = hash_file(source)
    validate_trusted_gdb_package(platform_name, source.name, digest)
    target = Path(destination)
    if target.exists() and any(target.iterdir()):
        raise ValueError("GDB extraction destination must be empty.")
    count = 0
    expanded_total = 0
    if source.name.endswith(".zip"):
        _preflight_gdb_zip(source)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_GDB_ENTRIES:
                raise ValueError("GDB archive has too many entries.")
            for member in archive.infolist():
                count += 1
                if member.flag_bits & 0x1:
                    raise ValueError("Encrypted GDB archive entries are not supported.")
                if member.file_size > MAX_GDB_FILE_BYTES:
                    raise ValueError("GDB archive entry exceeds its size limit.")
                if member.file_size and (not member.compress_size or
                                         member.file_size > member.compress_size * MAX_GDB_COMPRESSION_RATIO):
                    raise ValueError("GDB archive exceeds the compression ratio limit.")
                expanded_total += member.file_size
                _check_gdb_expanded(expanded_total, compressed_size)
                parts = _archive_parts(member.filename)
                if len(parts) == 1 or member.is_dir():
                    continue
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("GDB archive symlinks are not supported in ZIP packages.")
                with archive.open(member) as stream:
                    _copy_gdb_stream(stream, _gdb_destination(target, parts), member.file_size)
    else:
        _preflight_gdb_tar(source, target, compressed_size)
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source, "r:gz") as archive:
            links = []
            for member in archive:
                count += 1
                if count > MAX_GDB_ENTRIES:
                    raise ValueError("GDB archive has too many entries.")
                if member.size > MAX_GDB_FILE_BYTES:
                    raise ValueError("GDB archive entry exceeds its size limit.")
                expanded_total += member.size
                _check_gdb_expanded(expanded_total, compressed_size)
                parts = _archive_parts(member.name)
                if len(parts) == 1 or member.isdir():
                    continue
                if member.issym() or member.islnk():
                    links.append((
                        _gdb_destination(target, parts),
                        _tar_link_destination(member.name, member.linkname, target, member.islnk()),
                    ))
                    continue
                if not member.isfile():
                    raise ValueError("GDB archive contains an unsupported entry.")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("GDB archive contains an unreadable file.")
                with stream:
                    _copy_gdb_stream(stream, _gdb_destination(target, parts), member.size)
            while links:
                unresolved = []
                copied = False
                for link_destination, link_target in links:
                    if link_target.is_file():
                        expanded_total += link_target.stat().st_size
                        _check_gdb_expanded(expanded_total, compressed_size)
                        link_destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(link_target, link_destination)
                        copied = True
                    else:
                        unresolved.append((link_destination, link_target))
                if not copied:
                    raise ValueError("GDB archive contains an unresolved link target.")
                links = unresolved
    executable = target / "bin" / ("arm-none-eabi-gdb.exe" if platform_name == "windows-x64" else "arm-none-eabi-gdb")
    if not executable.is_file():
        raise ValueError("Trusted GDB package does not contain arm-none-eabi-gdb.")
    if platform_name != "windows-x64":
        executable.chmod(executable.stat().st_mode | 0o111)
    return executable


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release")
    parser.add_argument("--internal-distribution-approved", action="store_true")
    parser.add_argument("--flavor", choices=("all", "gui", "cli"), default="all")
    args = parser.parse_args(argv)
    if not args.internal_distribution_approved:
        parser.error("--internal-distribution-approved is required.")
    platform_name, xpack_name, extension, installer, executable = target()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="b300-openocd-") as temp:
        temp = Path(temp)
        filename = "xpack-openocd-%s-%s%s" % (VERSION, xpack_name, extension)
        archive, checksum = temp / filename, temp / (filename + ".sha")
        fetch("%s/%s" % (BASE, filename), archive)
        fetch("%s/%s.sha" % (BASE, filename), checksum)
        verified_sha256 = checksum.read_text().split()[0].lower()
        if hash_file(archive, maximum_bytes=MAX_GDB_PACKAGE_BYTES) != verified_sha256:
            raise RuntimeError("OpenOCD checksum mismatch.")
        validate_trusted_package(platform_name, filename, verified_sha256)
        openocd_root = temp / "openocd-runtime"
        extract_trusted_openocd_package(archive, openocd_root, platform_name)
        gdb_archive = None
        gdb_sha256 = None
        gdb_root = None
        if args.flavor in {"all", "gui"}:
            gdb_filename, gdb_sha256 = TRUSTED_GDB_PACKAGES[platform_name]
            gdb_archive = temp / gdb_filename
            fetch("%s/%s" % (GDB_BASE, gdb_filename), gdb_archive)
            actual_gdb_sha256 = hash_file(gdb_archive)
            validate_trusted_gdb_package(platform_name, gdb_filename, actual_gdb_sha256)
            gdb_root = temp / "gdb-runtime"
            extract_trusted_gdb_package(gdb_archive, gdb_root, platform_name)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user",
                               "-r", str(ROOT / "requirements-build.txt")])
        cli_plan = None
        if args.flavor in {"all", "cli"}:
            cli_plan = cli_pyinstaller_plan(
                platform_name, args.output_dir, temp / "pyinstaller-cli",
            )
            subprocess.check_call(list(cli_plan.command))
        gui_executable = "b300-stlink-gui.exe" if platform_name == "windows-x64" else "b300-stlink-gui"
        gui_application_root = None
        if args.flavor in {"all", "gui"}:
            gui_spec = (ROOT / "b300_gui_windows.spec"
                        if platform_name == "windows-x64" else ROOT / "b300_gui.spec")
            subprocess.check_call([
                sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--distpath", str(args.output_dir),
                "--workpath", str(temp / "pyinstaller-gui"),
                str(gui_spec),
            ])
            if platform_name == "windows-x64":
                gui_application_root = args.output_dir / "b300-stlink-gui"
                gui_executable = str(Path("b300-stlink-gui") / "b300-stlink-gui.exe")
        gui_name, cli_name = release_names(platform_name)

        def package(flavor_name, selected_executable, output_name, resources, application_root=None):
            command = [
                sys.executable, str(ROOT / "package_internal.py"),
                "--flavor", flavor_name,
                "--executable", str(args.output_dir / selected_executable),
                "--openocd-root", str(openocd_root),
                "--bootstrap", str(ROOT / installer),
                "--output", str(args.output_dir / output_name),
                "--platform", platform_name, "--internal-distribution-approved",
                "--version", TOOL_VERSION,
                "--openocd-archive", filename,
                "--openocd-sha256", verified_sha256,
                "--openocd-package", str(archive),
            ]
            if application_root is not None:
                command.extend(["--application-root", str(application_root)])
            if flavor_name == "gui":
                assert gdb_root is not None and gdb_archive is not None and gdb_sha256 is not None
                command.extend([
                    "--gdb-root", str(gdb_root), "--gdb-archive", gdb_archive.name,
                    "--gdb-sha256", gdb_sha256,
                ])
            for resource in resources:
                command.extend(["--resource", str(resource)])
            subprocess.check_call(command)

        if args.flavor in {"all", "cli"}:
            assert cli_plan is not None
            package(
                "cli", str(cli_plan.executable), cli_name,
                [ROOT / "LICENSE"] + runtime_resources(platform_name),
                application_root=cli_plan.application_root,
            )
        if args.flavor in {"all", "gui"}:
            package(
                "gui", gui_executable, gui_name,
                gui_resources(platform_name) + runtime_resources(platform_name),
                application_root=gui_application_root,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
