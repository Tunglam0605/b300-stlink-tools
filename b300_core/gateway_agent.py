"""Sleeping per-user Gateway Agent; hardware wakes only for a lease request."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from .gateway_agent_protocol import GatewayRequest, GatewayRequestStore
from .gateway_lease import (
    GatewayLeaseBusy,
    GatewayLeaseGrant,
    GatewayLeaseRequest,
)
from .gateway_supervisor import gateway_runtime_root
from .process_startup import child_process_kwargs


@dataclass(frozen=True)
class GatewayAgentStatus:
    instance_id: str
    pid: int
    heartbeat_mono: float
    state: str
    reason_code: str

    def to_record(self) -> dict:
        return {
            "schema_version": 1, "instance_id": self.instance_id, "pid": self.pid,
            "heartbeat_mono": self.heartbeat_mono, "state": self.state,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "GatewayAgentStatus":
        if not isinstance(record, Mapping) or set(record) != {
            "schema_version", "instance_id", "pid", "heartbeat_mono", "state", "reason_code",
        }:
            raise ValueError("Gateway Agent status schema is invalid.")
        if type(record["schema_version"]) is not int or record["schema_version"] != 1:
            raise ValueError("Gateway Agent status version is unsupported.")
        if not isinstance(record["instance_id"], str) or not record["instance_id"]:
            raise ValueError("Gateway Agent instance id is invalid.")
        if type(record["pid"]) is not int or record["pid"] <= 0:
            raise ValueError("Gateway Agent pid is invalid.")
        heartbeat = record["heartbeat_mono"]
        if not isinstance(heartbeat, (int, float)) or isinstance(heartbeat, bool) or heartbeat < 0:
            raise ValueError("Gateway Agent heartbeat is invalid.")
        if not isinstance(record["state"], str) or not isinstance(record["reason_code"], str):
            raise ValueError("Gateway Agent state is invalid.")
        return cls(record["instance_id"], record["pid"], float(heartbeat),
                   record["state"], record["reason_code"])


class GatewayAgentStatusStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else gateway_runtime_root() / "agent-status.json"
        self.start_lock_path = self.path.with_name("agent-start.lock")

    def write(self, status: GatewayAgentStatus) -> None:
        selected = GatewayAgentStatus.from_record(status.to_record())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="agent-status.", suffix=".tmp", dir=str(self.path.parent))
        temp = Path(name)
        try:
            if os.name != "nt": os.chmod(str(temp), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(selected.to_record(), handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.replace(str(temp), str(self.path))
        finally:
            try: temp.unlink()
            except OSError: pass

    def read(self) -> Optional[GatewayAgentStatus]:
        try:
            return GatewayAgentStatus.from_record(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return None


def _process_alive(pid: int) -> bool:
    try: os.kill(pid, 0)
    except OSError: return False
    return pid > 0


class GatewayAgentOwnerLock:
    """Atomic per-user owner lock; stale owners may be reclaimed safely."""

    def __init__(self, path: Path, *, pid: Optional[int] = None,
                 process_alive: Callable[[int], bool] = _process_alive) -> None:
        self.path = Path(path)
        self.pid = int(pid if pid is not None else os.getpid())
        self._process_alive = process_alive
        self._held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, (str(self.pid) + "\n").encode("ascii"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self._held = True
                return
            except FileExistsError:
                try:
                    raw = self.path.read_text(encoding="ascii").strip()
                    existing = int(raw)
                except (OSError, UnicodeError, ValueError):
                    raise RuntimeError("LOCK_CORRUPT")
                if existing > 0 and self._process_alive(existing):
                    raise RuntimeError("ALREADY_RUNNING")
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
        raise RuntimeError("ALREADY_RUNNING")

    def release(self) -> None:
        if not self._held:
            return
        try:
            raw = self.path.read_text(encoding="ascii").strip()
            if int(raw) == self.pid:
                self.path.unlink()
        except (OSError, UnicodeError, ValueError):
            pass
        finally:
            self._held = False


class GatewayAgentProcessManager:
    def __init__(self, *, store: Optional[GatewayAgentStatusStore] = None,
                 process_alive: Callable[[int], bool] = _process_alive,
                 process_factory: Optional[Callable[[Sequence[str]], object]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store or GatewayAgentStatusStore()
        self._process_alive = process_alive
        self._process_factory = process_factory or self._spawn
        self._clock = clock
        self._sleep = sleep

    def status(self) -> Optional[GatewayAgentStatus]:
        status = self.store.read()
        if status is None or not self._process_alive(status.pid): return None
        if self._clock() - status.heartbeat_mono > 5.0: return None
        return status

    def ensure_running(self, command: Sequence[str], *,
                       timeout_seconds: float = 5.0) -> GatewayAgentStatus:
        current = self.status()
        if current is not None: return current
        argv = tuple(str(item) for item in command)
        lowered = tuple(item.lower() for item in argv)
        if not argv or "sudo" in lowered or "gateway-agent" not in lowered:
            raise ValueError("Managed Gateway Agent command is invalid or privileged.")
        if not 0 < float(timeout_seconds) <= 60:
            raise ValueError("Gateway Agent startup timeout must be in (0, 60].")
        lock = GatewayAgentOwnerLock(
            self.store.start_lock_path, pid=os.getpid(),
            process_alive=self._process_alive,
        )
        acquired = False
        try:
            try:
                lock.acquire()
                acquired = True
            except RuntimeError as error:
                if str(error) not in {"ALREADY_RUNNING"}:
                    raise
            if acquired:
                if self.status() is None:
                    self._process_factory(argv)
        finally:
            if acquired:
                lock.release()
        deadline = self._clock() + float(timeout_seconds)
        while self._clock() < deadline:
            current = self.status()
            if current is not None: return current
            self._sleep(min(0.05, max(0.0, deadline - self._clock())))
        raise RuntimeError("GATEWAY_AGENT_START_TIMEOUT")

    def _spawn(self, command: Sequence[str]):
        log = gateway_runtime_root() / "gateway-agent.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        output = log.open("ab")
        kwargs = child_process_kwargs()
        if os.name == "nt":
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            return subprocess.Popen(list(command), stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT,
                                    shell=False, **kwargs)
        finally:
            output.close()


@dataclass(frozen=True)
class GatewayAgentSnapshot:
    state: str
    reason_code: str
    lease_active: bool

    def to_record(self) -> dict:
        return {
            "state": self.state,
            "reason_code": self.reason_code,
            "lease_active": self.lease_active,
        }


class GatewayAgent:
    def __init__(self, coordinator: object, *,
                 request_store: Optional[GatewayRequestStore] = None,
                 status_sink: Optional[Callable[[GatewayAgentSnapshot], None]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 poll_interval_seconds: float = 0.25) -> None:
        if not 0.02 <= float(poll_interval_seconds) <= 5.0:
            raise ValueError("Gateway Agent poll interval must be in [0.02, 5.0].")
        self.coordinator = coordinator
        self.requests = request_store or GatewayRequestStore()
        self._clock = clock
        self._status_sink = status_sink
        self._poll_interval = float(poll_interval_seconds)
        self._shutdown_requested = False

    def run_once(self) -> GatewayAgentSnapshot:
        for request in self.requests.pending():
            self._dispatch_once(request)
        lease = self.coordinator.tick()
        result = GatewayAgentSnapshot(
            state=str(getattr(lease, "state", "IDLE")),
            reason_code=str(getattr(lease, "reason_code", "GATEWAY_IDLE")),
            lease_active=bool(getattr(lease, "active", False)),
        )
        if self._status_sink is not None:
            self._status_sink(result)
        return result

    def run(self, stop_event: Optional[threading.Event] = None) -> int:
        event = stop_event or threading.Event()
        try:
            while not self._shutdown_requested:
                self.run_once()
                if event.wait(self._poll_interval):
                    break
            return 0
        finally:
            self.coordinator.shutdown("AGENT_SHUTDOWN")

    def _dispatch_once(self, request: GatewayRequest) -> None:
        try:
            if self._clock() >= request.expires_mono:
                response = self._error(request, "REQUEST_EXPIRED")
            else:
                response = self._dispatch(request)
        except (KeyError, TypeError, ValueError) as error:
            response = self._error(request, "REQUEST_INVALID", str(error))
        except Exception:
            response = self._error(request, "AGENT_OPERATION_FAILED")
        self.requests.respond(request.request_id, response)
        self.requests.complete(request.request_id)

    def _dispatch(self, request: GatewayRequest) -> dict:
        operation = request.operation
        payload = request.payload
        if operation == "status":
            self._exact_keys(payload, set())
            return self._ok(request, self.coordinator.public_snapshot().to_record())
        if operation == "acquire":
            self._exact_keys(payload, {
                "client_id", "client_label", "mode", "probe_serial",
            })
            result = self.coordinator.acquire(GatewayLeaseRequest(
                request_id=request.request_id,
                client_id=payload["client_id"],
                client_label=payload["client_label"],
                mode=payload["mode"],
                probe_serial=payload["probe_serial"],
            ))
            if isinstance(result, GatewayLeaseGrant):
                record = result.public.to_record()
                record.update({
                    "lease_id": result.lease_id,
                    "lease_token": result.token,
                    "lease_generation": result.generation,
                })
                return self._ok(request, record)
            if isinstance(result, GatewayLeaseBusy):
                return self._error(request, result.reason_code, details=result.to_record())
            return self._error(
                request, str(getattr(result, "reason_code", "GATEWAY_START_FAILED")),
                details=self._record(result),
            )
        if operation in {"renew", "release"}:
            self._exact_keys(payload, {"lease_id", "lease_token", "lease_generation"})
            method = getattr(self.coordinator, operation)
            result = method(
                payload["lease_id"], payload["lease_token"], payload["lease_generation"],
            )
            record = self._record(result)
            status = "ok" if record.get("reason_code") != "LEASE_INVALID" else "error"
            return self._response(request, status, record)
        if operation == "rescan":
            self._exact_keys(payload, set())
            return self._ok(request, self._record(self.coordinator.tick()))
        if operation == "shutdown":
            self._exact_keys(payload, set())
            result = self.coordinator.shutdown("AGENT_SHUTDOWN")
            self._shutdown_requested = True
            return self._ok(request, self._record(result))
        raise ValueError("Unsupported Gateway Agent operation.")

    @staticmethod
    def _exact_keys(payload: Mapping[str, object], expected: set) -> None:
        if set(payload) != expected:
            raise ValueError("Gateway Agent payload fields are invalid.")

    @staticmethod
    def _record(value: object) -> dict:
        method = getattr(value, "to_record", None)
        if not callable(method):
            raise ValueError("Gateway Agent result is not serializable.")
        record = method()
        if not isinstance(record, dict):
            raise ValueError("Gateway Agent result must serialize to an object.")
        return record

    @classmethod
    def _response(cls, request: GatewayRequest, status: str, details: dict) -> dict:
        return {"status": status, "reason_code": details.get("reason_code", "OK"),
                "result": details}

    @classmethod
    def _ok(cls, request: GatewayRequest, details: dict) -> dict:
        return cls._response(request, "ok", details)

    @classmethod
    def _error(cls, request: GatewayRequest, reason: str, message: str = "",
               details: Optional[dict] = None) -> dict:
        record = {"status": "error", "reason_code": reason}
        if message:
            record["message"] = message[:256]
        if details is not None:
            record["result"] = details
        return record


__all__ = [
    "GatewayAgent", "GatewayAgentOwnerLock", "GatewayAgentProcessManager",
    "GatewayAgentSnapshot", "GatewayAgentStatus", "GatewayAgentStatusStore",
]
