"""Private, durable Gateway upload and Application programming transactions."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .gateway_supervisor import gateway_runtime_root
from .gateway_system_mode import (
    SYSTEM_INGRESS_ROOT, ingress_mount_isolated, load_isolated_gateway_config,
)
from .models import ProbeRef
from .remote_programming import (
    GatewayProgrammingService, RemoteFirmwareManifest,
    RemoteProgrammingOperation,
)


MAX_ACTIVE_JOBS = 8
MAX_RETAINED_JOBS = 64
MAX_STAGING_BYTES = 64 * 1024 * 1024
ABANDONED_TTL_SECONDS = 3600.0
RESULT_TTL_SECONDS = 24 * 3600.0
MAX_JOB_LOG_BYTES = 1024 * 1024
APPROVAL_TTL_SECONDS = 120.0


class ProgramJobError(RuntimeError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code


def _manifest_record(manifest: RemoteFirmwareManifest) -> dict:
    item = manifest.validate()
    return {key: value.value if hasattr(value, "value") else value
            for key, value in asdict(item).items()}


class GatewayProgramJobs:
    """Only the Agent may prepare or commit jobs; SFTP writes upload bytes."""

    def __init__(self, root: Optional[Path] = None, coordinator=None, *,
                 programming: Optional[GatewayProgrammingService] = None,
                 ingress_root: Optional[Path] = None) -> None:
        root_path = Path(root) if root is not None else gateway_runtime_root() / "program-jobs"
        self.root = Path(os.path.abspath(str(root_path.expanduser())))
        self.ingress_root = (Path(os.path.abspath(str(Path(ingress_root).expanduser())))
                             if ingress_root is not None else None)
        self.coordinator = coordinator
        self.programming = programming or GatewayProgrammingService()
        self._lock = threading.RLock()
        self._approvals = {}
        self._worker = None
        self._active_job_id = None
        self._prepare_root()
        if self.ingress_root is not None:
            if os.name != "nt":
                private = self.root.lstat()
                parent = self.root.parent.lstat()
                if (private.st_uid != os.getuid() or parent.st_uid != os.getuid()
                        or not stat.S_ISDIR(parent.st_mode)
                        or parent.st_mode & 0o077):
                    raise ProgramJobError("STAGING_UNSAFE", "Private state root is unsafe.")
            self._prepare_ingress()

    def _prepare_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ProgramJobError("STAGING_UNSAFE", "Job root cannot be a symlink.")
        if os.name != "nt":
            os.chmod(self.root, 0o700)

    def _require_isolated_programming(self) -> None:
        if self.ingress_root != SYSTEM_INGRESS_ROOT:
            return
        config = load_isolated_gateway_config()
        if (config is None or not config.flash_enabled
                or config.ingress_root != self.ingress_root):
            raise ProgramJobError("ISOLATED_FLASH_DISABLED",
                                  "Isolated Application flash is pending boundary verification.")
        if not ingress_mount_isolated(
                self.ingress_root, uid=getattr(os, "getuid", lambda: -1)(),
                gid=getattr(self, "_ingress_gid", -1)):
            raise ProgramJobError("INGRESS_MOUNT_UNAVAILABLE",
                                  "System ingress mount is unavailable or unsafe.")

    def _prepare_ingress(self) -> None:
        root = self.ingress_root
        try:
            info = root.lstat()
            if root == SYSTEM_INGRESS_ROOT and not ingress_mount_isolated(
                    root, uid=getattr(os, "getuid", lambda: -1)(), gid=info.st_gid):
                raise ProgramJobError("INGRESS_MOUNT_UNAVAILABLE",
                                      "System ingress mount is unavailable or unsafe.")
            parent = root.parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or not stat.S_ISDIR(parent.st_mode)
                    or root.is_symlink() or root.parent.is_symlink()):
                raise ProgramJobError("STAGING_UNSAFE")
            if os.name != "nt":
                if (info.st_uid != os.getuid() or info.st_mode & 0o022
                        or not info.st_mode & 0o010
                        or parent.st_mode & 0o022):
                    raise ProgramJobError("STAGING_UNSAFE")
                self._ingress_gid = info.st_gid
            jobs = root / "program-jobs"
            jobs.mkdir(mode=0o710, exist_ok=True)
            if jobs.is_symlink() or not jobs.is_dir():
                raise ProgramJobError("STAGING_UNSAFE")
            if os.name != "nt":
                os.chmod(jobs, 0o710)
                if jobs.stat().st_gid != self._ingress_gid:
                    os.chown(jobs, -1, self._ingress_gid)
                if (jobs.stat().st_uid != os.getuid()
                        or jobs.stat().st_gid != self._ingress_gid
                        or stat.S_IMODE(jobs.stat().st_mode) != 0o710):
                    raise ProgramJobError("STAGING_UNSAFE")
            self._ingress_jobs = jobs
        except OSError as error:
            raise ProgramJobError("STAGING_UNSAFE", "Ingress root is unsafe.") from error

    def _upload_path(self, job_id: str) -> Path:
        if self.ingress_root is None:
            return self._job_dir(job_id) / "artifact.part"
        return self._ingress_jobs / self._job_id(job_id) / "artifact.part"

    def _create_ingress_slot(self, job_id: str) -> Path:
        directory = self._ingress_jobs / self._job_id(job_id)
        directory.mkdir(mode=0o710)
        path = directory / "artifact.part"
        created_file = None
        try:
            if os.name != "nt":
                os.chmod(directory, 0o710)
                if directory.stat().st_gid != self._ingress_gid:
                    os.chown(directory, -1, self._ingress_gid)
                if (directory.stat().st_uid != os.getuid()
                        or directory.stat().st_gid != self._ingress_gid
                        or stat.S_IMODE(directory.stat().st_mode) != 0o710):
                    raise ProgramJobError("STAGING_UNSAFE")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(str(path), flags, 0o660)
            try:
                created_file = os.fstat(fd)
                if os.name != "nt":
                    os.fchmod(fd, 0o660)
                    if os.fstat(fd).st_gid != self._ingress_gid:
                        os.fchown(fd, -1, self._ingress_gid)
                    info = os.fstat(fd)
                    if (info.st_uid != os.getuid() or info.st_gid != self._ingress_gid
                            or stat.S_IMODE(info.st_mode) != 0o660):
                        raise ProgramJobError("STAGING_UNSAFE")
            finally:
                os.close(fd)
            return path
        except Exception:
            if created_file is not None:
                try:
                    current = path.lstat()
                    if ((current.st_dev, current.st_ino)
                            == (created_file.st_dev, created_file.st_ino)):
                        path.unlink()
                except FileNotFoundError:
                    pass
            directory.rmdir()
            raise

    def _remove_unrecorded_job(self, job_id: str) -> None:
        directory = self._job_dir(job_id)
        record = directory / "job.json"
        if record.exists() or record.is_symlink():
            info = record.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ProgramJobError("STAGING_UNSAFE")
            if os.name != "nt" and info.st_uid != os.getuid():
                raise ProgramJobError("STAGING_UNSAFE")
            record.unlink()
        directory.rmdir()

    def _remove_ingress_slot(self, job_id: str) -> None:
        if self.ingress_root is None:
            return
        directory = self._ingress_jobs / self._job_id(job_id)
        if not directory.exists() and not directory.is_symlink():
            return
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ProgramJobError("STAGING_UNSAFE")
        if os.name != "nt" and (info.st_uid != os.getuid()
                                or info.st_gid != self._ingress_gid
                                or stat.S_IMODE(info.st_mode) != 0o710):
            raise ProgramJobError("STAGING_UNSAFE")
        artifact = directory / "artifact.part"
        if artifact.exists() or artifact.is_symlink():
            file_info = artifact.lstat()
            if not stat.S_ISREG(file_info.st_mode) or file_info.st_nlink != 1:
                raise ProgramJobError("STAGING_UNSAFE")
            artifact.unlink()
        directory.rmdir()

    @staticmethod
    def _job_id(value: str) -> str:
        if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
            raise ProgramJobError("JOB_INVALID")
        return value

    def _job_dir(self, job_id: str) -> Path:
        path = self.root / self._job_id(job_id)
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ProgramJobError("STAGING_UNSAFE")
        return path

    def _record_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _read(self, job_id: str) -> dict:
        try:
            path = self._record_path(job_id)
            if path.is_symlink():
                raise ProgramJobError("STAGING_UNSAFE")
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("job_id") != job_id:
                raise ValueError("job record mismatch")
            return record
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ProgramJobError("JOB_INVALID", "Job record is absent or corrupt.") from error

    def _write(self, job_id: str, record: dict) -> None:
        directory = self._job_dir(job_id)
        fd, name = tempfile.mkstemp(prefix="job-", suffix=".tmp", dir=str(directory))
        temporary = Path(name)
        try:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._record_path(job_id))
        finally:
            temporary.unlink(missing_ok=True)

    def create_upload(self, manifest: RemoteFirmwareManifest, client_id: str,
                      probe_serial: Optional[str], *,
                      request_id: Optional[str] = None) -> dict:
        item = manifest.validate()
        if (item.operation != RemoteProgrammingOperation.FLASH_APPLICATION
                or Path(item.file_name).suffix.lower() != ".hex"):
            raise ProgramJobError("REMOTE_FLASH_UNSUPPORTED")
        if not isinstance(client_id, str) or not client_id or len(client_id) > 64:
            raise ProgramJobError("CLIENT_INVALID")
        if request_id is not None and (
                not isinstance(request_id, str) or not request_id
                or len(request_id) > 64):
            raise ProgramJobError("REQUEST_INVALID")
        with self._lock:
            self._require_isolated_programming()
            self._prune_expired()
            records = []
            for entry in self.root.iterdir():
                if entry.is_dir() and not entry.is_symlink():
                    records.append(self._read(entry.name))
            if request_id is not None:
                for previous in records:
                    if previous.get("request_id") != request_id:
                        continue
                    if (previous.get("manifest") != _manifest_record(item)
                            or previous.get("client_id") != client_id
                            or previous.get("probe_serial") != probe_serial):
                        raise ProgramJobError("REQUEST_REPLAYED")
                    return {
                        "job_id": previous["job_id"],
                        "upload_path": str(self._upload_path(previous["job_id"])),
                        "state": previous["state"],
                    }
            active = sum(record["state"] in {
                "UPLOADING", "STAGED", "AWAITING_CONFIRMATION", "RUNNING",
            } for record in records)
            reserved_bytes = sum(
                int(record["manifest"]["size"]) for record in records
                if not record.get("artifact_cleaned", False)
            )
            if (active >= MAX_ACTIVE_JOBS or len(records) >= MAX_RETAINED_JOBS
                    or reserved_bytes + item.size > MAX_STAGING_BYTES):
                raise ProgramJobError("STAGING_QUOTA_EXCEEDED")
            job_id = uuid.uuid4().hex
            directory = self._job_dir(job_id)
            directory.mkdir(mode=0o700)
            ingress_created = False
            try:
                upload = (self._create_ingress_slot(job_id) if self.ingress_root is not None
                          else self._upload_path(job_id))
                ingress_created = self.ingress_root is not None
                record = {
                    "job_id": job_id, "state": "UPLOADING",
                    "manifest": _manifest_record(item), "client_id": client_id,
                    "request_id": request_id,
                    "probe_serial": probe_serial, "created_at": time.time(),
                    "phase": "uploading", "progress": 0,
                    "reason_code": "", "reason": "", "next_action": "",
                }
                self._write(job_id, record)
                return {"job_id": job_id, "upload_path": str(upload),
                        "state": "UPLOADING"}
            except Exception:
                try:
                    if ingress_created:
                        self._remove_ingress_slot(job_id)
                finally:
                    self._remove_unrecorded_job(job_id)
                raise

    def _prune_expired(self) -> None:
        now = time.time()
        for entry in self.root.iterdir():
            if not entry.is_dir() or entry.is_symlink():
                continue
            try:
                record = self._read(entry.name)
                age = now - float(record["created_at"])
                if entry.name == self._active_job_id:
                    continue
                ttl = (ABANDONED_TTL_SECONDS if record["state"] in {
                    "UPLOADING", "STAGED", "AWAITING_CONFIRMATION",
                } else RESULT_TTL_SECONDS)
                if age < ttl or record["state"] == "RUNNING":
                    continue
                self._remove_ingress_slot(entry.name)
                for child in entry.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        raise ProgramJobError("STAGING_UNSAFE")
                    child.unlink()
                entry.rmdir()
            except (OSError, ValueError, ProgramJobError):
                # Uncertain or corrupt job evidence is retained, not erased.
                continue

    def staged_path(self, job_id: str) -> Path:
        record = self._read(job_id)
        manifest = RemoteFirmwareManifest(**record["manifest"]).validate()
        return self._job_dir(job_id) / manifest.file_name

    def _verified_staged_path(self, job_id: str) -> Path:
        if self.root.is_symlink():
            raise ProgramJobError("STAGING_UNSAFE", "Job root was replaced.")
        directory = self._job_dir(job_id)
        path = self.staged_path(job_id)
        try:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or directory.resolve(strict=True).parent != self.root.resolve(strict=True)
                    or path.resolve(strict=True).parent != directory.resolve(strict=True)):
                raise ProgramJobError("STAGING_UNSAFE", "Staged firmware is not one private regular file.")
        except OSError as error:
            raise ProgramJobError("STAGING_UNSAFE", "Staged firmware is missing or unsafe.") from error
        return path

    def finalize_upload(self, job_id: str) -> dict:
        with self._lock:
            self._require_isolated_programming()
            record = self._read(job_id)
            if record["state"] in {"STAGED", "AWAITING_CONFIRMATION", "RUNNING"}:
                manifest = RemoteFirmwareManifest(**record["manifest"]).validate()
                staged = self._verified_staged_path(job_id)
                if not manifest.matches_file(staged):
                    raise ProgramJobError("ARTIFACT_CHANGED")
                return self.status(job_id)
            if record["state"] != "UPLOADING":
                raise ProgramJobError("JOB_STATE_INVALID")
            if self.ingress_root is not None:
                try:
                    return self._finalize_isolated(job_id, record)
                except ProgramJobError as error:
                    record.update(reason_code=error.reason_code,
                                  reason=str(error)[:256],
                                  next_action="Correct the upload and create a new transaction if needed.")
                    self._write(job_id, record)
                    raise
            directory = self._job_dir(job_id)
            source = directory / "artifact.part"
            try:
                info = source.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ProgramJobError("STAGING_UNSAFE")
                manifest = RemoteFirmwareManifest(**record["manifest"]).validate()
                if info.st_size != manifest.size:
                    raise ProgramJobError("UPLOAD_HASH_MISMATCH")
                with source.open("rb") as handle:
                    hasher = hashlib.sha256()
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(chunk)
                    digest = hasher.hexdigest()
                if digest != manifest.sha256:
                    raise ProgramJobError("UPLOAD_HASH_MISMATCH")
                target = directory / manifest.file_name
                if target.exists() or target.is_symlink():
                    raise ProgramJobError("STAGING_UNSAFE")
                os.replace(source, target)
                os.chmod(target, 0o600)
                record.update(state="STAGED", phase="staged", progress=100)
                self._write(job_id, record)
                return self.status(job_id)
            except FileNotFoundError as error:
                raise ProgramJobError("UPLOAD_INCOMPLETE") from error

    def _finalize_isolated(self, job_id: str, record: dict) -> dict:
        manifest = RemoteFirmwareManifest(**record["manifest"]).validate()
        source = self._upload_path(job_id)
        private_dir = self._job_dir(job_id)
        target = private_dir / manifest.file_name
        temporary = None
        descriptor = -1
        try:
            self._prepare_ingress()
            parent = source.parent.lstat()
            if (not stat.S_ISDIR(parent.st_mode) or source.parent.is_symlink()
                    or source.parent.resolve(strict=True).parent != self._ingress_jobs.resolve(strict=True)):
                raise ProgramJobError("STAGING_UNSAFE")
            if os.name != "nt" and (parent.st_uid != os.getuid()
                                    or parent.st_gid != self._ingress_gid
                                    or stat.S_IMODE(parent.st_mode) != 0o710):
                raise ProgramJobError("STAGING_UNSAFE")
            before = source.lstat()
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size > 32 * 1024 * 1024):
                raise ProgramJobError("STAGING_UNSAFE")
            if os.name != "nt" and (before.st_uid != os.getuid()
                                    or before.st_gid != self._ingress_gid
                                    or stat.S_IMODE(before.st_mode) != 0o660):
                raise ProgramJobError("STAGING_UNSAFE")
            descriptor = os.open(str(source), os.O_RDONLY | getattr(os, "O_BINARY", 0)
                                 | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_CLOEXEC", 0))
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or opened.st_size != manifest.size):
                raise ProgramJobError("STAGING_UNSAFE")
            private_fd, private_name = tempfile.mkstemp(prefix="artifact-", suffix=".tmp",
                                                        dir=str(private_dir))
            temporary = Path(private_name)
            digest = hashlib.sha256()
            copied = 0
            with os.fdopen(descriptor, "rb") as ingress:
                descriptor = -1
                with os.fdopen(private_fd, "wb") as staged:
                    for chunk in iter(lambda: ingress.read(1024 * 1024), b""):
                        copied += len(chunk)
                        if copied > 32 * 1024 * 1024:
                            raise ProgramJobError("UPLOAD_TOO_LARGE")
                        staged.write(chunk)
                        digest.update(chunk)
                    staged.flush()
                    os.fsync(staged.fileno())
                    after = os.fstat(ingress.fileno())
            current = source.lstat()
            identity = lambda item: (item.st_dev, item.st_ino, item.st_size,
                                     item.st_mtime_ns, item.st_ctime_ns)
            if identity(opened) != identity(after) or identity(after) != identity(current):
                raise ProgramJobError("STAGING_UNSAFE", "Ingress changed during copy.")
            if copied != manifest.size or digest.hexdigest() != manifest.sha256:
                raise ProgramJobError("UPLOAD_HASH_MISMATCH")
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            # Linking a completed private temporary is atomic and fails if a prior
            # finalized artifact already exists; neither retry nor replay replaces it.
            os.link(temporary, target)
            temporary.unlink()
            temporary = None
            record.update(state="STAGED", phase="staged", progress=100,
                          reason_code="", reason="", next_action="")
            self._write(job_id, record)
            return self.status(job_id)
        except FileExistsError as error:
            raise ProgramJobError("STAGING_UNSAFE", "Private artifact already exists.") from error
        except FileNotFoundError as error:
            raise ProgramJobError("UPLOAD_INCOMPLETE") from error
        except OSError as error:
            raise ProgramJobError("STAGING_UNSAFE", "Ingress or private staging failed safely.") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _require_lease(self, record: dict, lease_id: str, token: str, generation: int) -> None:
        if self.coordinator is None or not self.coordinator.owns_flash_lease(
                lease_id, token, generation):
            raise ProgramJobError("LEASE_INVALID")
        if hasattr(self.coordinator, "public_snapshot"):
            selected = self.coordinator.public_snapshot().probe_serial
            if record["probe_serial"] not in (None, selected):
                raise ProgramJobError("PROBE_SELECTION_REQUIRED")

    def prepare(self, job_id: str, lease_id: str, token: str, generation: int) -> dict:
        with self._lock:
            self._require_isolated_programming()
            record = self._read(job_id)
            if record["state"] == "AWAITING_CONFIRMATION":
                self._require_lease(record, lease_id, token, generation)
                existing = self._approvals.get(job_id)
                if (existing is None or time.monotonic() >= existing[2]
                        or existing[3] != (lease_id, generation)):
                    raise ProgramJobError("APPROVAL_EXPIRED")
                staged = self._verified_staged_path(job_id)
                self.programming._verify_received_file(existing[0].manifest, staged)
                result = self.status(job_id)
                result["approval_token"] = existing[4]
                return result
            if record["state"] != "STAGED":
                raise ProgramJobError("JOB_STATE_INVALID")
            self._require_lease(record, lease_id, token, generation)
            manifest = RemoteFirmwareManifest(**record["manifest"]).validate()
            selected_serial = self.coordinator.public_snapshot().probe_serial
            if record["probe_serial"] is not None and record["probe_serial"] != selected_serial:
                raise ProgramJobError("PROBE_SELECTION_REQUIRED")
            probe = ProbeRef(selected_serial)
            staged = self._verified_staged_path(job_id)
            approval = self.programming.prepare_application(
                manifest, staged, probe,
            )
            plan = approval.plan
            if tuple(plan.erase_sectors) != (3, 4, 5, 6, 7):
                raise ProgramJobError("FLASH_PLAN_INVALID")
            approval_token = secrets.token_urlsafe(32)
            self._approvals[job_id] = (
                approval, hashlib.sha256(approval_token.encode()).hexdigest(),
                time.monotonic() + APPROVAL_TTL_SECONDS,
                (lease_id, generation),
                approval_token,
            )
            record.update(
                state="AWAITING_CONFIRMATION", phase="prepared", progress=100,
                plan={
                    "erase_sectors": list(plan.erase_sectors),
                    "image_sha256": plan.image.sha256,
                    "start_address": plan.image.start_address,
                    "end_address": plan.image.end_address,
                    "flash_crc32": plan.image.flash_crc32,
                    "probe_serial": plan.probe.serial,
                    "device_id": plan.target.device_id,
                    "flash_kib": plan.target.flash_kib,
                    "target_voltage": plan.target.target_voltage,
                    "protection_reported": plan.target.protection_reported,
                    "readout_protected": plan.target.readout_protected,
                    "protected_sectors": list(plan.target.protected_sectors),
                    "metadata_address": "0x0800C000",
                    "metadata_bytes": 44,
                    "transaction": [
                        "flash erase_sector 0 3 7",
                        "flash write_image {application.hex}",
                        "verify_image {application.hex}",
                        "metadata_plan: 0x0800C000 / 44 bytes / STLM + VERIFIED",
                        "reset run",
                    ],
                },
            )
            self._write(job_id, record)
            result = self.status(job_id)
            result["approval_token"] = approval_token
            return result

    def commit(self, job_id: str, approval_token: str, lease_id: str,
               token: str, generation: int) -> dict:
        with self._lock:
            self._require_isolated_programming()
            record = self._read(job_id)
            supplied_digest = hashlib.sha256(str(approval_token).encode()).hexdigest()
            if record["state"] in {"RUNNING", "SUCCEEDED", "FAILED"}:
                if not secrets.compare_digest(
                        str(record.get("approval_digest", "")), supplied_digest):
                    raise ProgramJobError("APPROVAL_MISMATCH")
                return self.status(job_id)
            if record["state"] != "AWAITING_CONFIRMATION":
                raise ProgramJobError("JOB_STATE_INVALID")
            self._require_lease(record, lease_id, token, generation)
            selected = self._approvals.get(job_id)
            if (selected is None or time.monotonic() >= selected[2]
                    or selected[3] != (lease_id, generation)
                    or not secrets.compare_digest(
                        selected[1], supplied_digest
                    )):
                raise ProgramJobError("APPROVAL_MISMATCH")
            approval = selected[0]
            try:
                staged = self._verified_staged_path(job_id)
                if approval.staged_path != staged:
                    raise ProgramJobError("STAGING_UNSAFE", "Approved staged path changed.")
                self.programming._verify_received_file(approval.manifest, staged)
            except ProgramJobError:
                raise
            except Exception as error:
                raise ProgramJobError("ARTIFACT_CHANGED", str(error)) from error
            if self._worker is not None and self._worker.is_alive():
                raise ProgramJobError("GATEWAY_BUSY")
            self._create_private_log(job_id)
            record.update(
                state="RUNNING", phase="validating", progress=0,
                approval_digest=selected[1],
            )
            self._write(job_id, record)
            if hasattr(self.coordinator, "adopt_flash_job"):
                self.coordinator.adopt_flash_job(lease_id, token, generation)
            self._active_job_id = job_id
            self._worker = threading.Thread(
                target=self._run_job,
                args=(job_id, approval, lease_id, token, generation),
                name="b300-application-program", daemon=False,
            )
            self._worker.start()
            return self.status(job_id)

    def _create_private_log(self, job_id: str) -> None:
        path = self._job_dir(job_id) / "flash.log"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(path), flags, 0o600)
        except OSError as error:
            raise ProgramJobError("STAGING_UNSAFE", "Flash log path is already occupied or unsafe.") from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ProgramJobError("STAGING_UNSAFE", "Flash log is not one private regular file.")
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            else:
                os.chmod(path, 0o600)
        finally:
            os.close(fd)

    def _append_private_log(self, job_id: str, line: str) -> None:
        path = self._job_dir(job_id) / "flash.log"
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ProgramJobError("STAGING_UNSAFE", "Flash log was replaced.")
            if before.st_size >= MAX_JOB_LOG_BYTES:
                return
            flags = os.O_WRONLY | os.O_APPEND
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(str(path), flags)
            try:
                opened = os.fstat(fd)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                    raise ProgramJobError("STAGING_UNSAFE", "Flash log identity changed.")
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8", errors="replace") as handle:
                    fd = -1
                    handle.write(str(line).replace("\x00", "")[:2048] + "\n")
            finally:
                if fd >= 0:
                    os.close(fd)
        except (OSError, ProgramJobError):
            # Logging cannot be allowed to redirect a destructive transaction.
            # The canonical flash result and post-verification remain decisive.
            return

    def cancel(self, job_id: str, lease_id: str, token: str, generation: int) -> dict:
        with self._lock:
            record = self._read(job_id)
            if record["state"] not in {"UPLOADING", "STAGED", "AWAITING_CONFIRMATION"}:
                raise ProgramJobError("JOB_STATE_INVALID")
            self._require_lease(record, lease_id, token, generation)
            self._approvals.pop(job_id, None)
            record.update(state="CANCELLED", phase="cancelled", progress=0)
            self._write(job_id, record)
            return self.status(job_id)

    def cleanup(self, job_id: str) -> dict:
        with self._lock:
            record = self._read(job_id)
            if record["state"] not in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
                raise ProgramJobError("JOB_STATE_INVALID")
            artifact = self.staged_path(job_id)
            if artifact.exists() or artifact.is_symlink():
                info = artifact.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ProgramJobError("STAGING_UNSAFE")
                artifact.unlink()
            partial = self._upload_path(job_id)
            if partial.exists() or partial.is_symlink():
                info = partial.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ProgramJobError("STAGING_UNSAFE")
                if self.ingress_root is None:
                    partial.unlink()
            self._remove_ingress_slot(job_id)
            record["artifact_cleaned"] = True
            self._write(job_id, record)
            return self.status(job_id)

    def _run_job(self, job_id, approval, lease_id, token, generation) -> None:
        stop_heartbeat = threading.Event()

        def renew() -> None:
            while not stop_heartbeat.wait(5.0):
                try:
                    self.coordinator.renew(lease_id, token, generation)
                except Exception:
                    pass

        heartbeat = threading.Thread(target=renew, name="b300-flash-lease-heartbeat", daemon=True)
        heartbeat.start()
        try:
            def event_sink(line: str) -> None:
                self._append_private_log(job_id, line)

            def phase_sink(event) -> None:
                with self._lock:
                    record = self._read(job_id)
                    record.update(phase=str(event.phase), progress=int(event.progress))
                    self._write(job_id, record)

            staged = self._verified_staged_path(job_id)
            if approval.staged_path != staged:
                raise ProgramJobError("STAGING_UNSAFE", "Approved staged path changed.")
            self._require_isolated_programming()
            result = self.programming.flash_application(
                approval, event_sink=event_sink, phase_sink=phase_sink,
            )
            verification = result.boot_verification
            metadata = result.confirmed_metadata
            verified = (
                result.succeeded
                and result.flash_command is not None
                and "** Verified OK **" in result.flash_command.output.splitlines()
                and result.reset_command is not None
                and result.reset_command.returncode == 0
                and result.verified_metadata_bytes is not None
                and len(result.verified_metadata_bytes) == 44
                and metadata is not None and metadata.valid
                and metadata.state_name == "CONFIRMED"
                and verification is not None and verification.passed
                and verification.pc is not None
                and 0x08010000 <= verification.pc < 0x08080000
                and verification.bkp1r == 0
            )
            with self._lock:
                record = self._read(job_id)
                record.update(
                    state="SUCCEEDED" if verified else "FAILED",
                    phase="complete" if verified else (result.failure_phase or "post_verifying"),
                    progress=100 if verified else record.get("progress", 0),
                    reason_code="" if verified else "FLASH_FAILED",
                    reason="" if verified else (result.reason or "Post-flash verification failed."),
                    next_action="" if verified else (result.next_action or "Inspect Gateway flash log and target state."),
                    pc=verification.pc if verification else None,
                    bkp1r=verification.bkp1r if verification else None,
                    metadata_state=metadata.state_name if metadata else None,
                    metadata_sequence=metadata.sequence if metadata else None,
                )
                self._write(job_id, record)
        except Exception as error:
            with self._lock:
                record = self._read(job_id)
                record.update(
                    state="FAILED", phase=getattr(error, "phase", "programming"),
                    reason_code=getattr(error, "reason_code", "FLASH_FAILED"),
                    reason=str(error)[:512],
                    next_action="Inspect Gateway log and board before starting a new transaction.",
                )
                self._write(job_id, record)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=1)
            if hasattr(self.coordinator, "finish_flash_job"):
                self.coordinator.finish_flash_job(lease_id, token, generation)
            else:
                self.coordinator.release(lease_id, token, generation)
            with self._lock:
                self._approvals.pop(job_id, None)
                self._active_job_id = None

    def status(self, job_id: str) -> dict:
        with self._lock:
            record = self._read(job_id)
            if (record["state"] == "RUNNING" and self._active_job_id != job_id
                    or record["state"] == "AWAITING_CONFIRMATION"
                    and job_id not in self._approvals):
                record.update(
                    state="RECOVERY_REQUIRED", phase="recovery",
                    reason_code="JOB_RECOVERY_REQUIRED",
                    reason="Gateway Agent restarted before this job reached a recorded terminal state.",
                    next_action=(
                        "Inspect the board and Gateway log. If hardware ownership remains stale, "
                        "run local hardware recover only after OpenOCD is stopped. "
                        "Do not retry flash automatically."
                    ),
                )
                self._write(job_id, record)
            public = (
                "job_id", "state", "manifest", "probe_serial", "plan", "phase",
                "progress", "reason_code", "reason", "next_action", "pc",
                "bkp1r", "metadata_state", "metadata_sequence",
            )
            return {key: record[key] for key in public if key in record}

    def wait_active(self, timeout: Optional[float] = None) -> None:
        worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout)
            if worker.is_alive():
                raise TimeoutError("Gateway Application programming job is still running.")
