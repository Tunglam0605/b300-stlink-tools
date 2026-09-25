#!/usr/bin/env python3
"""Preflight and explicitly confirmed isolated Ubuntu Gateway migration.\n\nThe read-only plan command never mutates the host. The apply command is root-only,\nrequires explicit system-change confirmation, stages an exact hash-pinned bundle,\nvalidates the least-privilege hardware boundary, and rolls service/udev selection\nback to the legacy user Agent if a pre-flash migration gate fails. Neither command\nflashes the MCU.\n"""

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
                        path_stat: Callable, fd_stat: Callable) -> tuple[dict, object]:
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
            or record["flash_enabled"] is not True):
        raise TransitionError("MARKER_NOT_ACTIVE")
    return record, opened


def _transition_active_to_pending(
        marker_path: Path, owner_lock_path: Path, *, probes, locker,
        effective_uid: Callable, system_name: str, trusted_uid: int,
        lock_uid: Optional[int] = None,
        path_stat: Callable = os.lstat, fd_stat: Callable = os.fstat,
        fsync_dir: Callable = _fsync_directory,
        clock: Callable = time.monotonic, sleep: Callable = time.sleep,
        timeout_seconds: float = 2.0) -> dict:
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
            marker, root_uid=trusted_uid, path_stat=path_stat, fd_stat=fd_stat)
        if not _same_inode(path_stat(marker), marker_identity):
            raise TransitionError("MARKER_CHANGED")
        record["flash_enabled"] = False
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
        return {"state": "PENDING_REPLUG"}
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
    if command[0] == "/usr/sbin/runuser":
        if len(command) != 11:
            return False
        if command[1:4] != ("-u", operator_name, "--"):
            return False
        if command[4] != "/usr/bin/env" or command[7:9] != ("/usr/bin/systemctl", "--user"):
            return False
        uid_match = re.fullmatch(r"XDG_RUNTIME_DIR=/run/user/([0-9]+)", command[5])
        bus_match = re.fullmatch(
            r"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/([0-9]+)/bus", command[6])
        return (uid_match is not None and bus_match is not None
                and uid_match.group(1) == bus_match.group(1)
                and command[9] in {"is-enabled", "is-active"}
                and command[10] == SYSTEM_UNIT)
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
    if command[0] == "/usr/sbin/runuser":
        invocation = command
    else:
        executable = {"systemctl": "/usr/bin/systemctl",
                      "getfacl": "/usr/bin/getfacl"}.get(command[0])
        if executable is None:
            raise ValueError("Unsupported read-only host query")
        invocation = (executable, *command[1:])
    return subprocess.run(invocation, capture_output=True,
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
        if user and getattr(os, "geteuid", lambda: -1)() == 0:
            import pwd
            operator_uid = pwd.getpwnam(self.operator_name).pw_uid
            prefix = (
                "/usr/sbin/runuser", "-u", self.operator_name, "--",
                "/usr/bin/env", "XDG_RUNTIME_DIR=/run/user/%d" % operator_uid,
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%d/bus" % operator_uid,
                "/usr/bin/systemctl", "--user",
            )
        elif user:
            prefix = ("systemctl", "--user", "--machine=%s@.host" % self.operator_name)
        else:
            prefix = ("systemctl",)
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
                  "agent_owned": False, "acl_known": False, "operator_acl": None}
        if len(selected) == 1:
            _, node, info = selected[0]
            acl = self._query(("getfacl", "-cp", node))
            result.update(node=node, uid=info.st_uid, gid=info.st_gid,
                          mode="%04o" % stat.S_IMODE(info.st_mode),
                          agent_owned=bool(agent_group.get("exists") and
                                           agent_group.get("gid") == info.st_gid),
                          acl_known=acl is not None,
                          operator_acl=(None if acl is None else any(
                              re.fullmatch(r"user:%s:rw[x-]" % re.escape(self.operator_name),
                                           line) is not None
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



TRUSTED_BUNDLE_ROOT = Path("/var/lib/b300-stlink-installer")
SYSTEM_RUNTIME_ROOT = Path("/opt/b300-stlink")
SYSTEM_RUNTIME_BIN = SYSTEM_RUNTIME_ROOT / "bin/b300-stlink"
SYSTEMD_ROOT = Path("/etc/systemd/system")
AGENT_UDEV_RULE = Path("/etc/udev/rules.d/99-b300-agent.rules")
LEGACY_UDEV_OVERRIDE = Path("/etc/udev/rules.d/49-b300-stlink.rules")
AGENT_UDEV_RULE_TEXT = (
    '# B300 isolated Gateway: only b300-probe may open ST-Link devices.\n'
    'SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="374?", '
    'MODE="0660", GROUP="b300-probe"\n'
)
LEGACY_UDEV_MASK_TEXT = (
    "# B300 isolated Gateway overrides the vendor 49-b300-stlink.rules file.\\n"
    "# Direct plugdev/uaccess permission is intentionally disabled.\\n"
)


def _checked_command(command, *, timeout: float = 30.0, input_text: Optional[str] = None,
                     runner=subprocess.run):
    try:
        result = runner(tuple(str(item) for item in command), capture_output=True, text=True,
                        input=input_text, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise StageError("SYSTEM_COMMAND_FAILED") from error
    if result.returncode != 0:
        raise StageError("SYSTEM_COMMAND_FAILED")
    return result


def _safe_mkdir(path: Path, mode: int, *, owner_uid: int = 0, owner_gid: int = 0) -> None:
    selected = Path(path)
    selected.mkdir(parents=True, exist_ok=True)
    info = selected.lstat()
    if not stat.S_ISDIR(info.st_mode) or selected.is_symlink():
        raise StageError("SYSTEM_PATH_UNSAFE")
    os.chown(selected, owner_uid, owner_gid)
    os.chmod(selected, mode)


def _trusted_bundle_copy(source: Path, expected_sha256: str, *,
                         destination_root: Path = TRUSTED_BUNDLE_ROOT) -> Path:
    if not sys.platform.startswith("linux") or getattr(os, "geteuid", lambda: -1)() != 0:
        raise StageError("ROOT_LINUX_REQUIRED")
    digest = str(expected_sha256).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise StageError("EXPECTED_HASH_INVALID")
    root = Path(destination_root)
    _safe_mkdir(root, 0o700)
    destination = root / ("candidate-" + digest + ".tar.gz")
    if destination.exists() or destination.is_symlink():
        info = destination.lstat()
        _trusted_regular(info, owner_uid=0, maximum=MAX_BUNDLE_BYTES)
        with destination.open("rb") as stream:
            if _hash_open_bundle(stream) != digest:
                raise StageError("TRUSTED_BUNDLE_CONFLICT")
        return destination

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(str(Path(source)), flags)
    temporary = root / (".candidate-" + secrets.token_hex(8))
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size <= 0 or opened.st_size > MAX_BUNDLE_BYTES):
            raise StageError("BUNDLE_PATH_UNSAFE")
        out_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        out_fd = os.open(str(temporary), out_flags, 0o600)
        calculated = hashlib.sha256()
        total = 0
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as source_stream, \
                    os.fdopen(out_fd, "wb") as output:
                while True:
                    chunk = source_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BUNDLE_BYTES:
                        raise StageError("BUNDLE_TOO_LARGE")
                    calculated.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if calculated.hexdigest() != digest:
            temporary.unlink(missing_ok=True)
            raise StageError("BUNDLE_HASH_MISMATCH")
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        _rename_noreplace(temporary, destination)
        _fsync_directory(root)
        return destination
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _ensure_group(name: str, *, runner=subprocess.run) -> bool:
    import grp
    try:
        grp.getgrnam(name)
        return False
    except KeyError:
        _checked_command(("/usr/sbin/groupadd", "--system", name), runner=runner)
        return True


def _ensure_isolated_identities(operator_name: str, *, runner=subprocess.run) -> dict:
    import grp
    import pwd
    created_groups = []
    for name in ("b300-agent", "b300-probe", "b300-upload", "b300-operator"):
        if _ensure_group(name, runner=runner):
            created_groups.append(name)
    created_user = False
    try:
        agent = pwd.getpwnam("b300-agent")
    except KeyError:
        _checked_command((
            "/usr/sbin/useradd", "--system", "--gid", "b300-agent",
            "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin",
            "b300-agent",
        ), runner=runner)
        created_user = True
        agent = pwd.getpwnam("b300-agent")
    if agent.pw_uid <= 0:
        raise StageError("IDENTITY_INVALID")
    operator = pwd.getpwnam(operator_name)
    _checked_command((
        "/usr/sbin/usermod", "-a", "-G",
        "b300-probe,b300-upload,b300-operator", "b300-agent",
    ), runner=runner)
    _checked_command((
        "/usr/sbin/usermod", "-a", "-G", "b300-upload,b300-operator", operator_name,
    ), runner=runner)
    return {
        "agent_uid": agent.pw_uid,
        "agent_gid": grp.getgrnam("b300-agent").gr_gid,
        "probe_gid": grp.getgrnam("b300-probe").gr_gid,
        "upload_gid": grp.getgrnam("b300-upload").gr_gid,
        "operator_uid": operator.pw_uid,
        "operator_gid": grp.getgrnam("b300-operator").gr_gid,
        "created_user": created_user,
        "created_groups": created_groups,
    }


def _copy_staged_runtime(candidate: Path, *, runtime_root: Path = SYSTEM_RUNTIME_ROOT) -> None:
    import shutil
    candidate = Path(candidate)
    root = Path(runtime_root)
    for required in ("b300-stlink", "B300-RUNTIME.sha256", "vendor"):
        if not (candidate / required).exists():
            raise StageError("STAGED_RUNTIME_INCOMPLETE")
    reserved = {"candidates", "bin"}
    for source in candidate.iterdir():
        if source.name in {"systemd", "STAGE-RECEIPT.json"}:
            continue
        destination = root / source.name
        if destination.name in reserved or destination.exists() or destination.is_symlink():
            raise StageError("ACTIVE_RUNTIME_OCCUPIED")
        if source.is_dir():
            shutil.copytree(source, destination, symlinks=False)
        elif source.is_file():
            shutil.copy2(source, destination)
        else:
            raise StageError("STAGED_RUNTIME_UNSAFE")
    bin_dir = root / "bin"
    if bin_dir.exists() or bin_dir.is_symlink():
        raise StageError("ACTIVE_RUNTIME_OCCUPIED")
    os.mkdir(bin_dir, 0o755)
    shutil.copy2(candidate / "b300-stlink", bin_dir / "b300-stlink")
    os.chmod(bin_dir / "b300-stlink", 0o755)
    for directory, folders, files in os.walk(root):
        for name in folders:
            os.chown(Path(directory) / name, 0, 0)
        for name in files:
            os.chown(Path(directory) / name, 0, 0)
    _fsync_directory(root)


def _atomic_marker(record: dict, *, marker: Path = SYSTEM_MARKER) -> None:
    parent = marker.parent
    _safe_mkdir(parent, 0o755)
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > 4096:
        raise StageError("MARKER_INVALID")
    temporary = parent / (".isolated-gateway-" + secrets.token_hex(8))
    _write_exclusive(temporary, payload, mode=0o600)
    os.chown(temporary, 0, 0)
    os.replace(temporary, marker)
    _fsync_directory(parent)


def _write_exact_file(path: Path, payload: bytes, mode: int = 0o644) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    if selected.exists() or selected.is_symlink():
        raise StageError("SYSTEM_TARGET_OCCUPIED")
    _write_exclusive(selected, payload, mode=mode)
    os.chown(selected, 0, 0)
    _fsync_directory(selected.parent)


def _run_as_operator(operator: str, command, *, timeout: float = 30.0,
                     runner=subprocess.run):
    return _checked_command(("/usr/sbin/runuser", "-u", operator, "--", *command),
                            timeout=timeout, runner=runner)


def _user_systemctl(operator: str, *arguments, runner=subprocess.run):
    import pwd
    uid = pwd.getpwnam(operator).pw_uid
    return _run_as_operator(operator, (
        "/usr/bin/env", "XDG_RUNTIME_DIR=/run/user/%d" % uid,
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%d/bus" % uid,
        "/usr/bin/systemctl", "--user", *arguments,
    ), runner=runner)


def _json_from_output(output: str) -> dict:
    for line in reversed(str(output).splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            return record
    raise StageError("CLI_RESPONSE_INVALID")


def _verify_system_agent(operator: str, *, cli: Path = SYSTEM_RUNTIME_BIN,
                         runner=subprocess.run) -> dict:
    deadline = time.monotonic() + 15.0
    status = None
    while time.monotonic() < deadline:
        try:
            result = _run_as_operator(
                operator, (str(cli), "debug", "gateway-agent-status", "--json"),
                timeout=10.0, runner=runner)
            status = _json_from_output(result.stdout)
            if status.get("status") == "ok" and status.get("state") == "IDLE":
                break
        except StageError:
            pass
        time.sleep(0.25)
    if not isinstance(status, dict) or status.get("state") != "IDLE":
        raise StageError("SYSTEM_AGENT_NOT_READY")

    request_id = uuid.uuid4().hex if "uuid" in globals() else secrets.token_hex(16)
    client_id = "isolated-install-" + secrets.token_hex(6)
    acquired = _run_as_operator(operator, (
        str(cli), "debug", "gateway-acquire",
        "--request-id", request_id,
        "--client-id", client_id,
        "--client-label", "Isolated Gateway installation validation",
        "--lease-mode", "LIVE_WATCH", "--json",
    ), timeout=20.0, runner=runner)
    acquire_record = _json_from_output(acquired.stdout)
    result = acquire_record.get("result") if isinstance(acquire_record.get("result"), dict) else acquire_record
    lease_id = result.get("lease_id")
    lease_token = result.get("lease_token")
    generation = result.get("lease_generation", result.get("generation"))
    if (not isinstance(lease_id, str) or not lease_id
            or not isinstance(lease_token, str) or not lease_token
            or type(generation) is not int or generation <= 0):
        raise StageError("LIVE_WATCH_VALIDATION_FAILED")
    try:
        if not result.get("tcl_endpoint") and result.get("state") not in {"ACTIVE", "READY"}:
            raise StageError("LIVE_WATCH_VALIDATION_FAILED")
    finally:
        _run_as_operator(operator, (
            str(cli), "debug", "gateway-release",
            "--request-id", secrets.token_hex(16),
            "--lease-id", lease_id, "--lease-token", lease_token,
            "--lease-generation", str(generation), "--json",
        ), timeout=15.0, runner=runner)

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        checked = _run_as_operator(
            operator, (str(cli), "debug", "gateway-agent-status", "--json"),
            timeout=10.0, runner=runner)
        final_status = _json_from_output(checked.stdout)
        if final_status.get("status") == "ok" and final_status.get("state") == "IDLE":
            return final_status
        time.sleep(0.25)
    raise StageError("LIVE_WATCH_CLEANUP_FAILED")


def _set_flash_enabled(record: dict, enabled: bool, *, marker: Path = SYSTEM_MARKER) -> dict:
    updated = dict(record)
    updated["flash_enabled"] = bool(enabled)
    _atomic_marker(updated, marker=marker)
    return updated


def _rollback_isolated_install(operator: str, *, runner=subprocess.run) -> None:
    try:
        if SYSTEM_MARKER.is_file():
            try:
                record = json.loads(SYSTEM_MARKER.read_text(encoding="utf-8"))
                if isinstance(record, dict):
                    _set_flash_enabled(record, False)
            except Exception:
                pass
        subprocess.run(("/usr/bin/systemctl", "disable", "--now", SYSTEM_UNIT),
                       capture_output=True, text=True, timeout=20, check=False)
        subprocess.run(("/usr/bin/systemctl", "disable", "--now", MOUNT_UNIT),
                       capture_output=True, text=True, timeout=20, check=False)
        for path in (SYSTEMD_ROOT / SYSTEM_UNIT, SYSTEMD_ROOT / MOUNT_UNIT,
                     AGENT_UDEV_RULE, LEGACY_UDEV_OVERRIDE, SYSTEM_MARKER):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        subprocess.run(("/usr/bin/systemctl", "daemon-reload"),
                       capture_output=True, text=True, timeout=15, check=False)
        subprocess.run(("/usr/bin/udevadm", "control", "--reload-rules"),
                       capture_output=True, text=True, timeout=15, check=False)
        subprocess.run(("/usr/bin/udevadm", "trigger", "--subsystem-match=usb", "--action=change"),
                       capture_output=True, text=True, timeout=20, check=False)
        try:
            _user_systemctl(operator, "enable", "--now", SYSTEM_UNIT, runner=runner)
        except Exception:
            pass
    except Exception:
        pass


def apply_isolated_gateway(bundle: Path, expected_sha256: str, *,
                           operator: str = "aubot", probe_serial: Optional[str] = None,
                           confirmed: bool = False, runner=subprocess.run) -> dict:
    """Install and validate the isolated system Agent; never flashes the MCU."""
    if not confirmed:
        raise StageError("SYSTEM_CHANGE_CONFIRMATION_REQUIRED")
    if not sys.platform.startswith("linux") or getattr(os, "geteuid", lambda: -1)() != 0:
        raise StageError("ROOT_LINUX_REQUIRED")

    trusted_bundle = _trusted_bundle_copy(bundle, expected_sha256)
    host = LinuxHostProbe(operator_name=operator)
    plan = build_plan(trusted_bundle, expected_sha256, host=host,
                      probe_serial=probe_serial)
    if not plan.ready:
        raise StageError("PLAN_NOT_READY")

    inventory_root = TRUSTED_BUNDLE_ROOT / "rollback"
    _safe_mkdir(inventory_root, 0o700)
    inventory_path = inventory_root / (
        "pre-migration-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + ".json")
    _write_exclusive(
        inventory_path,
        (json.dumps(plan.to_record(), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        mode=0o600,
    )
    identities = _ensure_isolated_identities(operator, runner=runner)

    staged = stage_candidate(
        plan, trusted_bundle, expected_sha256, host=host,
        identity_provider=lambda: (identities["agent_uid"], identities["upload_gid"]),
        probe_serial=probe_serial,
    )
    candidate = Path(staged["candidate_dir"])

    migration_started = False
    try:
        _user_systemctl(operator, "disable", "--now", SYSTEM_UNIT, runner=runner)
        migration_started = True

        _copy_staged_runtime(candidate)
        service_payload = (candidate / "systemd/b300-stlink-gateway-agent.service").read_bytes()
        mount_payload = (candidate / "systemd/b300-stlink-ingress.mount.rendered").read_bytes()
        _write_exact_file(SYSTEMD_ROOT / SYSTEM_UNIT, service_payload)
        _write_exact_file(SYSTEMD_ROOT / MOUNT_UNIT, mount_payload)

        _safe_mkdir(Path("/var/lib/b300-stlink"), 0o755)
        _safe_mkdir(Path("/var/lib/b300-stlink/gateway"), 0o700,
                    owner_uid=identities["agent_uid"], owner_gid=identities["agent_gid"])
        _safe_mkdir(Path("/var/spool/b300-stlink"), 0o755)
        _safe_mkdir(Path("/var/spool/b300-stlink/ingress"), 0o755)

        if LEGACY_UDEV_OVERRIDE.exists() or LEGACY_UDEV_OVERRIDE.is_symlink():
            raise StageError("LEGACY_UDEV_OVERRIDE_PRESENT")
        _write_exact_file(LEGACY_UDEV_OVERRIDE, LEGACY_UDEV_MASK_TEXT.encode("utf-8"))
        _write_exact_file(AGENT_UDEV_RULE, AGENT_UDEV_RULE_TEXT.encode("utf-8"))

        marker_record = {
            "schema_version": 1,
            "socket_path": "/run/b300-stlink/agent.sock",
            "state_root": "/var/lib/b300-stlink/gateway",
            "ingress_root": "/var/spool/b300-stlink/ingress",
            "operator_uid": identities["operator_uid"],
            "operator_gid": identities["operator_gid"],
            "flash_enabled": False,
        }
        _atomic_marker(marker_record)

        _checked_command(("/usr/bin/systemctl", "daemon-reload"), runner=runner)
        _checked_command(("/usr/bin/udevadm", "control", "--reload-rules"), runner=runner)
        _checked_command(("/usr/bin/udevadm", "trigger", "--subsystem-match=usb", "--action=change"),
                         timeout=20.0, runner=runner)
        _checked_command(("/usr/bin/udevadm", "settle"), timeout=20.0, runner=runner)

        node = Path(plan.host["probe"]["node"])
        if not node.is_char_device():
            raise StageError("PROBE_NODE_INVALID")
        # Remove any stale logind uaccess ACL from the pre-migration rule, then
        # assert the exact current-node ownership expected from the new rule.
        _checked_command(("/usr/bin/setfacl", "-b", str(node)), runner=runner)
        os.chown(node, 0, identities["probe_gid"])
        os.chmod(node, 0o660)
        info = node.stat()
        if info.st_gid != identities["probe_gid"] or stat.S_IMODE(info.st_mode) != 0o660:
            raise StageError("PROBE_PERMISSION_TRANSITION_FAILED")

        _checked_command(("/usr/bin/systemctl", "enable", "--now", MOUNT_UNIT),
                         timeout=30.0, runner=runner)
        _checked_command(("/usr/bin/systemctl", "enable", "--now", SYSTEM_UNIT),
                         timeout=30.0, runner=runner)

        _run_as_operator(operator, ("/usr/bin/test", "!", "-r", str(node)), runner=runner)
        _run_as_operator(operator, ("/usr/bin/test", "!", "-w", str(node)), runner=runner)
        _run_as_operator("b300-agent", ("/usr/bin/test", "-r", str(node)), runner=runner)
        _run_as_operator("b300-agent", ("/usr/bin/test", "-w", str(node)), runner=runner)

        pending_status = _verify_system_agent(operator, runner=runner)
        marker_record = _set_flash_enabled(marker_record, True)

        deadline = time.monotonic() + 5.0
        active_status = pending_status
        while time.monotonic() < deadline:
            checked = _run_as_operator(
                operator, (str(SYSTEM_RUNTIME_BIN), "debug", "gateway-agent-status", "--json"),
                timeout=10.0, runner=runner)
            active_status = _json_from_output(checked.stdout)
            if "remote_application_flash_isolated_v1" in active_status.get("capabilities", []):
                break
            time.sleep(0.25)
        if "remote_application_flash_isolated_v1" not in active_status.get("capabilities", []):
            raise StageError("ISOLATED_FLASH_CAPABILITY_NOT_READY")

        return {
            "schema_version": 1,
            "status": "ok",
            "state": "ACTIVE",
            "version": plan.candidate.get("version"),
            "bundle_sha256": expected_sha256.lower(),
            "candidate_dir": str(candidate),
            "rollback_inventory": str(inventory_path),
            "system_agent_state": active_status.get("state"),
            "isolated_flash_capability": True,
            "flash_enabled": marker_record["flash_enabled"],
        }
    except Exception:
        if migration_started:
            _rollback_isolated_install(operator, runner=runner)
        raise

def main(argv=None, *, host=None, trust_root: Path = Path("/"),
         trusted_uid: int = 0, path_stat: Callable = os.lstat, output=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    selected = subcommands.add_parser("plan", help="read-only Gateway migration preflight")
    selected.add_argument("--bundle", required=True, type=Path)
    selected.add_argument("--expected-sha256", required=True)
    selected.add_argument("--probe-serial")
    selected.add_argument("--json", action="store_true")

    apply_parser = subcommands.add_parser(
        "apply", help="root-only guarded migration to the isolated system Gateway")
    apply_parser.add_argument("--bundle", required=True, type=Path)
    apply_parser.add_argument("--expected-sha256", required=True)
    apply_parser.add_argument("--operator", default="aubot")
    apply_parser.add_argument("--probe-serial")
    apply_parser.add_argument("--confirm-system-change", action="store_true")
    apply_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    stream = output or sys.stdout
    if args.action == "plan":
        plan = build_plan(args.bundle, args.expected_sha256, host=host,
                          trust_root=trust_root, trusted_uid=trusted_uid,
                          path_stat=path_stat, probe_serial=args.probe_serial)
        print(json.dumps(plan.to_record(), sort_keys=True, separators=(",", ":")), file=stream)
        return 0 if plan.ready else 1
    try:
        result = apply_isolated_gateway(
            args.bundle, args.expected_sha256, operator=args.operator,
            probe_serial=args.probe_serial, confirmed=args.confirm_system_change)
    except (StageError, TransitionError, OSError, KeyError, ValueError) as error:
        record = {
            "schema_version": 1, "status": "error",
            "reason_code": getattr(error, "reason_code", type(error).__name__),
            "message": str(error),
        }
        print(json.dumps(record, sort_keys=True, separators=(",", ":")), file=stream)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
