"""OS-held cross-process lock for one B300 Gateway host's ST-Link hardware."""

from __future__ import annotations

import os
import threading
from pathlib import Path


class HardwareOwnerBusy(RuntimeError):
    pass


class _OwnerToken:
    def __init__(self, owner: "FileHardwareOwner") -> None:
        self.owner = owner
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.owner._release()
            self.released = True


class FileHardwareOwner:
    """An OS lock survives thread changes and is released on process death."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._guard = threading.RLock()
        self._file = None
        self._depth = 0

    def acquire(self) -> _OwnerToken:
        with self._guard:
            if self._depth:
                self._depth += 1
                return _OwnerToken(self)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(self.path.parent, 0o700)
            handle = self.path.open("a+b")
            try:
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
            except (OSError, PermissionError) as error:
                handle.close()
                raise HardwareOwnerBusy("ST-Link is owned by another process.") from error
            self._file = handle
            self._depth = 1
            return _OwnerToken(self)

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
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


DEFAULT_HARDWARE_OWNER = FileHardwareOwner(
    Path.home() / ".b300-stlink" / "hardware-owner.lock"
)
