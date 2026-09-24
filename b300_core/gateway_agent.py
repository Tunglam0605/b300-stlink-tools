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
from .gateway_protocol import gateway_capabilities
from .process_startup import child_process_kwargs


LEGACY_AGENT_CAPABILITIES = (
    "gateway-status", "gateway-ensure", "gateway-rescan",
    "gateway-gdb-activity-v1", "gateway-agent", "gateway-exclusive-lease-v1",
)


@dataclass(frozen=True)
class GatewayAgentStatus:
    instance_id: str
    pid: int
    heartbeat_mono: float
    state: str
    reason_code: str
    capabilities: tuple[str, ...] = LEGACY_AGENT_CAPABILITIES

    def to_record(self) -> dict:
        return {
            "schema_version": 1, "instance_id": self.instance_id, "pid": self.pid,
            "heartbeat_mono": self.heartbeat_mono, "state": self.state,
            "reason_code": self.reason_code,
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "GatewayAgentStatus":
        required = {
            "schema_version", "instance_id", "pid", "heartbeat_mono", "state", "reason_code",
        }
        if (not isinstance(record, Mapping) or not required <= set(record)
                or not set(record) <= required | {"capabilities"}):
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
        capabilities = record.get("capabilities", list(LEGACY_AGENT_CAPABILITIES))
        if (not isinstance(capabilities, list) or len(capabilities) > 32
                or any(not isinstance(value, str) or not value or len(value) > 64
                       for value in capabilities)):
            raise ValueError("Gateway Agent capabilities are invalid.")
        return cls(record["instance_id"], record["pid"], float(heartbeat),
                   record["state"], record["reason_code"], tuple(capabilities))


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
            # Windows may briefly deny replacement while a status reader has
            # the previous file open. Retry for a bounded interval so the
            # status heartbeat cannot die from a transient sharing violation.
            replaced = False
            for attempt in range(5):
                try:
                    os.replace(str(temp), str(self.path))
                    replaced = True
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01)
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
    except SystemError:
        # CPython on Windows can surface an underlying Win32 probe failure as
        # SystemError instead of OSError.  Unknown liveness is not proof that
        # an Agent owner is dead, so retain the owner/status fail-closed.
        return True
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
                    # A concurrent creator may have reserved the lock file but
                    # not written its PID yet. Treat only an empty/transient
                    # file as busy; preserve fail-closed handling for corrupt
                    # non-empty owner records.
                    try:
                        transient = not self.path.read_text(encoding="ascii").strip()
                    except OSError:
                        transient = True
                    raise RuntimeError("ALREADY_RUNNING" if transient else "LOCK_CORRUPT")
                # An owner record for this process is necessarily live even
                # when a caller supplies a process probe scoped to child PIDs.
                # This also serializes concurrent managers in the same process.
                if existing > 0 and (existing == self.pid or self._process_alive(existing)):
                    raise RuntimeError("ALREADY_RUNNING")
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    raise RuntimeError("ALREADY_RUNNING")
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
                 program_jobs: Optional[object] = None,
                 status_sink: Optional[Callable[[GatewayAgentSnapshot], None]] = None,
                 socket_server: Optional[object] = None,
                 capabilities=None,
                 clock: Callable[[], float] = time.monotonic,
                 poll_interval_seconds: float = 0.25) -> None:
        if not 0.02 <= float(poll_interval_seconds) <= 5.0:
            raise ValueError("Gateway Agent poll interval must be in [0.02, 5.0].")
        self.coordinator = coordinator
        self.requests = request_store or GatewayRequestStore()
        self.program_jobs = program_jobs
        self._clock = clock
        self._status_sink = status_sink
        self._socket_server = socket_server
        self._capabilities_provider = (capabilities if callable(capabilities)
                                       else lambda: tuple(capabilities or gateway_capabilities()["capabilities"]))
        self._poll_interval = float(poll_interval_seconds)
        self._shutdown_requested = False
        self._inflight_prepare = set()
        self._prepare_workers = {}
        self._prepare_lock = threading.RLock()

    def run_once(self) -> GatewayAgentSnapshot:
        for request in self.requests.pending():
            if request.operation == "program_prepare":
                with self._prepare_lock:
                    if request.request_id in self._inflight_prepare:
                        continue
                    self._inflight_prepare.add(request.request_id)
                    worker = threading.Thread(
                        target=self._run_prepare_request, args=(request,),
                        name="b300-gateway-program-prepare", daemon=False,
                    )
                    self._prepare_workers[request.request_id] = worker
                    worker.start()
            else:
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
        socket_failures = []
        socket_thread = None
        if self._socket_server is not None:
            def serve_socket() -> None:
                try:
                    self._socket_server.serve(event)
                except Exception as error:
                    socket_failures.append(error)
                    event.set()
            socket_thread = threading.Thread(target=serve_socket,
                                             name="b300-gateway-control-socket", daemon=False)
            socket_thread.start()
        try:
            while not self._shutdown_requested:
                if socket_failures:
                    raise RuntimeError("Gateway socket server failed") from socket_failures[0]
                self.run_once()
                if event.wait(self._poll_interval):
                    break
            if socket_failures:
                raise RuntimeError("Gateway socket server failed") from socket_failures[0]
            return 0
        finally:
            event.set()
            if socket_thread is not None:
                socket_thread.join()
            with self._prepare_lock:
                workers = tuple(self._prepare_workers.values())
            for worker in workers:
                worker.join()
            if self.program_jobs is not None:
                self.program_jobs.wait_active()
            self.coordinator.shutdown("AGENT_SHUTDOWN")

    def _run_prepare_request(self, request: GatewayRequest) -> None:
        try:
            self._dispatch_once(request)
        finally:
            with self._prepare_lock:
                self._inflight_prepare.discard(request.request_id)
                self._prepare_workers.pop(request.request_id, None)

    def _dispatch_once(self, request: GatewayRequest) -> None:
        try:
            if self._clock() >= request.expires_mono:
                response = self._error(request, "REQUEST_EXPIRED")
            else:
                response = self._dispatch(request)
        except (KeyError, TypeError, ValueError) as error:
            response = self._error(request, "REQUEST_INVALID", str(error))
        except Exception as error:
            from .gateway_program_jobs import ProgramJobError
            if isinstance(error, ProgramJobError):
                response = self._error(request, error.reason_code, str(error))
            else:
                response = self._error(request, "AGENT_OPERATION_FAILED")
        response["capabilities"] = list(self._capabilities_provider())
        self.requests.respond(request.request_id, response, request=request)
        self.requests.complete(request.request_id)

    def _dispatch(self, request: GatewayRequest) -> dict:
        operation = request.operation
        payload = request.payload
        if operation.startswith("program_"):
            return self._dispatch_program(request)
        if operation == "status":
            self._exact_keys(payload, set())
            result = self.coordinator.public_snapshot().to_record()
            result["capabilities"] = list(self._capabilities_provider())
            return self._ok(request, result)
        if operation in {"runtime_status", "runtime_ensure", "runtime_rescan"}:
            self._exact_keys(payload, set())
            action = operation.removeprefix("runtime_")
            return self._ok(request, self.coordinator.runtime_snapshot(action).to_record())
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

    def _dispatch_program(self, request: GatewayRequest) -> dict:
        from .gateway_program_jobs import GatewayProgramJobs
        from .remote_programming import RemoteFirmwareManifest

        if self.program_jobs is None:
            self.program_jobs = GatewayProgramJobs(coordinator=self.coordinator)
        jobs = self.program_jobs
        payload = request.payload
        operation = request.operation
        if operation == "program_create_upload":
            self._exact_keys(payload, {"manifest", "client_id", "probe_serial"})
            if not isinstance(payload["manifest"], dict):
                raise ValueError("Firmware manifest must be an object.")
            manifest = RemoteFirmwareManifest(**payload["manifest"]).validate()
            return self._ok(request, jobs.create_upload(
                manifest, payload["client_id"], payload["probe_serial"],
                request_id=request.request_id,
            ))
        if operation == "program_finalize_upload":
            self._exact_keys(payload, {"job_id"})
            return self._ok(request, jobs.finalize_upload(payload["job_id"]))
        if operation == "program_status":
            self._exact_keys(payload, {"job_id"})
            return self._ok(request, jobs.status(payload["job_id"]))
        if operation == "program_cleanup":
            self._exact_keys(payload, {"job_id"})
            return self._ok(request, jobs.cleanup(payload["job_id"]))
        lease_fields = {"job_id", "lease_id", "lease_token", "lease_generation"}
        if operation == "program_prepare":
            self._exact_keys(payload, lease_fields)
            return self._ok(request, jobs.prepare(
                payload["job_id"], payload["lease_id"],
                payload["lease_token"], payload["lease_generation"],
            ))
        if operation == "program_cancel":
            self._exact_keys(payload, lease_fields)
            return self._ok(request, jobs.cancel(
                payload["job_id"], payload["lease_id"],
                payload["lease_token"], payload["lease_generation"],
            ))
        if operation == "program_commit":
            self._exact_keys(payload, lease_fields | {"approval_token"})
            return self._ok(request, jobs.commit(
                payload["job_id"], payload["approval_token"],
                payload["lease_id"], payload["lease_token"],
                payload["lease_generation"],
            ))
        raise ValueError("Unsupported Gateway programming operation.")

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
