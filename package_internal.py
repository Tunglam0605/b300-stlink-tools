#!/usr/bin/env python3
"""Create a portable platform-specific B300 ST-Link archive."""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import tarfile
import zipfile
from pathlib import Path

from b300_version import __version__ as TOOL_VERSION
from b300_core.offline_setup import (
    TREE_MANIFEST_NAME,
    TRUSTED_TREE_MANIFESTS,
    build_tree_manifest,
)


def add_tree_zip(archive: zipfile.ZipFile, root: Path) -> None:
    for source in root.rglob("*"):
        if source.is_file() and source != root / TREE_MANIFEST_NAME:
            archive.write(source, "vendor/openocd/" + source.relative_to(root).as_posix())


def executable(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mode |= 0o755
    return info


def add_tree_tar(archive: tarfile.TarFile, root: Path) -> None:
    for source in root.rglob("*"):
        if source.is_file() and source != root / TREE_MANIFEST_NAME:
            archive.add(source, "vendor/openocd/" + source.relative_to(root).as_posix(),
                        filter=executable if source.name == "openocd" else None)


def openocd_manifest(root: Path) -> bytes:
    return build_tree_manifest(root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--gui-executable", type=Path)
    parser.add_argument("--resource", action="append", default=[], type=Path)
    parser.add_argument("--openocd-root", required=True, type=Path)
    parser.add_argument("--bootstrap", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--version", default=TOOL_VERSION)
    parser.add_argument("--openocd-archive", required=True)
    parser.add_argument("--openocd-sha256", required=True)
    parser.add_argument("--openocd-package", required=True, type=Path)
    parser.add_argument("--internal-distribution-approved", action="store_true")
    args = parser.parse_args(argv)
    if not args.internal_distribution_approved:
        parser.error("--internal-distribution-approved is required.")
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", args.openocd_sha256):
        parser.error("--openocd-sha256 must contain exactly 64 hexadecimal characters.")
    required = [args.executable, args.openocd_root, args.bootstrap, args.openocd_package]
    if args.gui_executable is not None:
        required.append(args.gui_executable)
    required.extend(args.resource)
    if not all(item.exists() for item in required):
        parser.error("A required bundle input is missing.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    metadata = (
        "platform=%s\nversion=%s\nopenocd=0.12.0-7\n"
        "openocd_archive=%s\nopenocd_sha256=%s\n" % (
            args.platform,
            args.version,
            args.openocd_archive,
            args.openocd_sha256.upper(),
        )
    ).encode("ascii")
    manifest = openocd_manifest(args.openocd_root)
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    if manifest_digest != TRUSTED_TREE_MANIFESTS.get(args.platform):
        raise ValueError(
            "Expanded OpenOCD runtime does not match the built-in tree trust anchor."
        )
    if args.output.name.endswith(".tar.gz"):
        with tarfile.open(args.output, "w:gz") as archive:
            archive.add(args.executable, arcname=args.executable.name, filter=executable)
            if args.gui_executable is not None:
                archive.add(args.gui_executable, arcname=args.gui_executable.name,
                            filter=executable)
            for resource in args.resource:
                archive.add(resource, arcname=resource.name)
            archive.add(args.bootstrap, arcname=args.bootstrap.name, filter=executable)
            add_tree_tar(archive, args.openocd_root)
            info = tarfile.TarInfo("BUNDLE-METADATA.txt")
            info.size = len(metadata)
            archive.addfile(info, io.BytesIO(metadata))
            manifest_info = tarfile.TarInfo("vendor/openocd/%s" % TREE_MANIFEST_NAME)
            manifest_info.size = len(manifest)
            archive.addfile(manifest_info, io.BytesIO(manifest))
            archive.add(
                args.openocd_package,
                arcname="vendor/packages/%s" % args.openocd_archive,
            )
    elif args.output.suffix == ".zip":
        with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(args.executable, args.executable.name)
            if args.gui_executable is not None:
                archive.write(args.gui_executable, args.gui_executable.name)
            for resource in args.resource:
                archive.write(resource, resource.name)
            archive.write(args.bootstrap, args.bootstrap.name)
            add_tree_zip(archive, args.openocd_root)
            archive.writestr("BUNDLE-METADATA.txt", metadata)
            archive.writestr("vendor/openocd/%s" % TREE_MANIFEST_NAME, manifest)
            archive.write(
                args.openocd_package,
                "vendor/packages/%s" % args.openocd_archive,
            )
    else:
        parser.error("Output must end with .zip or .tar.gz.")
    print("Created: %s" % args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
