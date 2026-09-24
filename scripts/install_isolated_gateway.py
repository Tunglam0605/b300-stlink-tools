#!/usr/bin/env python3
"""Preflight and a guarded marker transition for isolated Ubuntu Gateway setup.

The CLI deliberately exposes only read-only plan. A later administrator workflow
may call the root-only transition API; no apply or rollback CLI exists here.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


def _relative_name(name: str) -> None:
    """Reject archive names outside the portable runtime namespace."""
    parts = name.split("/")
    if any(not part or part in (".", "..") or part.endswith((".", " "))
           or re.search(r'[\\:<>"|?*\x00-\x1f\x7f]', part)
           or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", part)
           for part in parts):
        raise ValueError("Unsafe archive member name")


def _openocd_quiescent() -> bool:
    root = Path("/proc")
    if not root.is_dir():
        return False
    try:
        for entry in root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                name = (entry / "comm").read_text(encoding="ascii").strip().lower()
            except FileNotFoundError:
                continue
            if "openocd" in name:
                return False
        return True
    except (OSError, UnicodeError):
        return False


MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_JOB_RECORD_BYTES = 65536
MAX_STAGE_JOURNAL_BYTES = 16 * 1024 * 1024
MAX_STAGE_RECEIPT_BYTES = 8 * 1024 * 1024
EXPECTED_ARCHIVE_MEMBERS = frozenset({
    "BUNDLE-METADATA.txt", "B300-RUNTIME.sha256", "b300-stlink",
    "packaging/linux/b300-stlink-gateway-agent-system.service",
    "packaging/linux/b300-stlink-ingress.mount.in",
})
TERMINAL_JOBS = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
ACTIVE_JOBS = frozenset({"UPLOADING", "STAGED", "AWAITING_CONFIRMATION",
                         "RUNNING", "RECOVERY_REQUIRED"})
SYSTEM_UNIT = "b300-stlink-gateway-agent.service"
MOUNT_UNIT = r"var-spool-b300\x2dstlink-ingress.mount"
FILE_KEYS = ("legacy_bundle_manifest", "system_unit", "mount_unit",
             "agent_udev_rule", "legacy_udev_rule", "vendor_udev_rule")
GROUP_KEYS = ("b300-agent", "b300-probe", "b300-upload", "b300-operator",
              "plugdev", "sudo")
SYSTEM_OWNER_LOCK = Path("/var/lib/b300-stlink/gateway/hardware-owner.lock")
SYSTEM_MARKER = Path("/etc/b300-stlink/isolated-gateway.json")
EXACT_IDLE_OWNER_RECORD = b'\0{"schema_version":1,"state":"IDLE"}'


class TransitionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class StageError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class BoundaryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _LinuxFlock:
    @staticmethod
    def acquire(fd: int) -> None:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def release(fd: int) -> None:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _identity_numbers() -> tuple[int, int]:
    import grp
    import pwd
    return pwd.getpwnam("b300-agent").pw_uid, grp.getgrnam("b300-upload").gr_gid


def _escape_ingress_mount(path: str) -> str:
    command = ("/usr/bin/systemd-escape", "--path", "--suffix=mount", path)
    result = subprocess.run(command, capture_output=True, text=True,
                            timeout=4, check=False)
    if result.returncode != 0:
        raise StageError("SYSTEMD_ESCAPE_FAILED")
    return result.stdout.strip()


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o600) -> str:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags, mode)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "wb") as stream:
        if hasattr(os, "fchmod"):
            os.fchmod(stream.fileno(), mode)
        else:
            os.chmod(path, mode)
        stream.write(payload)
        digest.update(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return digest.hexdigest()


def _read_bounded_json(path: Path, *, maximum: int = 1024 * 1024) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        raise StageError("STAGE_EVIDENCE_UNSAFE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    with os.fdopen(descriptor, "rb") as stream:
        data = stream.read(maximum + 1)
    try:
        record = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise StageError("STAGE_EVIDENCE_UNSAFE") from error
    if not isinstance(record, dict):
        raise StageError("STAGE_EVIDENCE_UNSAFE")
    return record


def _runtime_manifest_expected(root: Path, version: str) -> dict[str, str]:
    path = root / "B300-RUNTIME.sha256"
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > 1024 * 1024):
        raise StageError("RUNTIME_MANIFEST_INVALID")
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise StageError("RUNTIME_MANIFEST_INVALID")
    lines = raw.decode("utf-8").splitlines()
    if not lines or lines[0] != "# B300 runtime " + version:
        raise StageError("RUNTIME_MANIFEST_INVALID")
    expected = {}
    for line in lines[1:]:
        match = re.fullmatch(r"([0-9a-f]{64}) \*([^\r\n]+)", line)
        if match is None:
            raise StageError("RUNTIME_MANIFEST_INVALID")
        digest, name = match.groups()
        _relative_name(name)
        if name in expected or name == "B300-RUNTIME.sha256":
            raise StageError("RUNTIME_MANIFEST_INVALID")
        expected[name] = digest
    return expected


def _hash_open_bundle(handle) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    total = 0
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        total += len(chunk)
        if total > MAX_BUNDLE_BYTES:
            raise StageError("BUNDLE_TOO_LARGE")
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


def _ensure_stage_parent(root: Path, relative: str, created: set[Path]) -> Path:
    current = root
    parts = relative.split("/")
    for part in parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            os.mkdir(current, 0o700)
            created.add(current)
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise StageError("STAGE_PATH_UNSAFE")
    return current / parts[-1]


def _extract_stage_archive(handle, root: Path, members: tuple[str, ...],
                           version: str) -> tuple[dict[str, str], set[Path]]:
    names = set()
    hashes = {}
    directories: set[Path] = {root}
    expanded = 0
    count = 0
    handle.seek(0)
    with tarfile.open(fileobj=handle, mode="r|gz") as archive:
        for member in archive:
            count += 1
            if count > MAX_ARCHIVE_MEMBERS:
                raise StageError("BUNDLE_MEMBER_LIMIT")
            if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                raise StageError("BUNDLE_MEMBER_LIMIT")
            expanded += member.size
            if expanded > MAX_EXPANDED_BYTES:
                raise StageError("BUNDLE_EXPANDED_LIMIT")
            _relative_name(member.name)
            if (len(member.name) > 240 or not member.isfile()
                    or member.name in names or member.name not in members):
                raise StageError("BUNDLE_MEMBER_UNSAFE")
            names.add(member.name)
            target = _ensure_stage_parent(root, member.name, directories)
            source = archive.extractfile(member)
            if source is None:
                raise StageError("BUNDLE_MEMBER_UNREADABLE")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(str(target), flags, 0o600)
            digest = hashlib.sha256()
            copied = 0
            with source, os.fdopen(descriptor, "wb") as output:
                selected_mode = 0o755 if member.mode & 0o111 else 0o644
                if hasattr(os, "fchmod"):
                    os.fchmod(output.fileno(), selected_mode)
                else:
                    os.chmod(target, selected_mode)
                while copied < member.size:
                    chunk = source.read(min(1024 * 1024, member.size - copied))
                    if not chunk:
                        raise StageError("BUNDLE_MEMBER_SHORT")
                    output.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            hashes[member.name] = digest.hexdigest()
    if names != set(members):
        raise StageError("BUNDLE_MEMBER_MISMATCH")
    expected = _runtime_manifest_expected(root, version)
    if expected != {name: digest for name, digest in hashes.items()
                    if name != "B300-RUNTIME.sha256"}:
        raise StageError("RUNTIME_MANIFEST_MISMATCH")
    return hashes, directories


def _render_stage_units(root: Path, agent_uid: int, upload_gid: int,
                        mount_name: str) -> tuple[dict[str, str], Path]:
    source_root = root / "packaging/linux"
    service_source = source_root / "b300-stlink-gateway-agent-system.service"
    mount_source = source_root / "b300-stlink-ingress.mount.in"
    if service_source.stat().st_size > 65536 or mount_source.stat().st_size > 65536:
        raise StageError("UNIT_TEMPLATE_INVALID")
    service = service_source.read_text(encoding="utf-8")
    mount = mount_source.read_text(encoding="utf-8")
    if ("BindsTo=" + mount_name not in service
            or "After=" + mount_name not in service
            or "ExecStart=/opt/b300-stlink/bin/b300-stlink debug gateway-agent --managed-child --json" not in service
            or "sudo " in service
            or mount.count("@AGENT_UID@") != 1 or mount.count("@UPLOAD_GID@") != 1
            or "Where=/var/spool/b300-stlink/ingress" not in mount
            or "size=65M,nr_inodes=256,nodev,nosuid,noexec" not in mount):
        raise StageError("UNIT_TEMPLATE_INVALID")
    rendered = mount.replace("@AGENT_UID@", str(agent_uid)).replace(
        "@UPLOAD_GID@", str(upload_gid))
    if "@" in rendered:
        raise StageError("UNIT_TEMPLATE_INVALID")
    systemd_root = root / "systemd"
    os.mkdir(systemd_root, 0o700)
    hashes = {
        "systemd/b300-stlink-gateway-agent.service": _write_exclusive(
            systemd_root / "b300-stlink-gateway-agent.service",
            service.encode("utf-8"), mode=0o644),
        "systemd/b300-stlink-ingress.mount.rendered": _write_exclusive(
            systemd_root / "b300-stlink-ingress.mount.rendered",
            rendered.encode("utf-8"), mode=0o644),
    }
    return hashes, systemd_root


def _planned_stage_paths(journal: Path, install_root: Path, candidates_root: Path,
                         temporary: Path, candidate: Path, completion_temp: Path,
                         members: tuple[str, ...]) -> list[str]:
    paths = {journal, completion_temp, install_root, candidates_root, temporary, candidate,
             temporary / "systemd", temporary / "STAGE-RECEIPT.json",
             temporary / "systemd/b300-stlink-gateway-agent.service",
             temporary / "systemd/b300-stlink-ingress.mount.rendered"}
    for name in members:
        target = temporary.joinpath(*name.split("/"))
        paths.add(target)
        current = target.parent
        while current != temporary:
            paths.add(current)
            current = current.parent
    return sorted(str(path) for path in paths)


def _hash_staged_file(path: Path, maximum: int = MAX_MEMBER_BYTES) -> str:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > maximum):
        raise StageError("RECOVERY_REQUIRED")
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(descriptor, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            if total > maximum:
                raise StageError("RECOVERY_REQUIRED")
            digest.update(chunk)
    return digest.hexdigest()


def _rename_noreplace(source: Path, destination: Path) -> None:
    if os.name != "posix":
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(str(destination))
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as error:
        raise StageError("ATOMIC_PROMOTE_UNAVAILABLE") from error
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _verify_staged_candidate(candidate: Path, journal: dict,
                             members: tuple[str, ...], expected_sha256: str) -> None:
    if (journal.get("status") != "COMPLETE"
            or journal.get("bundle_sha256") != expected_sha256
            or journal.get("candidate_dir") != str(candidate)
            or journal.get("members") != list(members)):
        raise StageError("RECOVERY_REQUIRED")
    try:
        receipt = _read_bounded_json(candidate / "STAGE-RECEIPT.json",
                                     maximum=MAX_STAGE_RECEIPT_BYTES)
        if (receipt.get("bundle_sha256") != expected_sha256
                or receipt.get("members") != list(members)
                or receipt.get("version") != journal.get("version")):
            raise StageError("RECOVERY_REQUIRED")
        member_hashes = receipt.get("member_sha256")
        if not isinstance(member_hashes, dict) or set(member_hashes) != set(members):
            raise StageError("RECOVERY_REQUIRED")
        expected = _runtime_manifest_expected(candidate, journal["version"])
        if set(expected) != set(members) - {"B300-RUNTIME.sha256"}:
            raise StageError("RECOVERY_REQUIRED")
        all_files = set()
        for directory, folders, files in os.walk(candidate, followlinks=False):
            for name in folders + files:
                item = Path(directory) / name
                info = item.lstat()
                if stat.S_ISLNK(info.st_mode):
                    raise StageError("RECOVERY_REQUIRED")
                if name in files:
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise StageError("RECOVERY_REQUIRED")
                    all_files.add(item.relative_to(candidate).as_posix())
                elif not stat.S_ISDIR(info.st_mode):
                    raise StageError("RECOVERY_REQUIRED")
        render_names = set(receipt.get("rendered_unit_sha256", {}))
        if all_files != set(members) | render_names | {"STAGE-RECEIPT.json"}:
            raise StageError("RECOVERY_REQUIRED")
        for name in members:
            path = candidate.joinpath(*name.split("/"))
            digest = _hash_staged_file(path)
            if digest != member_hashes[name]:
                raise StageError("RECOVERY_REQUIRED")
            if name != "B300-RUNTIME.sha256" and digest != expected[name]:
                raise StageError("RECOVERY_REQUIRED")
        for name, digest in receipt["rendered_unit_sha256"].items():
            if _hash_staged_file(candidate / name, 65536) != digest:
                raise StageError("RECOVERY_REQUIRED")
    except (OSError, ValueError, UnicodeError, KeyError, TypeError) as error:
        raise StageError("RECOVERY_REQUIRED") from error


def stage_candidate(
        plan: GatewayInstallPlan, bundle: Path, expected_sha256: str, *, host,
        install_root: Path = Path("/opt/b300-stlink"), trust_root: Path = Path("/"),
        trusted_uid: int = 0, path_stat: Callable = os.lstat,
        fd_stat: Callable = os.fstat, effective_uid: Callable = getattr(os, "geteuid", lambda: -1),
        system_name: str = sys.platform, identity_provider: Callable = _identity_numbers,
        escape_unit: Callable = _escape_ingress_mount,
        fsync_dir: Callable = _fsync_directory,
        promote: Optional[Callable] = None,
        probe_serial: Optional[str] = None) -> dict:
    """Stage one exact candidate without touching active software or services."""
    if system_name != "linux" or effective_uid() != 0:
        raise StageError("ROOT_LINUX_REQUIRED")
    selected = Path(bundle)
    digest = str(expected_sha256).lower()
    if (not isinstance(plan, GatewayInstallPlan) or not plan.ready
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or plan.candidate.get("path") != str(selected)
            or plan.candidate.get("sha256") != digest):
        raise StageError("PLAN_NOT_FRESH")
    fresh = build_plan(selected, digest, host=host, trust_root=trust_root,
                       trusted_uid=trusted_uid, path_stat=path_stat,
                       probe_serial=probe_serial)
    if not fresh.ready or fresh.candidate != plan.candidate:
        raise StageError("PLAN_NOT_FRESH")
    actual, metadata, members = _inspect_bundle(selected, include_members=True)
    if actual != digest or any(len(name) > 240 for name in members):
        raise StageError("BUNDLE_CHANGED")
    identities = identity_provider()
    if (not isinstance(identities, tuple) or len(identities) != 2
            or any(type(value) is not int or value <= 0 for value in identities)):
        raise StageError("IDENTITY_INVALID")
    agent_uid, upload_gid = identities
    mount_name = escape_unit("/var/spool/b300-stlink/ingress")
    if mount_name != MOUNT_UNIT:
        raise StageError("MOUNT_NAME_INVALID")
    root = Path(install_root)
    if not root.is_absolute() or root.name != "b300-stlink":
        raise StageError("STAGE_PATH_UNSAFE")
    _trusted_directory_chain(root.parent, owner_uid=trusted_uid, path_stat=path_stat)
    candidates_root = root / "candidates"
    candidate = candidates_root / (metadata["version"] + "-" + digest)
    journal = root.parent / (".b300-stlink-stage-" + digest + ".json")
    for directory in (root, candidates_root):
        try:
            info = path_stat(directory)
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != trusted_uid
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise StageError("STAGE_PATH_UNSAFE")
    if journal.exists() or journal.is_symlink():
        info = path_stat(journal)
        _trusted_regular(info, owner_uid=trusted_uid, maximum=MAX_STAGE_JOURNAL_BYTES)
        try:
            candidate_info = path_stat(candidate)
        except OSError as error:
            raise StageError("RECOVERY_REQUIRED") from error
        if (not stat.S_ISDIR(candidate_info.st_mode)
                or candidate_info.st_uid != trusted_uid
                or stat.S_IMODE(candidate_info.st_mode) & 0o022):
            raise StageError("RECOVERY_REQUIRED")
        record = _read_bounded_json(journal, maximum=MAX_STAGE_JOURNAL_BYTES)
        _verify_staged_candidate(candidate, record, members, digest)
        return {"state": "STAGED", "candidate_dir": str(candidate),
                "journal_path": str(journal), "reused": True}
    if candidate.exists() or candidate.is_symlink():
        raise StageError("STAGE_TARGET_OCCUPIED")
    temporary = root / (".stage-" + digest + "-" + secrets.token_hex(8))
    journal_tmp = root.parent / (".b300-stlink-stage-complete-" + secrets.token_hex(8))
    if temporary.exists() or temporary.is_symlink():
        raise StageError("STAGE_TARGET_OCCUPIED")
    planned = _planned_stage_paths(journal, root, candidates_root,
                                   temporary, candidate, journal_tmp, members)
    record = {"schema_version": 1, "status": "STAGING", "bundle_sha256": digest,
              "bundle_path": str(selected), "version": metadata["version"],
              "members": list(members), "candidate_dir": str(candidate),
              "temp_dir": str(temporary), "planned_paths": planned,
              "completion_temp": str(journal_tmp),
              "agent_uid": agent_uid, "upload_gid": upload_gid,
              "mount_unit_target": "/etc/systemd/system/" + mount_name}
    journal_payload = (json.dumps(record, sort_keys=True,
                                  separators=(",", ":")) + "\n").encode("utf-8")
    if len(journal_payload) > MAX_STAGE_JOURNAL_BYTES:
        raise StageError("STAGE_JOURNAL_TOO_LARGE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(str(selected), flags)
    with os.fdopen(descriptor, "rb") as archive_handle:
        source_info = fd_stat(archive_handle.fileno())
        path_info = path_stat(selected)
        if (not _same_inode(source_info, path_info) or not stat.S_ISREG(source_info.st_mode)
                or source_info.st_uid != trusted_uid or source_info.st_nlink != 1
                or stat.S_IMODE(source_info.st_mode) & 0o022):
            raise StageError("BUNDLE_CHANGED")
        if _hash_open_bundle(archive_handle) != digest:
            raise StageError("BUNDLE_CHANGED")
        try:
            _write_exclusive(journal, journal_payload)
            fsync_dir(root.parent)
            if not root.exists():
                os.mkdir(root, 0o755)
                fsync_dir(root.parent)
            if not candidates_root.exists():
                os.mkdir(candidates_root, 0o755)
                fsync_dir(root)
            os.mkdir(temporary, 0o700)
            fsync_dir(root)
            extracted, directories = _extract_stage_archive(
                archive_handle, temporary, members, metadata["version"])
            if _hash_open_bundle(archive_handle) != digest:
                raise StageError("BUNDLE_CHANGED")
            rendered, systemd_root = _render_stage_units(
                temporary, agent_uid, upload_gid, mount_name)
            directories.add(systemd_root)
            receipt = {"schema_version": 1, "bundle_sha256": digest,
                       "members": list(members), "version": metadata["version"],
                       "member_sha256": extracted,
                       "rendered_unit_sha256": rendered}
            receipt_payload = (json.dumps(receipt, sort_keys=True,
                                          separators=(",", ":")) + "\n").encode("utf-8")
            if len(receipt_payload) > MAX_STAGE_RECEIPT_BYTES:
                raise StageError("STAGE_RECEIPT_TOO_LARGE")
            _write_exclusive(temporary / "STAGE-RECEIPT.json", receipt_payload)
            for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
                fsync_dir(directory)
            if candidate.exists() or candidate.is_symlink():
                raise StageError("STAGE_TARGET_OCCUPIED")
            (promote or _rename_noreplace)(temporary, candidate)
            fsync_dir(candidates_root)
            completed = {**record, "status": "COMPLETE"}
            completed_payload = (json.dumps(completed, sort_keys=True,
                                            separators=(",", ":")) + "\n").encode("utf-8")
            if len(completed_payload) > MAX_STAGE_JOURNAL_BYTES:
                raise StageError("STAGE_JOURNAL_TOO_LARGE")
            _write_exclusive(journal_tmp, completed_payload)
            os.replace(journal_tmp, journal)
            fsync_dir(root.parent)
        except Exception as error:
            # Keep journal, temporary files, and any promoted candidate for
            # explicit manual recovery. Never infer a failed flash or retry.
            if isinstance(error, StageError):
                raise
            raise StageError("STAGING_INTERRUPTED") from error
    return {"state": "STAGED", "candidate_dir": str(candidate),
            "journal_path": str(journal), "reused": False}


def _trusted_directory_chain(path: Path, *, owner_uid: int,
                             path_stat: Callable) -> None:
    current = Path(path)
    while True:
        try:
            info = path_stat(current)
        except OSError as error:
            raise TransitionError("PATH_UNSAFE") from error
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise TransitionError("PATH_UNSAFE")
        if current.parent == current:
            return
        current = current.parent


def _trusted_regular(info, *, owner_uid: int, maximum: int) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1 or not 0 < info.st_size <= maximum):
        raise TransitionError("PATH_UNSAFE")


def _same_inode(left, right) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _read_active_marker(marker_path: Path, *, root_uid: int,
                        path_stat: Callable, fd_stat: Callable,
                        expected_enabled: bool = True) -> tuple[dict, object]:
    try:
        before = path_stat(marker_path)
        _trusted_regular(before, owner_uid=root_uid, maximum=4096)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(str(marker_path), flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = fd_stat(stream.fileno())
            _trusted_regular(opened, owner_uid=root_uid, maximum=4096)
            if not _same_inode(before, opened):
                raise TransitionError("MARKER_CHANGED")
            raw = stream.read(4097)
        record = json.loads(raw.decode("utf-8"))
    except TransitionError:
        raise
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise TransitionError("MARKER_INVALID") from error
    keys = {"schema_version", "socket_path", "state_root", "ingress_root",
            "operator_uid", "operator_gid", "flash_enabled"}
    if (not isinstance(record, dict) or set(record) != keys
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["socket_path"] != "/run/b300-stlink/agent.sock"
            or record["state_root"] != "/var/lib/b300-stlink/gateway"
            or record["ingress_root"] != "/var/spool/b300-stlink/ingress"
            or type(record["operator_uid"]) is not int or record["operator_uid"] < 0
            or type(record["operator_gid"]) is not int or record["operator_gid"] < 0
            or record["flash_enabled"] is not expected_enabled):
        raise TransitionError("MARKER_NOT_ACTIVE" if expected_enabled else "MARKER_NOT_PENDING")
    return record, opened


def _transition_active_to_pending(
        marker_path: Path, owner_lock_path: Path, *, probes, locker,
        effective_uid: Callable, system_name: str, trusted_uid: int,
        lock_uid: Optional[int] = None,
        path_stat: Callable = os.lstat, fd_stat: Callable = os.fstat,
        fsync_dir: Callable = _fsync_directory,
        clock: Callable = time.monotonic, sleep: Callable = time.sleep,
        timeout_seconds: float = 2.0,
        expected_enabled: bool = True, target_enabled: bool = False,
        extra_check: Optional[Callable] = None) -> dict:
    """Guard the marker switch with the Agent's existing persistent owner inode."""
    if system_name != "linux" or effective_uid() != 0:
        raise TransitionError("ROOT_LINUX_REQUIRED")
    if not 0 < timeout_seconds <= 5:
        raise ValueError("Lock timeout must be in (0, 5] seconds")
    marker = Path(marker_path)
    lock_path = Path(owner_lock_path)
    selected_lock_uid = trusted_uid if lock_uid is None else lock_uid
    _trusted_directory_chain(marker.parent, owner_uid=trusted_uid, path_stat=path_stat)
    _trusted_directory_chain(lock_path.parent.parent, owner_uid=trusted_uid,
                             path_stat=path_stat)
    try:
        lock_dir = path_stat(lock_path.parent)
        if (not stat.S_ISDIR(lock_dir.st_mode) or lock_dir.st_uid != selected_lock_uid
                or stat.S_IMODE(lock_dir.st_mode) & 0o077):
            raise TransitionError("PATH_UNSAFE")
        before = path_stat(lock_path)
        _trusted_regular(before, owner_uid=selected_lock_uid, maximum=4097)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(str(lock_path), flags)
    except TransitionError:
        raise
    except OSError as error:
        raise TransitionError("LOCK_MISSING") from error
    acquired = False
    temporary = None
    try:
        opened = fd_stat(descriptor)
        _trusted_regular(opened, owner_uid=selected_lock_uid, maximum=4097)
        if not _same_inode(before, opened):
            raise TransitionError("LOCK_REPLACED")
        deadline = clock() + timeout_seconds
        while True:
            try:
                locker.acquire(descriptor)
                acquired = True
                break
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK) and not isinstance(error, BlockingIOError):
                    raise TransitionError("LOCK_UNAVAILABLE") from error
                if clock() >= deadline:
                    raise TransitionError("LOCK_BUSY") from error
                sleep(min(0.02, max(0.0, deadline - clock())))
        rechecked = path_stat(lock_path)
        _trusted_regular(rechecked, owner_uid=selected_lock_uid, maximum=4097)
        if not _same_inode(rechecked, fd_stat(descriptor)):
            raise TransitionError("LOCK_REPLACED")
        os.lseek(descriptor, 0, os.SEEK_SET)
        durable = os.read(descriptor, 4098)
        if durable != EXACT_IDLE_OWNER_RECORD:
            try:
                record = json.loads(durable[1:].decode("ascii")) if durable[:1] == b"\0" else None
            except (UnicodeError, json.JSONDecodeError):
                record = None
            if isinstance(record, dict) and record.get("state") == "ACTIVE":
                raise TransitionError("OWNER_NOT_IDLE")
            raise TransitionError("OWNER_RECORD_INVALID")
        for name in ("agent_idle", "jobs_idle", "openocd_quiescent"):
            try:
                if getattr(probes, name)() is not True:
                    raise TransitionError("QUIESCENCE_UNKNOWN")
            except TransitionError:
                raise
            except Exception as error:
                raise TransitionError("QUIESCENCE_UNKNOWN") from error
        record, marker_identity = _read_active_marker(
            marker, root_uid=trusted_uid, path_stat=path_stat, fd_stat=fd_stat,
            expected_enabled=expected_enabled)
        if not _same_inode(path_stat(marker), marker_identity):
            raise TransitionError("MARKER_CHANGED")
        if extra_check is not None:
            extra_check()
        record["flash_enabled"] = target_enabled
        payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > 4096:
            raise TransitionError("MARKER_INVALID")
        try:
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=".isolated-gateway-", dir=str(marker.parent))
            temporary = Path(temporary_name)
            with os.fdopen(temporary_fd, "wb") as stream:
                if hasattr(os, "fchmod"):
                    os.fchmod(stream.fileno(), 0o600)
                else:
                    os.chmod(temporary, 0o600)
                if os.name == "posix":
                    os.fchown(stream.fileno(), trusted_uid, -1)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                fresh = fd_stat(stream.fileno())
                _trusted_regular(fresh, owner_uid=trusted_uid, maximum=4096)
            os.replace(temporary, marker)
            temporary = None
            fsync_dir(marker.parent)
        except TransitionError:
            raise
        except OSError as error:
            raise TransitionError("MARKER_WRITE_FAILED") from error
        return {"state": "ACTIVE" if target_enabled else "PENDING_REPLUG"}
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        try:
            if acquired:
                locker.release(descriptor)
        finally:
            os.close(descriptor)


def transition_active_to_pending(*, probes, timeout_seconds: float = 2.0) -> dict:
    """Root-only production entry point; no CLI command calls it yet."""
    if not sys.platform.startswith("linux") or getattr(os, "geteuid", lambda: -1)() != 0:
        raise TransitionError("ROOT_LINUX_REQUIRED")
    import pwd
    agent_uid = pwd.getpwnam("b300-agent").pw_uid
    return _transition_active_to_pending(
        SYSTEM_MARKER, SYSTEM_OWNER_LOCK, probes=probes, locker=_LinuxFlock(),
        effective_uid=os.geteuid, system_name="linux", trusted_uid=0,
        lock_uid=agent_uid, timeout_seconds=timeout_seconds)


_USB_OPEN_SOURCE = (
    "import errno,os,sys\n"
    "flags=os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0)\n"
    "try: fd=os.open(sys.argv[1],flags)\n"
    "except OSError as error: sys.exit(13 if error.errno in (errno.EACCES,errno.EPERM) else 14)\n"
    "else: os.close(fd)\n"
)


def _run_boundary_command(command: tuple[str, ...]):
    if not (command in (("/usr/bin/setpriv", "--version"),
                        ("/usr/bin/python3", "--version"))
            or (command and command[0] == "/usr/bin/setpriv"
                and "/usr/bin/python3" in command and command[-2] == _USB_OPEN_SOURCE)):
        raise BoundaryError("OPEN_COMMAND_UNSAFE")
    return subprocess.run(command, capture_output=True, text=True,
                          timeout=4, check=False)


def _boundary_identities() -> dict:
    import grp
    import pwd
    agent = pwd.getpwnam("b300-agent")
    operator = pwd.getpwnam("aubot")
    probe_gid = grp.getgrnam("b300-probe").gr_gid
    upload_gid = grp.getgrnam("b300-upload").gr_gid
    operator_access_gid = grp.getgrnam("b300-operator").gr_gid
    return {
        "agent_uid": agent.pw_uid, "agent_gid": agent.pw_gid,
        "probe_gid": probe_gid, "upload_gid": upload_gid,
        "operator_access_gid": operator_access_gid,
        "operator_uid": operator.pw_uid, "operator_gid": operator.pw_gid,
        "operator_groups": tuple(os.getgrouplist("aubot", operator.pw_gid)),
        "agent_groups": (probe_gid, upload_gid, operator_access_gid),
    }


def _bounded_ingress_mount(agent_uid: int, upload_gid: int) -> bool:
    command = ("/usr/bin/findmnt", "--json", "--bytes", "--output",
               "TARGET,FSTYPE,OPTIONS,SIZE", "--mountpoint",
               "/var/spool/b300-stlink/ingress")
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=4, check=False)
        if result.returncode != 0:
            return False
        filesystems = json.loads(result.stdout).get("filesystems")
        if not isinstance(filesystems, list) or len(filesystems) != 1:
            return False
        item = filesystems[0]
        options = str(item.get("options", "")).split(",")
        values = dict(part.split("=", 1) for part in options if "=" in part)
        return (item.get("target") == "/var/spool/b300-stlink/ingress"
                and item.get("fstype") == "tmpfs"
                and int(item.get("size", -1)) == 65 * 1024 * 1024
                and {"nodev", "nosuid", "noexec"}.issubset(set(options))
                and values.get("nr_inodes") == "256"
                and values.get("uid") == str(agent_uid)
                and values.get("gid") == str(upload_gid)
                and values.get("mode", "").lstrip("0") == "710")
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError,
            subprocess.TimeoutExpired):
        return False


def _open_tool_versions(runner: Callable) -> None:
    try:
        setpriv = runner(("/usr/bin/setpriv", "--version"))
        python = runner(("/usr/bin/python3", "--version"))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BoundaryError("OPEN_TOOLS_UNAVAILABLE") from error
    if (setpriv.returncode != 0 or python.returncode != 0
            or re.search(r"util-linux [0-9]+\.[0-9]+", str(setpriv.stdout)) is None
            or re.search(r"Python 3\.(?:9|[1-9][0-9])(?:\.|\s)",
                         str(python.stdout)) is None):
        raise BoundaryError("OPEN_TOOLS_UNAVAILABLE")


def _unprivileged_open_result(node: str, identities: dict, *, agent: bool,
                              runner: Callable) -> int:
    if re.fullmatch(r"/dev/bus/usb/[0-9]{3}/[0-9]{3}", node) is None:
        raise BoundaryError("USB_NODE_UNSAFE")
    uid_key, gid_key = (("agent_uid", "agent_gid") if agent
                        else ("operator_uid", "operator_gid"))
    command = ["/usr/bin/setpriv", "--reuid", str(identities[uid_key]),
               "--regid", str(identities[gid_key])]
    if agent:
        command.append("--groups=" + ",".join(str(gid) for gid in identities["agent_groups"]))
    else:
        command.append("--init-groups")
    command.extend(("--bounding-set=-all", "--inh-caps=-all",
                    "--no-new-privs", "--reset-env", "/usr/bin/python3",
                    "-I", "-c", _USB_OPEN_SOURCE, node))
    try:
        result = runner(tuple(command))
        return int(result.returncode)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise BoundaryError("USB_OPEN_UNKNOWN") from error


def _validate_boundary_identities(identities: dict) -> None:
    fields = ("agent_uid", "agent_gid", "probe_gid", "upload_gid",
              "operator_access_gid", "operator_uid", "operator_gid")
    if (not isinstance(identities, dict)
            or any(type(identities.get(field)) is not int or identities[field] <= 0
                   for field in fields)
            or identities["agent_uid"] == identities["operator_uid"]):
        raise BoundaryError("IDENTITY_INVALID")
    agent_groups = identities.get("agent_groups")
    operator_groups = identities.get("operator_groups")
    if (not isinstance(agent_groups, (tuple, list))
            or not isinstance(operator_groups, (tuple, list))
            or any(type(value) is not int or value <= 0
                   for value in (*agent_groups, *operator_groups))
            or not {identities["probe_gid"], identities["upload_gid"],
                    identities["operator_access_gid"]}.issubset(set(agent_groups))
            or identities["probe_gid"] in operator_groups
            or identities["probe_gid"] == identities["operator_gid"]):
        raise BoundaryError("IDENTITY_INVALID")


def _verify_boundary(
        marker_path: Path, owner_lock_path: Path, *, probe_serial: Optional[str],
        probe_provider: Callable, mount_check: Callable, identity_provider: Callable,
        node_stat: Callable, runner: Callable, probes, locker,
        effective_uid: Callable, system_name: str, trusted_uid: int,
        lock_uid: Optional[int] = None, path_stat: Callable = os.lstat,
        fd_stat: Callable = os.fstat, fsync_dir: Callable = _fsync_directory,
        timeout_seconds: float = 2.0) -> dict:
    """Activate only after duplicate boundary proofs, including one under flock."""
    if system_name != "linux" or effective_uid() != 0:
        raise BoundaryError("ROOT_LINUX_REQUIRED")
    marker = Path(marker_path)
    _trusted_directory_chain(marker.parent, owner_uid=trusted_uid, path_stat=path_stat)
    _read_active_marker(marker, root_uid=trusted_uid, path_stat=path_stat,
                        fd_stat=fd_stat, expected_enabled=False)
    try:
        identities = identity_provider()
    except (OSError, KeyError) as error:
        raise BoundaryError("IDENTITY_INVALID") from error
    _validate_boundary_identities(identities)
    first_identity = [None]
    last_node = [None]

    def prove() -> None:
        try:
            if getattr(probes, "service_idle")() is not True:
                raise BoundaryError("SERVICE_NOT_IDLE")
        except BoundaryError:
            raise
        except Exception as error:
            raise BoundaryError("SERVICE_NOT_IDLE") from error
        try:
            if mount_check(identities["agent_uid"], identities["upload_gid"]) is not True:
                raise BoundaryError("INGRESS_MOUNT_UNSAFE")
        except BoundaryError:
            raise
        except Exception as error:
            raise BoundaryError("INGRESS_MOUNT_UNSAFE") from error
        try:
            evidence = probe_provider(probe_serial)
        except Exception as error:
            raise BoundaryError("PROBE_NOT_UNIQUE") from error
        if (not isinstance(evidence, dict) or evidence.get("count") != 1
                or evidence.get("incomplete_count") != 0
                or evidence.get("selected") is not True):
            raise BoundaryError("PROBE_NOT_UNIQUE")
        node = evidence.get("node")
        if not isinstance(node, str) or re.fullmatch(
                r"/dev/bus/usb/[0-9]{3}/[0-9]{3}", node) is None:
            raise BoundaryError("USB_NODE_UNSAFE")
        if (evidence.get("uid") != 0 or evidence.get("gid") != identities["probe_gid"]
                or evidence.get("mode") != "0660"
                or evidence.get("device_type") != "char"
                or evidence.get("acl_known") is not True
                or evidence.get("operator_acl") is not False):
            raise BoundaryError("USB_ACL_UNSAFE")
        try:
            before = node_stat(node)
        except OSError as error:
            raise BoundaryError("USB_NODE_UNSAFE") from error
        identity = (node, before.st_dev, before.st_ino)
        if (not stat.S_ISCHR(before.st_mode) or before.st_uid != 0
                or before.st_gid != identities["probe_gid"]
                or stat.S_IMODE(before.st_mode) != 0o660
                or (before.st_dev, before.st_ino) !=
                   (evidence.get("st_dev"), evidence.get("st_ino"))):
            raise BoundaryError("USB_NODE_UNSAFE")
        if first_identity[0] is not None and first_identity[0] != identity:
            raise BoundaryError("PROBE_CHANGED")
        first_identity[0] = identity
        _open_tool_versions(runner)
        agent_result = _unprivileged_open_result(node, identities, agent=True, runner=runner)
        if agent_result == 13:
            raise BoundaryError("AGENT_USB_DENIED")
        if agent_result != 0:
            raise BoundaryError("USB_OPEN_UNKNOWN")
        operator_result = _unprivileged_open_result(
            node, identities, agent=False, runner=runner)
        if operator_result == 0:
            raise BoundaryError("OPERATOR_USB_ALLOWED")
        if operator_result != 13:
            raise BoundaryError("USB_OPEN_UNKNOWN")
        after = node_stat(node)
        if (not stat.S_ISCHR(after.st_mode) or after.st_uid != 0
                or after.st_gid != identities["probe_gid"]
                or stat.S_IMODE(after.st_mode) != 0o660
                or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)):
            raise BoundaryError("PROBE_CHANGED")
        last_node[0] = node

    for name in ("agent_idle", "jobs_idle", "openocd_quiescent"):
        try:
            if getattr(probes, name)() is not True:
                raise BoundaryError("QUIESCENCE_UNKNOWN")
        except BoundaryError:
            raise
        except Exception as error:
            raise BoundaryError("QUIESCENCE_UNKNOWN") from error
    prove()
    _transition_active_to_pending(
        marker, owner_lock_path, probes=probes, locker=locker,
        effective_uid=effective_uid, system_name=system_name,
        trusted_uid=trusted_uid, lock_uid=lock_uid,
        path_stat=path_stat, fd_stat=fd_stat, fsync_dir=fsync_dir,
        timeout_seconds=timeout_seconds, expected_enabled=False,
        target_enabled=True, extra_check=prove)
    return {"state": "ACTIVE", "verified": True, "probe_node": last_node[0],
            "checks": {"probe": True, "usb_acl": True, "agent_open": True,
                       "operator_denied": True, "bounded_ingress": True,
                       "quiescent": True}}


def verify_boundary(*, probe_serial: Optional[str], probes,
                    timeout_seconds: float = 2.0) -> dict:
    """Root-only production boundary verification; no CLI command calls it."""
    if not sys.platform.startswith("linux") or getattr(os, "geteuid", lambda: -1)() != 0:
        raise BoundaryError("ROOT_LINUX_REQUIRED")
    identities = _boundary_identities()
    host = LinuxHostProbe()
    return _verify_boundary(
        SYSTEM_MARKER, SYSTEM_OWNER_LOCK, probe_serial=probe_serial,
        probe_provider=lambda serial: host._probe_state(serial, {"exists": True,
            "gid": identities["probe_gid"]}),
        mount_check=_bounded_ingress_mount,
        identity_provider=lambda: identities,
        node_stat=os.lstat, runner=_run_boundary_command,
        probes=probes, locker=_LinuxFlock(), effective_uid=os.geteuid,
        system_name="linux", trusted_uid=0, lock_uid=identities["agent_uid"],
        timeout_seconds=timeout_seconds)


@dataclass(frozen=True)
class GatewayInstallPlan:
    candidate: dict
    host: dict
    rollback_inventory: dict
    blockers: tuple[dict, ...]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_record(self) -> dict:
        return {"schema_version": 1, "ready": self.ready,
                "decision": "GO" if self.ready else "NO_GO",
                "candidate": dict(self.candidate), "host": dict(self.host),
                "rollback_inventory": dict(self.rollback_inventory),
                "blockers": [dict(item) for item in self.blockers]}


def _block(blockers: list, code: str, detail: str) -> None:
    blockers.append({"code": code, "detail": detail})


def _bundle_path_safe(bundle: Path, trusted_root: Path, trusted_uid: int,
                      path_stat: Callable) -> bool:
    if (not bundle.is_absolute() or not trusted_root.is_absolute()
            or ".." in bundle.parts or ".." in trusted_root.parts
            or not str(bundle).endswith(".tar.gz")):
        return False
    try:
        relative = bundle.relative_to(trusted_root)
        paths = [trusted_root]
        for part in relative.parts:
            paths.append(paths[-1] / part)
        for index, path in enumerate(paths):
            info = path_stat(path)
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != trusted_uid
                    or stat.S_IMODE(info.st_mode) & 0o022):
                return False
            if index == len(paths) - 1:
                if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
                    return False
                if not 0 < info.st_size <= MAX_BUNDLE_BYTES:
                    return False
            elif not stat.S_ISDIR(info.st_mode):
                return False
        return True
    except (OSError, ValueError, AttributeError):
        return False


def _inspect_bundle(bundle: Path, *, include_members: bool = False):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(str(bundle), flags)
    with os.fdopen(fd, "rb") as handle:
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BUNDLE_BYTES:
                raise ValueError("Bundle exceeds the size limit")
            digest.update(chunk)
        handle.seek(0)
        with tarfile.open(fileobj=handle, mode="r|gz") as archive:
            names = set()
            expanded = 0
            count = 0
            metadata_raw = None
            for member in archive:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise ValueError("Bundle has too many members")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise ValueError("Bundle member exceeds size limit")
                expanded += member.size
                if expanded > MAX_EXPANDED_BYTES:
                    raise ValueError("Bundle expanded size exceeds limit")
                _relative_name(member.name)
                if not member.isfile() or member.name in names:
                    raise ValueError("Bundle contains duplicate or non-regular members")
                names.add(member.name)
                if member.name == "BUNDLE-METADATA.txt":
                    if member.size > 4096:
                        raise ValueError("Bundle metadata exceeds limit")
                    metadata_file = archive.extractfile(member)
                    if metadata_file is None:
                        raise ValueError("Bundle metadata is unreadable")
                    metadata_raw = metadata_file.read(4097)
            if not EXPECTED_ARCHIVE_MEMBERS.issubset(names):
                raise ValueError("Bundle lacks required isolated Gateway files")
            if metadata_raw is None:
                raise ValueError("Bundle metadata is unreadable")
            raw = metadata_raw.decode("ascii")
            metadata = {}
            for line in raw.splitlines():
                key, separator, value = line.partition("=")
                if not separator or key in metadata:
                    raise ValueError("Bundle metadata is malformed")
                metadata[key] = value
            if (metadata.get("platform") not in {"linux-x64", "linux-arm64"}
                    or metadata.get("flavor") not in {"gui", "cli"}
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*",
                                        metadata.get("version", ""))):
                raise ValueError("Bundle metadata has unsupported target")
        result = (digest.hexdigest(), metadata)
        return (*result, tuple(sorted(names))) if include_members else result


def inspect_job_states(root: Path) -> dict[str, int]:
    """Count bounded private job states; discard every other record field."""
    target = Path(root)
    try:
        root_info = target.lstat()
    except FileNotFoundError:
        return {}
    except OSError:
        return {"CORRUPT": 1}
    if not stat.S_ISDIR(root_info.st_mode):
        return {"CORRUPT": 1}
    counts: dict[str, int] = {}
    try:
        for directory in target.iterdir():
            try:
                name = directory.name
                if len(name) != 32 or any(character not in "0123456789abcdef" for character in name):
                    raise ValueError("Unexpected job name")
                directory_info = directory.lstat()
                record_path = directory / "job.json"
                record_info = record_path.lstat()
                if (not stat.S_ISDIR(directory_info.st_mode)
                        or not stat.S_ISREG(record_info.st_mode)
                        or record_info.st_nlink != 1
                        or record_info.st_size > MAX_JOB_RECORD_BYTES):
                    raise ValueError("Unsafe job record")
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(str(record_path), flags)
                with os.fdopen(descriptor, "rb") as stream:
                    record = json.loads(stream.read(MAX_JOB_RECORD_BYTES + 1).decode("utf-8"))
                state = record.get("state") if isinstance(record, dict) else None
                if record.get("job_id") != name or state not in TERMINAL_JOBS | ACTIVE_JOBS:
                    raise ValueError("Invalid job state")
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError, AttributeError):
                state = "CORRUPT"
            counts[state] = counts.get(state, 0) + 1
    except OSError:
        counts["CORRUPT"] = counts.get("CORRUPT", 0) + 1
    return counts


def _safe_inventory(evidence: dict) -> tuple[dict, dict]:
    services = {key: {"enabled": evidence.get("services", {}).get(key, {}).get("enabled"),
                      "active": evidence.get("services", {}).get(key, {}).get("active")}
                for key in ("legacy_user", "system_agent", "ingress_mount")}
    def safe_jobs(raw) -> dict:
        if not isinstance(raw, dict):
            return {"CORRUPT": 1}
        counts = {}
        for state, count in raw.items():
            if (state not in TERMINAL_JOBS | ACTIVE_JOBS | {"CORRUPT"}
                    or type(count) is not int or count < 0):
                counts["CORRUPT"] = counts.get("CORRUPT", 0) + 1
            else:
                counts[state] = counts.get(state, 0) + count
        return counts

    jobs = {key: safe_jobs(evidence.get("jobs", {}).get(key))
            for key in ("legacy", "system")}
    def safe_digest(value):
        return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) else None

    files = {key: {"exists": bool(evidence.get("files", {}).get(key, {}).get("exists")),
                   "sha256": safe_digest(evidence.get("files", {}).get(key, {}).get("sha256"))}
             for key in FILE_KEYS}
    groups = {key: {field: value for field, value in evidence.get("groups", {}).get(key, {}).items()
                    if field in {"exists", "gid", "operator_member"}}
              for key in GROUP_KEYS}
    probe = evidence.get("probe", {})
    public_probe = {key: probe.get(key) for key in
                    ("count", "incomplete_count", "selected", "node", "uid", "gid", "mode",
                     "agent_owned", "acl_known", "operator_acl")}
    host = {"os_name": evidence.get("os_name"),
            "distribution": evidence.get("distribution"),
            "machine": evidence.get("machine"),
            "operator_uid": evidence.get("operator_uid"),
            "openocd_quiescent": evidence.get("openocd_quiescent"),
            "probe": public_probe}
    inventory = {"operator_home": evidence.get("operator_home"),
                 "services": services, "owner_states": {
                     key: evidence.get("owner_states", {}).get(key)
                     for key in ("legacy", "system")},
                 "lease_present": {key: evidence.get("lease_present", {}).get(key)
                                   for key in ("legacy", "system")},
                 "jobs": jobs, "groups": groups, "files": files,
                 "path_hazards": list(evidence.get("path_hazards", ()))}
    return host, inventory


def _readonly_command(command: tuple[str, ...], operator_name: str) -> bool:
    if not command:
        return False
    if command[0] == "getfacl":
        return (len(command) == 3 and command[1] == "-cp"
                and re.fullmatch(r"/dev/bus/usb/[0-9]{3}/[0-9]{3}", command[2]) is not None)
    if command[0] != "systemctl":
        return False
    if len(command) == 3:
        return (command[1] in {"is-enabled", "is-active"}
                and command[2] in {SYSTEM_UNIT, MOUNT_UNIT})
    return (len(command) == 5 and command[1:3] == (
                "--user", "--machine=%s@.host" % operator_name)
            and command[3] in {"is-enabled", "is-active"}
            and command[4] == SYSTEM_UNIT)


def _run_readonly(command: tuple[str, ...]):
    if not _readonly_command(command, "aubot"):
        raise ValueError("Unsupported read-only host query")
    executable = {"systemctl": "/usr/bin/systemctl",
                  "getfacl": "/usr/bin/getfacl"}.get(command[0])
    if executable is None:
        raise ValueError("Unsupported read-only host query")
    return subprocess.run((executable, *command[1:]), capture_output=True,
                          text=True, timeout=4, check=False)


def _account(name: str):
    import pwd
    return pwd.getpwnam(name)


def _group(name: str):
    import grp
    return grp.getgrnam(name)


class LinuxHostProbe:
    """Sanitized Linux observations; every subprocess is a bounded query."""

    def __init__(self, *, root: Path = Path("/"), runner: Callable = _run_readonly,
                 system_name: Optional[str] = None, machine: Optional[str] = None,
                 operator_name: str = "aubot", account_lookup: Callable = _account,
                 group_lookup: Callable = _group,
                 quiescent_probe: Callable = _openocd_quiescent) -> None:
        self.root = Path(root)
        self.runner = runner
        self.system_name = system_name
        self.machine = machine
        self.operator_name = operator_name
        self.account_lookup = account_lookup
        self.group_lookup = group_lookup
        self.quiescent_probe = quiescent_probe

    def _mapped(self, absolute: str | Path) -> Path:
        return self.root / Path(absolute).as_posix().lstrip("/")

    def _query(self, command: tuple[str, ...]) -> Optional[str]:
        if _readonly_command(command, self.operator_name):
            try:
                result = self.runner(command)
                return str(result.stdout).strip() if result.returncode in (0, 1, 3, 4) else None
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return None
        raise ValueError("Unsupported read-only host query")

    def _service_state(self, name: str, *, user: bool = False) -> dict:
        prefix = ("systemctl", "--user", "--machine=%s@.host" % self.operator_name) if user else ("systemctl",)
        enabled = self._query((*prefix, "is-enabled", name))
        active = self._query((*prefix, "is-active", name))
        return {
            "enabled": (True if enabled in {"enabled", "enabled-runtime"}
                        else False if enabled in {"disabled", "masked", "static", "indirect",
                                                  "not-found", "generated", "transient"} else None),
            "active": (True if active == "active"
                       else False if active in {"inactive", "failed", "dead"} else None),
        }

    def _file_evidence(self, absolute: str | Path) -> dict:
        path = self._mapped(absolute)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return {"exists": False}
        except OSError:
            return {"exists": True, "sha256": None}
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            return {"exists": True, "sha256": None}
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(str(path), flags)
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
            return {"exists": True, "sha256": digest.hexdigest()}
        except OSError:
            return {"exists": True, "sha256": None}

    def _present(self, absolute: str | Path) -> Optional[bool]:
        try:
            self._mapped(absolute).lstat()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return None

    def _owner_state(self, absolute: str | Path) -> str:
        path = self._mapped(absolute)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return "MISSING"
        except OSError:
            return "CORRUPT"
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 4097:
            return "CORRUPT"
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(str(path), flags)
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read(4098)
            if len(data) > 4097 or data[:1] != b"\0":
                return "CORRUPT"
            if len(data) == 1:
                return "IDLE"
            record = json.loads(data[1:].decode("ascii"))
            if not isinstance(record, dict) or record.get("schema_version") != 1:
                return "CORRUPT"
            if set(record) == {"schema_version", "state"} and record.get("state") == "IDLE":
                return "IDLE"
            if (set(record) == {"schema_version", "state", "pid", "instance_id"}
                    and record.get("state") == "ACTIVE"
                    and type(record.get("pid")) is int and record["pid"] > 0
                    and isinstance(record.get("instance_id"), str)
                    and re.fullmatch(r"[0-9a-f]{32}", record["instance_id"])):
                return "ACTIVE"
            return "CORRUPT"
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            return "CORRUPT"

    def _group_state(self, name: str, operator) -> dict:
        try:
            selected = self.group_lookup(name)
        except (KeyError, OSError):
            return {"exists": False}
        return {"exists": True, "gid": selected.gr_gid,
                "operator_member": bool(operator is not None and (
                    selected.gr_gid == operator.pw_gid
                    or self.operator_name in selected.gr_mem))}

    def _distribution(self) -> Optional[str]:
        path = self._mapped("/etc/os-release")
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if os.readlink(path) not in {"../usr/lib/os-release", "/usr/lib/os-release"}:
                    return None
                path = self._mapped("/usr/lib/os-release")
                info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return None
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(str(path), flags)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                for line in stream:
                    if line.startswith("ID="):
                        return line.partition("=")[2].strip().strip('"').lower()
        except (OSError, UnicodeError):
            return None
        return None

    def _path_hazards(self) -> tuple[str, ...]:
        targets = (
            "/opt/b300-stlink", "/etc/b300-stlink/isolated-gateway.json",
            "/etc/systemd/system/" + SYSTEM_UNIT,
            "/etc/systemd/system/" + MOUNT_UNIT,
            "/etc/udev/rules.d/99-b300-agent.rules",
            "/var/lib/b300-stlink/gateway", "/var/spool/b300-stlink/ingress",
        )
        hazards = set()
        for target in targets:
            current = self.root
            for part in Path(target).as_posix().lstrip("/").split("/"):
                current = current / part
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    break
                except OSError:
                    hazards.add(target)
                    break
                if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0
                        or stat.S_IMODE(info.st_mode) & 0o022):
                    hazards.add(target)
                    break
        return tuple(sorted(hazards))

    def _probe_state(self, probe_serial: Optional[str], agent_group: dict) -> dict:
        sysfs = self._mapped("/sys/bus/usb/devices")
        matches = []
        count = 0
        incomplete_count = 0
        try:
            devices = tuple(sysfs.iterdir())
        except OSError:
            devices = ()
        for directory in devices:
            try:
                vendor = (directory / "idVendor").read_text(encoding="ascii").strip().lower()
                product = (directory / "idProduct").read_text(encoding="ascii").strip().lower()
            except (OSError, UnicodeError):
                continue
            if (vendor, product) != ("0483", "3748"):
                continue
            count += 1
            try:
                serial = (directory / "serial").read_text(encoding="ascii").strip()
                bus = int((directory / "busnum").read_text(encoding="ascii"))
                number = int((directory / "devnum").read_text(encoding="ascii"))
                node = "/dev/bus/usb/%03d/%03d" % (bus, number)
                info = self._mapped(node).lstat()
                matches.append((serial, node, info))
            except (OSError, ValueError, UnicodeError):
                incomplete_count += 1
        selected = ([item for item in matches if item[0] == probe_serial]
                    if probe_serial is not None else matches if count == 1 else [])
        if incomplete_count:
            selected = []
        result = {"count": count, "incomplete_count": incomplete_count,
                  "selected": len(selected) == 1,
                  "node": None, "uid": None, "gid": None, "mode": None,
                  "device_type": None, "st_dev": None, "st_ino": None,
                  "agent_owned": False, "acl_known": False, "operator_acl": None}
        if len(selected) == 1:
            _, node, info = selected[0]
            acl = self._query(("getfacl", "-cp", node))
            result.update(node=node, uid=info.st_uid, gid=info.st_gid,
                          mode="%04o" % stat.S_IMODE(info.st_mode),
                          device_type="char" if stat.S_ISCHR(info.st_mode) else "other",
                          st_dev=info.st_dev, st_ino=info.st_ino,
                          agent_owned=bool(agent_group.get("exists") and
                                           agent_group.get("gid") == info.st_gid),
                          acl_known=acl is not None,
                          operator_acl=(None if acl is None else any(
                              re.fullmatch(r"user:%s:[rwx-]{3}" % re.escape(self.operator_name),
                                           line.partition("#")[0].strip()) is not None
                              for line in acl.splitlines())))
        return result

    def inspect(self, probe_serial: Optional[str] = None) -> dict:
        os_name = (self.system_name or platform.system()).lower()
        machine = self.machine or platform.machine()
        if os_name != "linux":
            return {"os_name": os_name, "machine": machine}
        try:
            operator = self.account_lookup(self.operator_name)
        except (KeyError, OSError):
            operator = None
        home = str(operator.pw_dir) if operator is not None else None
        groups = {name: self._group_state(name, operator) for name in GROUP_KEYS}
        legacy_root = (Path(home) / ".b300-stlink/gateway-runtime") if home else None
        system_root = Path("/var/lib/b300-stlink/gateway")
        files = {
            "legacy_bundle_manifest": self._file_evidence(
                Path(home) / ".local/share/b300-stlink/B300-RUNTIME.sha256") if home else {"exists": False},
            "system_unit": self._file_evidence("/etc/systemd/system/" + SYSTEM_UNIT),
            "mount_unit": self._file_evidence("/etc/systemd/system/" + MOUNT_UNIT),
            "agent_udev_rule": self._file_evidence("/etc/udev/rules.d/99-b300-agent.rules"),
            "legacy_udev_rule": self._file_evidence("/etc/udev/rules.d/49-b300-stlink.rules"),
            "vendor_udev_rule": self._file_evidence("/usr/lib/udev/rules.d/49-b300-stlink.rules"),
        }
        return {
            "os_name": os_name, "distribution": self._distribution(),
            "machine": machine,
            "operator_uid": operator.pw_uid if operator is not None else None,
            "operator_home": home,
            "services": {
                "legacy_user": self._service_state(SYSTEM_UNIT, user=True),
                "system_agent": self._service_state(SYSTEM_UNIT),
                "ingress_mount": self._service_state(MOUNT_UNIT),
            },
            "owner_states": {
                "legacy": self._owner_state(Path(home) / ".b300-stlink/hardware-owner.lock") if home else "UNKNOWN",
                "system": self._owner_state(system_root / "hardware-owner.lock"),
            },
            "lease_present": {
                "legacy": self._present(legacy_root / "lease.json") if legacy_root else None,
                "system": self._present(system_root / "lease.json"),
            },
            "jobs": {
                "legacy": inspect_job_states(self._mapped(legacy_root / "program-jobs")) if legacy_root else {"CORRUPT": 1},
                "system": inspect_job_states(self._mapped(system_root / "program-jobs")),
            },
            "openocd_quiescent": self.quiescent_probe(),
            "probe": self._probe_state(probe_serial, groups["b300-agent"]),
            "groups": groups, "files": files,
            "path_hazards": self._path_hazards(),
        }


def build_plan(bundle: Path, expected_sha256: str, *, host=None,
               trust_root: Path = Path("/"), trusted_uid: int = 0,
               path_stat: Callable = os.lstat, probe_serial: Optional[str] = None) -> GatewayInstallPlan:
    """Gather an immutable decision record; never write or start any service."""
    selected = Path(bundle)
    blockers: list[dict] = []
    candidate = {"path": str(selected), "expected_sha256": expected_sha256.lower(),
                 "sha256": None, "platform": None, "flavor": None, "version": None}
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        _block(blockers, "EXPECTED_HASH_INVALID", "Expected SHA-256 must be 64 hex digits.")
    if not _bundle_path_safe(selected, Path(trust_root), trusted_uid, path_stat):
        _block(blockers, "BUNDLE_PATH_UNSAFE", "Bundle path, type, ownership or mode is unsafe.")
    else:
        try:
            actual, metadata = _inspect_bundle(selected)
            candidate.update(sha256=actual, platform=metadata["platform"],
                             flavor=metadata["flavor"], version=metadata["version"])
            if actual != expected_sha256.lower():
                _block(blockers, "BUNDLE_HASH_MISMATCH", "Candidate archive SHA-256 differs from expected.")
        except (OSError, ValueError, UnicodeError, tarfile.TarError) as error:
            _block(blockers, "BUNDLE_INVALID", "Candidate archive is unreadable or invalid.")
    try:
        evidence = (host or LinuxHostProbe()).inspect(probe_serial=probe_serial)
        public_host, inventory = _safe_inventory(evidence)
    except (OSError, ValueError, TypeError, KeyError):
        public_host, inventory = _safe_inventory({})
        _block(blockers, "HOST_INSPECTION_FAILED", "Host evidence could not be inspected safely.")
    if public_host["os_name"] != "linux":
        _block(blockers, "HOST_UNSUPPORTED", "System installer plan requires Ubuntu Linux.")
    if public_host["distribution"] != "ubuntu":
        _block(blockers, "HOST_NOT_UBUNTU", "Host distribution is not verified Ubuntu.")
    machine_platform = {"x86_64": "linux-x64", "amd64": "linux-x64",
                        "aarch64": "linux-arm64", "arm64": "linux-arm64"}.get(
                            str(public_host["machine"]).lower())
    if machine_platform is None or candidate["platform"] != machine_platform:
        _block(blockers, "BUNDLE_ARCH_MISMATCH", "Candidate does not match host CPU architecture.")
    if public_host["operator_uid"] is None:
        _block(blockers, "OPERATOR_MISSING", "Approved SSH operator account is absent.")
    for name, state in inventory["services"].items():
        if type(state["enabled"]) is not bool or type(state["active"]) is not bool:
            _block(blockers, "SERVICE_STATE_UNKNOWN", "A required service state is unknown.")
            break
    for name in ("system_agent", "ingress_mount"):
        if inventory["services"][name]["enabled"] or inventory["services"][name]["active"]:
            _block(blockers, "SYSTEM_SERVICE_PRESENT", "An isolated system unit is already enabled or active.")
            break
    if any(value not in {"IDLE", "MISSING"} for value in inventory["owner_states"].values()):
        _block(blockers, "OWNER_NOT_IDLE", "Hardware owner evidence is active or uncertain.")
    if any(value is not False for value in inventory["lease_present"].values()):
        _block(blockers, "LEASE_PRESENT", "Lease evidence requires manual review.")
    if any(count for group in inventory["jobs"].values() for state, count in group.items()
           if state not in TERMINAL_JOBS):
        _block(blockers, "ACTIVE_JOBS", "Active or corrupt job evidence requires manual review.")
    if public_host["openocd_quiescent"] is not True:
        _block(blockers, "OPENOCD_NOT_QUIESCENT", "OpenOCD is running or its state is unknown.")
    probe = public_host["probe"]
    if type(probe["incomplete_count"]) is not int or probe["incomplete_count"] != 0:
        _block(blockers, "PROBE_INCOMPLETE", "At least one matching ST-Link has incomplete identity or node evidence.")
    if (type(probe["count"]) is not int or probe["count"] < 1
            or probe["selected"] is not True
            or probe["count"] > 1 and probe_serial is None):
        _block(blockers, "PROBE_NOT_UNIQUE", "Exactly one intended ST-Link must be selected.")
    if probe["agent_owned"] is True:
        _block(blockers, "PROBE_ALREADY_AGENT_OWNED", "ST-Link is already assigned to the Agent.")
    if probe["acl_known"] is not True:
        _block(blockers, "USB_ACL_UNKNOWN", "Current USB ACL could not be inspected.")
    if inventory["path_hazards"]:
        _block(blockers, "PATH_UNSAFE", "An exact system target path is unsafe.")
    if any(inventory["files"][name]["exists"] for name in
           ("system_unit", "mount_unit", "agent_udev_rule")):
        _block(blockers, "SYSTEM_TARGET_OCCUPIED", "An installer-owned target already exists.")
    if any(inventory["files"][name]["exists"] and inventory["files"][name]["sha256"] is None
           for name in ("legacy_bundle_manifest", "legacy_udev_rule", "vendor_udev_rule")):
        _block(blockers, "FILE_HASH_UNKNOWN", "An existing rollback input could not be hashed.")
    return GatewayInstallPlan(candidate, public_host, inventory, tuple(blockers))


def main(argv=None, *, host=None, trust_root: Path = Path("/"),
         trusted_uid: int = 0, path_stat: Callable = os.lstat, output=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    selected = subcommands.add_parser("plan", help="read-only Gateway migration preflight")
    selected.add_argument("--bundle", required=True, type=Path)
    selected.add_argument("--expected-sha256", required=True)
    selected.add_argument("--probe-serial")
    selected.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    plan = build_plan(args.bundle, args.expected_sha256, host=host,
                      trust_root=trust_root, trusted_uid=trusted_uid,
                      path_stat=path_stat, probe_serial=args.probe_serial)
    stream = output or sys.stdout
    print(json.dumps(plan.to_record(), sort_keys=True, separators=(",", ":")), file=stream)
    return 0 if plan.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
