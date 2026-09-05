"""Deterministic integrity checks for complete portable application payloads.

The manifest detects incomplete or corrupted installations; publisher authenticity
continues to come from the signed release metadata and archive digest.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

MANIFEST_NAME = "B300-RUNTIME.sha256"


class RuntimeIntegrityError(ValueError):
    """The application runtime cannot be verified."""


def _version(version: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", version):
        raise RuntimeIntegrityError("Invalid runtime version.")
    return "# B300 runtime " + version


def _relative_name(name: str) -> None:
    parts = name.split("/")
    if any(not part or part in (".", "..") or part.endswith((".", " "))
           or re.search(r'[\\:<>"|?*\x00-\x1f\x7f]', part)
           or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", part)
           for part in parts):
        raise RuntimeIntegrityError("Unsafe runtime path: " + repr(name))


def _safe_path(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
        current = root
        for part in ("", *relative.parts):
            if part:
                current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RuntimeIntegrityError("Runtime links are not allowed: " + str(current))
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as error:
        raise RuntimeIntegrityError("Unsafe or missing runtime path: " + str(path)) from error


def runtime_files(root: Path) -> list[Path]:
    """List regular staged files in deterministic order, rejecting links/aliases."""
    root = Path(root)
    _safe_path(root, root)
    paths = []
    seen = set()
    def unreadable(error: OSError) -> None:
        raise RuntimeIntegrityError("Cannot enumerate runtime payload: " + str(error)) from error

    for directory, folders, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in folders + files:
            path = Path(directory) / name
            _safe_path(root, path)
            relative = path.relative_to(root).as_posix()
            _relative_name(relative)
            if relative.casefold() in seen:
                raise RuntimeIntegrityError("Duplicate runtime path: " + relative)
            seen.add(relative.casefold())
            if path.is_dir():
                continue
            if not stat.S_ISREG(path.stat().st_mode):
                raise RuntimeIntegrityError("Runtime payload must contain regular files: " + relative)
            if relative != MANIFEST_NAME:
                paths.append(path)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_runtime_manifest(root: Path, version: str) -> Path:
    """Write the complete staged tree's manifest and return its path."""
    root = Path(root)
    lines = [_version(version)]
    for path in runtime_files(root):
        lines.append(_digest(path) + " *" + path.relative_to(root).as_posix())
    if len(lines) == 1:
        raise RuntimeIntegrityError("Runtime payload is empty.")
    target = root / MANIFEST_NAME
    target.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return target


def validate_runtime(root: Path, expected_version: str) -> None:
    """Verify every manifest entry; unrelated user files are allowed.

    Raises RuntimeIntegrityError for missing, malformed, unsafe, or mismatched
    manifest/payload data. Reads payload bytes in bounded chunks.
    """
    root = Path(root)
    try:
        manifest = root / MANIFEST_NAME
        _safe_path(root, manifest)
        with manifest.open("r", encoding="utf-8", newline="") as stream:
            if stream.readline() != _version(expected_version) + "\n":
                raise RuntimeIntegrityError("Runtime manifest version/header does not match.")
            seen = set()
            for line in stream:
                match = re.fullmatch(r"([0-9a-f]{64}) \*([^\r\n]+)\n", line)
                if match is None:
                    raise RuntimeIntegrityError("Malformed runtime manifest entry.")
                digest, name = match.groups()
                _relative_name(name)
                key = name.casefold()
                if key == MANIFEST_NAME.casefold() or key in seen:
                    raise RuntimeIntegrityError("Duplicate or self-referencing runtime entry: " + name)
                seen.add(key)
                path = root.joinpath(*name.split("/"))
                _safe_path(root, path)
                if not path.is_file() or _digest(path) != digest:
                    raise RuntimeIntegrityError("Runtime file missing or corrupted: " + name)
            if not seen:
                raise RuntimeIntegrityError("Runtime manifest has no payload entries.")
    except (OSError, UnicodeError) as error:
        raise RuntimeIntegrityError("Cannot read runtime manifest or payload: " + str(error)) from error
