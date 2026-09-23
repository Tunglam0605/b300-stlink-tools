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
                 programming: Optional[GatewayProgrammingService] = None) -> None:
        self.root = Path(root) if root is not None else gateway_runtime_root() / "program-jobs"
        self.coordinator = coordinator
        self.programming = programming or GatewayProgrammingService()
        self._lock = threading.RLock()
        self._approvals = {}
        self._worker = None
        self._active_job_id = None
        self._prepare_root()

    def _prepare_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ProgramJobError("STAGING_UNSAFE", "Job root cannot be a symlink.")
        if os.name != "nt":
            os.chmod(self.root, 0o700)

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
                        "upload_path": str(self._job_dir(previous["job_id"]) / "artifact.part"),
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
            record = {
                "job_id": job_id, "state": "UPLOADING",
                "manifest": _manifest_record(item), "client_id": client_id,
                "request_id": request_id,
                "probe_serial": probe_serial, "created_at": time.time(),
                "phase": "uploading", "progress": 0,
                "reason_code": "", "reason": "", "next_action": "",
            }
            self._write(job_id, record)
            return {"job_id": job_id, "upload_path": str(directory / "artifact.part"),
                    "state": "UPLOADING"}

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

    def finalize_upload(self, job_id: str) -> dict:
        with self._lock:
            record = self._read(job_id)
            if record["state"] in {"STAGED", "AWAITING_CONFIRMATION", "RUNNING"}:
                manifest = RemoteFirmwareManifest(**record["manifest"]).validate()
                if not manifest.matches_file(self.staged_path(job_id)):
                    raise ProgramJobError("ARTIFACT_CHANGED")
                return self.status(job_id)
            if record["state"] != "UPLOADING":
                raise ProgramJobError("JOB_STATE_INVALID")
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
                record.update(state="STAGED", phase="staged", progress=100)
                self._write(job_id, record)
                return self.status(job_id)
            except FileNotFoundError as error:
                raise ProgramJobError("UPLOAD_INCOMPLETE") from error

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
            record = self._read(job_id)
            if record["state"] == "AWAITING_CONFIRMATION":
                self._require_lease(record, lease_id, token, generation)
                existing = self._approvals.get(job_id)
                if (existing is None or time.monotonic() >= existing[2]
                        or existing[3] != (lease_id, generation)):
                    raise ProgramJobError("APPROVAL_EXPIRED")
                self.programming._verify_received_file(existing[0].manifest, existing[0].staged_path)
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
            approval = self.programming.prepare_application(
                manifest, self.staged_path(job_id), probe,
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
                self.programming._verify_received_file(approval.manifest, approval.staged_path)
            except Exception as error:
                raise ProgramJobError("ARTIFACT_CHANGED", str(error)) from error
            if self._worker is not None and self._worker.is_alive():
                raise ProgramJobError("GATEWAY_BUSY")
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
            partial = self._job_dir(job_id) / "artifact.part"
            if partial.exists() or partial.is_symlink():
                info = partial.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ProgramJobError("STAGING_UNSAFE")
                partial.unlink()
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
                path = self._job_dir(job_id) / "flash.log"
                if path.exists() and path.stat().st_size >= MAX_JOB_LOG_BYTES:
                    return
                with path.open("a", encoding="utf-8", errors="replace") as handle:
                    handle.write(str(line).replace("\x00", "")[:2048] + "\n")

            def phase_sink(event) -> None:
                with self._lock:
                    record = self._read(job_id)
                    record.update(phase=str(event.phase), progress=int(event.progress))
                    self._write(job_id, record)

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
