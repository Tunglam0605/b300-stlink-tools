"""Non-secret Client history for reconnecting to managed Gateway jobs."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional


_PROFILE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def default_history_path() -> Path:
    root = (
        Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        if os.name == "nt" else
        Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    )
    return root / "B300-STLink" / "remote_program_jobs.json"


class RemoteProgramHistory:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else default_history_path()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("Remote program history is invalid.")
        jobs = raw.get("jobs")
        if not isinstance(jobs, dict):
            raise ValueError("Remote program history is invalid.")
        return jobs

    def get(self, profile_id: str) -> Optional[str]:
        if _PROFILE_ID.fullmatch(str(profile_id)) is None:
            raise ValueError("Gateway profile id is invalid.")
        value = self._read().get(profile_id)
        return value if isinstance(value, str) and _JOB_ID.fullmatch(value) else None

    def save(self, profile_id: str, job_id: str) -> None:
        if _PROFILE_ID.fullmatch(str(profile_id)) is None or _JOB_ID.fullmatch(str(job_id)) is None:
            raise ValueError("Gateway profile or programming job id is invalid.")
        jobs = self._read()
        jobs[profile_id] = job_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix="remote-jobs-", suffix=".tmp", dir=str(self.path.parent))
        temporary = Path(temporary_name)
        try:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "jobs": jobs}, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
