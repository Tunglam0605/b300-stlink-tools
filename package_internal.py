#!/usr/bin/env python3
"""Create a portable platform-specific B300 ST-Link archive."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import tempfile
import tarfile
import zipfile
from pathlib import Path

from b300_core.runtime_integrity import (
    MANIFEST_NAME, RuntimeIntegrityError, runtime_files,
    write_runtime_manifest, validate_runtime,
)

from b300_version import __version__ as TOOL_VERSION
from b300_core.offline_setup import (
    TREE_MANIFEST_NAME,
    TRUSTED_TREE_MANIFESTS,
    build_tree_manifest,
)


def stage_file(stage: Path, source: Path, name: str, *, executable_file=False) -> None:
    """Copy one regular input into a clean tree without archive path escapes."""
    from b300_core.runtime_integrity import _relative_name, _safe_path

    _relative_name(name)
    _safe_path(source.parent, source)
    if not source.is_file():
        raise RuntimeIntegrityError("Bundle input is not a regular file: " + str(source))
    target = stage.joinpath(*name.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        # Resources already present in an onedir tree may be supplied again.
        # Only identical duplicates are safe; a different payload is ambiguous.
        from b300_core.runtime_integrity import _digest
        if _digest(target) != _digest(source):
            raise RuntimeIntegrityError("Conflicting bundle path: " + name)
        return
    shutil.copy2(source, target)
    if executable_file:
        target.chmod(target.stat().st_mode | 0o755)


def stage_tree(stage: Path, source: Path, prefix: str = "", *, openocd=False) -> None:
    for path in runtime_files(source):
        relative = path.relative_to(source).as_posix()
        if openocd and relative == TREE_MANIFEST_NAME:
            continue
        stage_file(stage, path, prefix + relative,
                   executable_file=path.name in ("openocd", "arm-none-eabi-gdb"))


def resource_archive_name(resource: Path) -> str:
    """Preserve the runtime lookup path for trusted firmware resources."""
    if resource.parent.name == "firmware" and resource.parent.parent.name == "resources":
        return "resources/firmware/" + resource.name
    if resource.parent.name == "stlink-driver" and resource.parent.parent.name == "vendor":
        return "vendor/stlink-driver/" + resource.name
    return resource.name


def openocd_manifest(root: Path) -> bytes:
    return build_tree_manifest(root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flavor", required=True, choices=("gui", "cli"))
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--resource", action="append", default=[], type=Path)
    parser.add_argument("--application-root", type=Path)
    parser.add_argument("--openocd-root", required=True, type=Path)
    parser.add_argument("--bootstrap", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--version", default=TOOL_VERSION)
    parser.add_argument("--openocd-archive", required=True)
    parser.add_argument("--openocd-sha256", required=True)
    parser.add_argument("--openocd-package", required=True, type=Path)
    parser.add_argument("--gdb-root", type=Path)
    parser.add_argument("--gdb-archive")
    parser.add_argument("--gdb-sha256")
    parser.add_argument("--internal-distribution-approved", action="store_true")
    args = parser.parse_args(argv)
    if not args.internal_distribution_approved:
        parser.error("--internal-distribution-approved is required.")
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", args.openocd_sha256):
        parser.error("--openocd-sha256 must contain exactly 64 hexadecimal characters.")
    gdb_arguments = (args.gdb_root, args.gdb_archive, args.gdb_sha256)
    if any(argument is not None for argument in gdb_arguments):
        if any(argument is None for argument in gdb_arguments):
            parser.error("GDB runtime arguments must be supplied as a complete set.")
        if not re.fullmatch(r"[0-9A-Fa-f]{64}", args.gdb_sha256):
            parser.error("--gdb-sha256 must contain exactly 64 hexadecimal characters.")
    if args.flavor == "cli" and args.platform == "windows-x64":
        if args.application_root is None:
            parser.error("Windows CLI artifacts require --application-root for the onedir runtime.")
        if not (args.application_root / "_internal").is_dir():
            parser.error("Windows CLI --application-root is missing the _internal runtime.")
    required = [args.executable, args.openocd_root, args.bootstrap, args.openocd_package]
    if args.gdb_root is not None:
        required.append(args.gdb_root)
    if args.application_root is not None:
        required.append(args.application_root)
        try:
            args.executable.resolve().relative_to(args.application_root.resolve())
        except ValueError as error:
            parser.error("--executable must be inside --application-root")
    required.extend(args.resource)
    if not all(item.exists() for item in required):
        parser.error("A required bundle input is missing.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    metadata = (
        "platform=%s\nflavor=%s\nversion=%s\nopenocd=0.12.0-7\n"
        "openocd_archive=%s\nopenocd_sha256=%s\n" % (
            args.platform,
            args.flavor,
            args.version,
            args.openocd_archive,
            args.openocd_sha256.upper(),
        )
    ).encode("ascii")
    if args.gdb_root is not None:
        metadata += ("gdb=%s\ngdb_archive=%s\ngdb_sha256=%s\n" % (
            "15.2.1-1.1", args.gdb_archive, args.gdb_sha256.upper(),
        )).encode("ascii")
    manifest = openocd_manifest(args.openocd_root)
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    if manifest_digest != TRUSTED_TREE_MANIFESTS.get(args.platform):
        raise ValueError(
            "Expanded OpenOCD runtime does not match the built-in tree trust anchor."
        )
    if not (args.output.name.endswith(".tar.gz") or args.output.suffix == ".zip"):
        parser.error("Output must end with .zip or .tar.gz.")
    with tempfile.TemporaryDirectory(prefix="b300-runtime-") as directory:
        stage = Path(directory)
        if args.application_root is not None:
            stage_tree(stage, args.application_root)
        else:
            stage_file(stage, args.executable, args.executable.name, executable_file=True)
        for resource in args.resource:
            stage_file(stage, resource, resource_archive_name(resource))
        stage_file(stage, args.bootstrap, args.bootstrap.name, executable_file=True)
        stage_tree(stage, args.openocd_root, "vendor/openocd/", openocd=True)
        if args.gdb_root is not None:
            stage_tree(stage, args.gdb_root, "vendor/gdb/")
        (stage / "BUNDLE-METADATA.txt").write_bytes(metadata)
        (stage / "vendor/openocd" / TREE_MANIFEST_NAME).write_bytes(manifest)
        stage_file(stage, args.openocd_package, "vendor/packages/" + args.openocd_archive)
        write_runtime_manifest(stage, args.version)
        validate_runtime(stage, args.version)
        payload = sorted([*runtime_files(stage), stage / MANIFEST_NAME],
                         key=lambda path: path.relative_to(stage).as_posix())
        if args.output.name.endswith(".tar.gz"):
            with tarfile.open(args.output, "w:gz") as archive:
                for source in payload:
                    archive.add(source, source.relative_to(stage).as_posix(), recursive=False)
        else:
            with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
                for source in payload:
                    archive.write(source, source.relative_to(stage).as_posix())
    print("Created: %s" % args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
