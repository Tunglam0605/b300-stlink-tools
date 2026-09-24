#!/usr/bin/env python3
"""Read-only preflight for a separately authorized isolated Ubuntu Gateway install.

This module deliberately has no apply, verify-boundary, or rollback operation.
The plan is evidence for a later transaction, never authorization to mutate a host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
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
MAX_JOB_RECORD_BYTES = 65536
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


def _inspect_bundle(bundle: Path) -> tuple[str, dict]:
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
        with tarfile.open(fileobj=handle, mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("Bundle has too many members")
            names = set()
            for member in members:
                _relative_name(member.name)
                if not member.isfile() or member.name in names:
                    raise ValueError("Bundle contains duplicate or non-regular members")
                names.add(member.name)
            if not EXPECTED_ARCHIVE_MEMBERS.issubset(names):
                raise ValueError("Bundle lacks required isolated Gateway files")
            metadata_member = archive.getmember("BUNDLE-METADATA.txt")
            if metadata_member.size > 4096:
                raise ValueError("Bundle metadata exceeds limit")
            metadata_file = archive.extractfile(metadata_member)
            if metadata_file is None:
                raise ValueError("Bundle metadata is unreadable")
            raw = metadata_file.read(4097).decode("ascii")
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
        return digest.hexdigest(), metadata


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
                    ("count", "selected", "node", "uid", "gid", "mode",
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
        try:
            devices = tuple(sysfs.iterdir())
        except OSError:
            devices = ()
        for directory in devices:
            try:
                vendor = (directory / "idVendor").read_text(encoding="ascii").strip().lower()
                product = (directory / "idProduct").read_text(encoding="ascii").strip().lower()
                if (vendor, product) != ("0483", "3748"):
                    continue
                serial = (directory / "serial").read_text(encoding="ascii").strip()
                bus = int((directory / "busnum").read_text(encoding="ascii"))
                number = int((directory / "devnum").read_text(encoding="ascii"))
                node = "/dev/bus/usb/%03d/%03d" % (bus, number)
                info = self._mapped(node).lstat()
                matches.append((serial, node, info))
            except (OSError, ValueError, UnicodeError):
                continue
        selected = ([item for item in matches if item[0] == probe_serial]
                    if probe_serial is not None else matches if len(matches) == 1 else [])
        result = {"count": len(matches), "selected": len(selected) == 1,
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
