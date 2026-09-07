"""Single-owner Gateway lifecycle with fail-closed hardware health."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence

from .debug_service import DebugConfig, DebugService, DebugState
from .gateway_status import GatewaySnapshot, SUPPORTED_SCHEMA_VERSION
from .gateway_protocol import gateway_capabilities
from .models import ProbeInfo
from .probe import list_probes
from .probe_presence import ProbePresenceTracker
from .probe_selection import ProbeSelectionError, select_probe
from .remote_debug_guard import RemoteDebugGuard
from .tcl_client import SafeTclClient, TclEndpoint
from .process_startup import child_process_kwargs


def gateway_runtime_root() -> Path:
    override = os.environ.get("B300_GATEWAY_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".b300-stlink" / "gateway-runtime"


class GatewayStatusStore:
    """Atomic per-user snapshot used by short-lived SSH CLI commands."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else gateway_runtime_root()
        self.status_path = self.root / "status.json"
        self.start_lock_path = self.root / "starting.lock"
        self.log_path = self.root / "gateway.log"

    def request_rescan(self) -> Path:
        """Queue a one-shot hardware rescan for the persistent per-user owner."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ("rescan-%s.request" % uuid.uuid4().hex)
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, b"rescan\n")
        finally:
            os.close(fd)
        return path

    def consume_rescan_requests(self) -> bool:
        """Consume all queued requests; coalescing is safe for read-only discovery."""
        requested = False
        try:
            paths = tuple(self.root.glob("rescan-*.request"))
        except OSError:
            return False
        for path in paths:
            try:
                path.unlink()
                requested = True
            except FileNotFoundError:
                pass
        return requested

    def write(self, snapshot: GatewaySnapshot, *, owner_pid: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        record = snapshot.to_record()
        record.update(gateway_capabilities())
        record["owner_pid"] = int(owner_pid)
        fd, temp_name = tempfile.mkstemp(prefix="status.", suffix=".tmp", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(record, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.status_path)
        finally:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass

    def read(self):
        try:
            record = json.loads(self.status_path.read_text(encoding="utf-8"))
            snapshot = GatewaySnapshot.from_record(record)
            owner_pid = int(record.get("owner_pid", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return snapshot, owner_pid

    def age_seconds(self) -> float:
        try:
            return max(0.0, time.time() - self.status_path.stat().st_mtime)
        except OSError:
            return float("inf")


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class GatewayProcessManager:
    """Coordinate idempotent detached Gateway startup across SSH invocations."""

    def __init__(self, *, store: Optional[GatewayStatusStore] = None,
                 process_alive: Callable[[int], bool] = _process_alive,
                 process_factory: Optional[Callable[[Sequence[str]], object]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store or GatewayStatusStore()
        self._process_alive = process_alive
        self._process_factory = process_factory or self._spawn
        self._clock = clock
        self._sleep = sleep

    def status(self) -> GatewaySnapshot:
        stored = self.store.read()
        if stored is not None:
            snapshot, owner_pid = stored
            if self._process_alive(owner_pid):
                if snapshot.attach_ready and self.store.age_seconds() > 5.0:
                    return GatewaySnapshot.from_record({
                        "schema_version": SUPPORTED_SCHEMA_VERSION,
                        "instance_id": snapshot.instance_id,
                        "generation": snapshot.generation,
                        "sequence": snapshot.sequence + 1,
                        "state": "DISCONNECTED",
                        "reason_code": "GATEWAY_HEARTBEAT_STALE",
                        "selected_probe": None,
                        "gdb_endpoint": None,
                        "tcl_endpoint": None,
                        "cpu_state": "unknown",
                        "evidence_age_ms": None,
                    })
                return snapshot
            if snapshot.state in {"WAITING_PROBE", "WAITING_SELECTION", "DISCONNECTED", "FAILED"}:
                return snapshot
            generation = snapshot.generation
            sequence = snapshot.sequence + 1
        else:
            generation = 0
            sequence = 0
        return GatewaySnapshot.from_record({
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "instance_id": "stopped",
            "generation": generation,
            "sequence": sequence,
            "state": "STOPPED",
            "reason_code": "GATEWAY_PROCESS_NOT_RUNNING",
            "selected_probe": None,
            "gdb_endpoint": None,
            "tcl_endpoint": None,
            "cpu_state": "unknown",
            "evidence_age_ms": None,
        })

    def ensure(self, command: Sequence[str], *, timeout_seconds: float = 12.0) -> GatewaySnapshot:
        current = self.status()
        if current.attach_ready:
            return current
        argv = tuple(str(item) for item in command)
        lowered = tuple(item.lower() for item in argv)
        if not argv or "sudo" in lowered or "--bind-address" not in lowered:
            raise ValueError("Managed Gateway command must be an explicit unprivileged loopback command.")
        bind_index = lowered.index("--bind-address")
        if bind_index + 1 >= len(argv) or argv[bind_index + 1] != "127.0.0.1":
            raise ValueError("Managed Gateway command must bind loopback only.")
        if not 0 < float(timeout_seconds) <= 60:
            raise ValueError("Gateway startup timeout must be greater than 0 and at most 60 seconds.")
        self.store.root.mkdir(parents=True, exist_ok=True)
        stored = self.store.read()
        live_owner_pid = 0
        if stored is not None and self._process_alive(stored[1]):
            live_owner_pid = stored[1]
        lock_fd = None
        started_process = None
        try:
            if not live_owner_pid:
                try:
                    lock_fd = os.open(
                        str(self.store.start_lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                    )
                    os.write(lock_fd, str(os.getpid()).encode("ascii"))
                except FileExistsError:
                    lock_fd = None
            if lock_fd is not None:
                started_process = self._process_factory(argv)
            deadline = self._clock() + float(timeout_seconds)
            last = self.status()
            while not last.attach_ready and self._clock() < deadline:
                if (started_process is not None
                        and started_process.poll() is not None
                        and last.state != "STARTING"):
                    break
                self._sleep(min(0.1, max(0.0, deadline - self._clock())))
                last = self.status()
            return last
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
                try:
                    self.store.start_lock_path.unlink()
                except OSError:
                    pass

    def rescan(self, command: Sequence[str], *, timeout_seconds: float = 12.0) -> GatewaySnapshot:
        """Ask the live owner for fresh discovery, or start one when none exists."""
        if not 0 < float(timeout_seconds) <= 60:
            raise ValueError("Gateway rescan timeout must be greater than 0 and at most 60 seconds.")
        stored = self.store.read()
        if stored is None or not self._process_alive(stored[1]):
            return self.ensure(command, timeout_seconds=timeout_seconds)
        before, owner_pid = stored
        self.store.request_rescan()
        deadline = self._clock() + float(timeout_seconds)
        last = self.status()
        while self._clock() < deadline:
            if (last.instance_id != before.instance_id
                    or last.sequence > before.sequence):
                return last
            if not self._process_alive(owner_pid):
                break
            self._sleep(min(0.1, max(0.0, deadline - self._clock())))
            last = self.status()
        return last

    def _spawn(self, command: Sequence[str]):
        self.store.root.mkdir(parents=True, exist_ok=True)
        output = self.store.log_path.open("ab")
        kwargs = child_process_kwargs()
        if os.name == "nt":
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            return subprocess.Popen(
                list(command), stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.STDOUT, shell=False, **kwargs,
            )
        finally:
            output.close()


class GatewaySupervisor:
    """Own one DebugService and publish only target-verified READY snapshots."""

    def __init__(self, *, service_factory: Callable[[], object] = DebugService,
                 probe_discovery: Callable[[], Sequence[ProbeInfo]] = list_probes,
                 target_state_probe: Optional[Callable[[DebugConfig], str]] = None,
                 remote_guard_factory: Optional[Callable[[DebugConfig], object]] = None,
                 snapshot_sink: Optional[Callable[[GatewaySnapshot], None]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 gdb_port: int = 3333, tcl_port: int = 6666,
                 requested_serial: Optional[str] = None) -> None:
        if not 1 <= int(gdb_port) <= 65535 or not 1 <= int(tcl_port) <= 65535:
            raise ValueError("Gateway ports must be in range 1..65535.")
        if int(gdb_port) == int(tcl_port):
            raise ValueError("Gateway GDB and TCL ports must be distinct.")
        self._service_factory = service_factory
        self._probe_discovery = probe_discovery
        self._target_state_probe = target_state_probe or self._probe_target_state
        self._remote_guard_factory = remote_guard_factory or self._create_remote_guard
        self._sink = snapshot_sink
        self._clock = clock
        self._gdb_port = int(gdb_port)
        self._tcl_port = int(tcl_port)
        self._requested_serial = requested_serial
        self._instance_id = uuid.uuid4().hex
        self._sequence = 0
        self._generation = 0
        self._service = None
        self._remote_guard = None
        self._selected: Optional[ProbeInfo] = None
        self._presence: Optional[ProbePresenceTracker] = None
        self._evidence_at: Optional[float] = None
        self._hardware_error = False
        self._manual_stop = False
        self._lock = threading.RLock()
        self._snapshot = GatewaySnapshot.from_record({
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "instance_id": self._instance_id,
            "generation": 0,
            "sequence": 0,
            "state": "STOPPED",
            "reason_code": "USER_STOPPED",
            "selected_probe": None,
            "gdb_endpoint": None,
            "tcl_endpoint": None,
            "cpu_state": "unknown",
            "evidence_age_ms": None,
        })

    @property
    def snapshot(self) -> GatewaySnapshot:
        with self._lock:
            return self._snapshot

    def ensure(self) -> GatewaySnapshot:
        with self._lock:
            self._manual_stop = False
            if self._service is not None:
                if (self._snapshot.state == "READY"
                        and self._service.state in (DebugState.READY, DebugState.CONNECTED)):
                    return self._snapshot
                return self.observe()
            try:
                selected, probe_ref = select_probe(
                    tuple(self._probe_discovery()), self._requested_serial,
                )
            except ProbeSelectionError as error:
                state = "WAITING_PROBE" if error.code == "NO_PROBE" else "WAITING_SELECTION"
                return self._publish(state, error.code)
            self._selected = selected
            self._presence = ProbePresenceTracker(selected)
            presence = self._presence.observe(tuple(self._probe_discovery()))
            self._generation = max(self._generation + 1, presence.generation)
            config = DebugConfig(
                probe_ref, "127.0.0.1", self._gdb_port, None, self._tcl_port,
                gdb_max_connections=2,
            )
            self._publish("STARTING", "START_REQUESTED")
            service = self._service_factory()
            self._service = service
            self._hardware_error = False
            try:
                service.start(config, event_sink=self._on_openocd_line)
            except Exception:
                try:
                    service.stop()
                finally:
                    self._service = None
                return self._publish("FAILED", "TARGET_UNVERIFIED")
            if self._hardware_error:
                service.stop()
                self._service = None
                return self._publish("DISCONNECTED", "OPENOCD_HARDWARE_ERROR")
            try:
                cpu_state = str(self._target_state_probe(config)).lower()
                if cpu_state not in {"running", "halted"}:
                    raise RuntimeError("OpenOCD did not return verified target run state.")
            except Exception:
                # GDB attach/detach can briefly delay the independent TCL
                # health probe. Revoke attach readiness, but retain this
                # verified OpenOCD owner so the next health cycle can recover
                # without colliding with its still-bound listeners.
                return self._publish("DISCONNECTED", "TARGET_UNVERIFIED")
            if self._hardware_error:
                service.stop()
                self._service = None
                return self._publish("DISCONNECTED", "OPENOCD_HARDWARE_ERROR")
            self._arm_remote_guard(config, cpu_state)
            self._evidence_at = self._clock()
            return self._publish("READY", "TARGET_VERIFIED", cpu_state=cpu_state)

    def observe(self) -> GatewaySnapshot:
        with self._lock:
            if self._manual_stop or self._snapshot.state == "STOPPED":
                return self._snapshot
            service = self._service
            if (self._hardware_error
                    or (self._snapshot.state == "DISCONNECTED"
                        and self._snapshot.reason_code == "OPENOCD_HARDWARE_ERROR")):
                if service is not None:
                    self._stop_service("hardware_error")
                return self._publish("DISCONNECTED", "OPENOCD_HARDWARE_ERROR")
            if service is None or service.state not in (DebugState.READY, DebugState.CONNECTED):
                return self._publish("FAILED", "OPENOCD_EXITED")
            if self._presence is None:
                return self._publish("FAILED", "PROBE_IDENTITY_LOST")
            presence = self._presence.observe(tuple(self._probe_discovery()))
            if presence.kind in {"REMOVED", "AMBIGUOUS"}:
                self._stop_service("probe_removed")
                reason = "PROBE_REMOVED" if presence.kind == "REMOVED" else "PROBE_IDENTITY_AMBIGUOUS"
                return self._publish("DISCONNECTED", reason)
            try:
                config = DebugConfig(
                    select_probe((presence.probe,), presence.probe.serial)[1],
                    "127.0.0.1", self._gdb_port, None, self._tcl_port,
                    gdb_max_connections=2,
                )
                cpu_state = str(self._target_state_probe(config)).lower()
                if cpu_state not in {"running", "halted"}:
                    raise RuntimeError("unverified target")
            except Exception:
                return self._publish("DISCONNECTED", "TARGET_UNVERIFIED")
            self._arm_remote_guard(config, cpu_state)
            if self._hardware_error:
                self._stop_service("hardware_error")
                return self._publish("DISCONNECTED", "OPENOCD_HARDWARE_ERROR")
            self._evidence_at = self._clock()
            return self._publish("READY", "TARGET_VERIFIED", cpu_state=cpu_state)

    def rescan(self) -> GatewaySnapshot:
        with self._lock:
            if self._service is not None:
                return self.observe()
            return self.ensure()

    def maintain_once(self) -> GatewaySnapshot:
        """Advance the persistent owner one health/recovery cycle.

        A READY owner verifies both USB presence and target state.  A revoked
        owner remains fail-closed for that cycle, then later cycles rescan and
        recreate OpenOCD only after one selectable probe is present again.
        """
        with self._lock:
            if self._manual_stop:
                return self._snapshot
            if self._service is not None:
                return self.observe()
            return self.ensure()

    def stop(self) -> GatewaySnapshot:
        with self._lock:
            self._manual_stop = True
            if self._service is not None:
                self._stop_service("server_shutdown")
            self._hardware_error = False
            return self._publish("STOPPED", "USER_STOPPED")

    def _on_openocd_line(self, line: str) -> None:
        lowered = str(line).lower()
        guard = self._remote_guard
        if guard is not None:
            guard.handle_openocd_line(line)
        if any(marker in lowered for marker in ("libusb", "target not examined", "swd fault", "error:")):
            # The owner loop performs serialized cleanup.  This callback only
            # revokes the public READY claim immediately.
            self._hardware_error = True
            with self._lock:
                if self._service is not None:
                    self._publish("DISCONNECTED", "OPENOCD_HARDWARE_ERROR")

    def _arm_remote_guard(self, config: DebugConfig, initial_state: str) -> None:
        if self._remote_guard is not None:
            return
        guard = self._remote_guard_factory(config)
        guard.capture_initial_state(initial_state)
        self._remote_guard = guard

    def _stop_service(self, reason: str) -> None:
        service = self._service
        guard = self._remote_guard
        self._remote_guard = None
        if guard is not None:
            try:
                guard.restore_initial_state(reason=reason)
            except Exception:
                pass
        if service is not None:
            service.stop()
        self._service = None

    def _publish(self, state: str, reason_code: str,
                 *, cpu_state: str = "unknown") -> GatewaySnapshot:
        self._sequence += 1
        ready = state == "READY"
        selected = None
        if self._selected is not None and ready:
            selected = {
                "serial": self._selected.serial,
                "usb_identity": self._selected.usb_identity,
                "source": self._selected.source,
            }
        evidence_age = None
        if ready and self._evidence_at is not None:
            evidence_age = max(0, int((self._clock() - self._evidence_at) * 1000))
        self._snapshot = GatewaySnapshot.from_record({
            "schema_version": SUPPORTED_SCHEMA_VERSION,
            "instance_id": self._instance_id,
            "generation": self._generation,
            "sequence": self._sequence,
            "state": state,
            "reason_code": reason_code,
            "selected_probe": selected,
            "gdb_endpoint": "127.0.0.1:%d" % self._gdb_port if ready else None,
            "tcl_endpoint": "127.0.0.1:%d" % self._tcl_port if ready else None,
            "cpu_state": cpu_state if ready else "unknown",
            "evidence_age_ms": evidence_age,
        })
        if self._sink is not None:
            self._sink(self._snapshot)
        return self._snapshot

    @staticmethod
    def _probe_target_state(config: DebugConfig) -> str:
        if config.tcl_port is None:
            raise RuntimeError("Gateway health requires a loopback TCL endpoint.")
        return SafeTclClient(TclEndpoint("127.0.0.1", config.tcl_port)).wait_target_state()

    @staticmethod
    def _create_remote_guard(config: DebugConfig) -> RemoteDebugGuard:
        if config.tcl_port is None:
            raise RuntimeError("Managed Gateway run-state guard requires loopback TCL.")
        return RemoteDebugGuard(SafeTclClient(TclEndpoint("127.0.0.1", config.tcl_port)))


__all__ = [
    "GatewayProcessManager", "GatewayStatusStore", "GatewaySupervisor",
    "gateway_runtime_root",
]
