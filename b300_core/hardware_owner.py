"""OS-held cross-process lock for one B300 Gateway host's ST-Link hardware."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from .subprocess_env import external_command_env


class HardwareOwnerBusy(RuntimeError):
    pass


def openocd_quiescent() -> bool:
    """Return true only after a bounded host-wide OpenOCD process check."""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                text=True, timeout=3, env=external_command_env(),
            )
            if result.returncode:
                return False
            names = (row[0].lower() for row in csv.reader(io.StringIO(result.stdout)) if row)
            return all("openocd" not in name for name in names)
        root = Path("/proc")
        if not root.is_dir():
            return False
        for entry in root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                name = (entry / "comm").read_text(encoding="ascii").strip().lower()
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError):
                return False
            if "openocd" in name:
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


class _OwnerToken:
    def __init__(self, owner: "FileHardwareOwner") -> None:
        self.owner = owner
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.owner._release()
            self.released = True


class FileHardwareOwner:
    """An OS lock plus a durable crash marker protects ST-Link across processes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._guard = threading.RLock()
        self._file = None
        self._depth = 0

    def _open_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(self.path), flags, 0o600)
            handle = os.fdopen(fd, "r+b")
            if os.name != "nt":
                os.chmod(self.path, 0o600)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except (OSError, PermissionError) as error:
            if "handle" in locals():
                handle.close()
            raise HardwareOwnerBusy("ST-Link is owned by another process.") from error

    @staticmethod
    def _read_record(handle) -> dict:
        handle.seek(1)
        raw = handle.read(4097)
        if not raw:
            return {"state": "IDLE"}  # initial/legacy lock file
        if len(raw) > 4096:
            raise HardwareOwnerBusy("HARDWARE_RECOVERY_REQUIRED: corrupt owner record.")
        try:
            record = json.loads(raw.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise HardwareOwnerBusy("HARDWARE_RECOVERY_REQUIRED: corrupt owner record.") from error
        if (not isinstance(record, dict) or record.get("schema_version") != 1
                or record.get("state") not in {"IDLE", "ACTIVE"}):
            raise HardwareOwnerBusy("HARDWARE_RECOVERY_REQUIRED: invalid owner record.")
        if record["state"] == "ACTIVE" and (
                type(record.get("pid")) is not int or record["pid"] <= 0
                or not isinstance(record.get("instance_id"), str)
                or len(record["instance_id"]) != 32):
            raise HardwareOwnerBusy("HARDWARE_RECOVERY_REQUIRED: invalid owner identity.")
        return record

    @staticmethod
    def _write_record(handle, record: dict) -> None:
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
        handle.seek(1)
        handle.write(encoded)
        handle.truncate(1 + len(encoded))
        handle.flush()
        os.fsync(handle.fileno())

    @staticmethod
    def _unlock(handle) -> None:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def acquire(self) -> _OwnerToken:
        with self._guard:
            if self._depth:
                self._depth += 1
                return _OwnerToken(self)
            handle = self._open_locked()
            try:
                record = self._read_record(handle)
                if record.get("state") != "IDLE":
                    raise HardwareOwnerBusy("HARDWARE_RECOVERY_REQUIRED: previous owner exited during hardware use.")
                self._write_record(handle, {
                    "schema_version": 1, "state": "ACTIVE",
                    "pid": os.getpid(), "instance_id": uuid.uuid4().hex,
                })
            except BaseException:
                self._unlock(handle)
                raise
            self._file = handle
            self._depth = 1
            return _OwnerToken(self)

    def recover(self, *, confirm: bool, quiescent_probe=openocd_quiescent) -> None:
        if confirm is not True:
            raise HardwareOwnerBusy("HARDWARE_RECOVERY_REQUIRED: explicit confirmation is required.")
        with self._guard:
            if self._depth:
                raise HardwareOwnerBusy("ST-Link is owned by this process.")
            handle = self._open_locked()
            try:
                record = self._read_record(handle)
                if record.get("state") == "IDLE":
                    return
                if quiescent_probe() is not True:
                    raise HardwareOwnerBusy("HARDWARE_RECOVERY_REQUIRED: OpenOCD or ST-Link owner is not proven stopped.")
                self._write_record(handle, {"schema_version": 1, "state": "IDLE"})
            finally:
                self._unlock(handle)

    def _release(self) -> None:
        with self._guard:
            if not self._depth:
                raise RuntimeError("Hardware owner token was already released.")
            self._depth -= 1
            if self._depth:
                return
            handle = self._file
            self._file = None
            if handle is None:
                raise RuntimeError("Hardware owner lock handle is missing.")
            try:
                self._write_record(handle, {"schema_version": 1, "state": "IDLE"})
            finally:
                self._unlock(handle)


DEFAULT_HARDWARE_OWNER = FileHardwareOwner(
    Path.home() / ".b300-stlink" / "hardware-owner.lock"
)
