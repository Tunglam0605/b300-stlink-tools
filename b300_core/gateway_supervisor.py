"""Single-owner Gateway lifecycle with fail-closed hardware health."""

from __future__ import annotations

import json
import hmac
import os
import socket
import re
import subprocess
import threading
import time
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

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


def _canonical_executable(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("OpenOCD executable identity is missing.")
    return os.path.normcase(os.path.realpath(os.path.abspath(text)))


def _process_identity(pid: int):
    """Return immutable OS evidence for one process, or ``None`` when unavailable."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if os.name != "nt":
        try:
            fields = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
            start = fields[19]
            executable = _canonical_executable(os.readlink(str(Path("/proc") / str(pid) / "exe")))
            boot = (Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip())
            if not boot:
                return None
            return {"pid": pid, "start_identity": start, "executable": executable, "boot_identity": boot}
        except (OSError, IndexError, ValueError, UnicodeError):
            return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.GetProcessTimes.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        )
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        )
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created = wintypes.FILETIME(); exited = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME(); user_time = wintypes.FILETIME()
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            # WMI's boot timestamp is stable for this boot and avoids treating a
            # machine reboot plus PID reuse as the old B300 owner.
            command = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                       "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')"]
            boot = subprocess.check_output(command, stderr=subprocess.DEVNULL, text=True, timeout=2).strip()
            if not boot:
                return None
            started = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            return {"pid": pid, "start_identity": str(started),
                    "executable": _canonical_executable(buffer.value), "boot_identity": boot}
        finally:
            kernel.CloseHandle(handle)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _closed_endpoints(gdb_endpoint: str, tcl_endpoint: str) -> bool:
    for endpoint in (gdb_endpoint, tcl_endpoint):
        host, _separator, port_text = endpoint.rpartition(":")
        try:
            connection = socket.create_connection((host, int(port_text)), timeout=0.15)
        except OSError:
            continue
        else:
            connection.close()
            return False
    return True


def _endpoint_owner_pid(endpoint: str):
    """Return a sole loopback TCP listener owner, otherwise fail closed."""
    host, separator, port_text = str(endpoint).rpartition(":")
    if host != "127.0.0.1" or not separator:
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    if os.name == "nt":
        return _windows_listener_owner_pid(port)
    return _linux_listener_owner_pid(host, port)


def _linux_listener_owner_pid(host: str, port: int):
    """Map a Linux loopback listener inode through /proc to one PID."""
    try:
        wanted_address = socket.inet_aton(host)
        inodes = set()
        for table_name in ("tcp",):
            for line in (Path("/proc/net") / table_name).read_text(encoding="ascii").splitlines()[1:]:
                fields = line.split()
                if len(fields) < 10 or fields[3] != "0A":  # TCP_LISTEN
                    continue
                address_text, separator, port_hex = fields[1].partition(":")
                if not separator or int(port_hex, 16) != port:
                    continue
                if int(address_text, 16).to_bytes(4, "little") != wanted_address:
                    continue
                inodes.add(fields[9])
        if not inodes:
            return None
        owners = set()
        for process_root in Path("/proc").iterdir():
            if not process_root.name.isdigit():
                continue
            try:
                for descriptor in (process_root / "fd").iterdir():
                    matched = re.fullmatch(r"socket:\[([0-9]+)\]", os.readlink(str(descriptor)))
                    if matched and matched.group(1) in inodes:
                        owners.add(int(process_root.name))
            except OSError:
                continue
        return next(iter(owners)) if len(owners) == 1 else None
    except (OSError, ValueError, OverflowError):
        return None


def _windows_listener_owner_pid(port: int):
    """Read the Windows IPv4 TCP listener table without shelling out."""
    try:
        import ctypes
        from ctypes import wintypes
        ip_helper = ctypes.WinDLL("iphlpapi", use_last_error=True)
        get_table = ip_helper.GetExtendedTcpTable
        get_table.argtypes = (wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                              wintypes.ULONG, wintypes.INT, wintypes.ULONG)
        get_table.restype = wintypes.DWORD
        size = wintypes.DWORD(0)
        error_more_data = 122
        if get_table(None, ctypes.byref(size), False, 2, 3, 0) not in (0, error_more_data):
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if get_table(buffer, ctypes.byref(size), False, 2, 3, 0) != 0:
            return None
        class Row(ctypes.Structure):
            _fields_ = [("state", wintypes.DWORD), ("local_addr", wintypes.DWORD),
                        ("local_port", wintypes.DWORD), ("remote_addr", wintypes.DWORD),
                        ("remote_port", wintypes.DWORD), ("pid", wintypes.DWORD)]
        count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
        offset = ctypes.sizeof(wintypes.DWORD)
        owners = set()
        loopback = int.from_bytes(socket.inet_aton("127.0.0.1"), "little")
        for index in range(count):
            row = Row.from_buffer_copy(buffer, offset + index * ctypes.sizeof(Row))
            if (row.local_addr == loopback and socket.ntohs(row.local_port & 0xFFFF) == port):
                owners.add(int(row.pid))
        return next(iter(owners)) if len(owners) == 1 else None
    except (AttributeError, OSError, ValueError):
        return None


class _GatewayOpenOcdOwnerStore:
    """Private, atomic recovery evidence. This record is never a public snapshot."""

    _FIELDS = frozenset({
        "schema_version", "pid", "start_identity", "executable", "boot_identity",
        "gateway_instance_id", "gateway_generation", "lease_id", "lease_generation",
        "lease_token_digest", "gdb_endpoint", "tcl_endpoint",
    })

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, record: Mapping[str, object]) -> None:
        validated = self._validate(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="openocd-owner.", suffix=".tmp", dir=str(self.path.parent))
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(validated, handle, sort_keys=True)
                handle.write("\n")
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try: Path(temporary).unlink()
            except OSError: pass

    def read(self):
        try:
            return self._validate(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def clear(self) -> None:
        try: self.path.unlink()
        except FileNotFoundError: pass
        except OSError: pass

    @classmethod
    def _validate(cls, record: Mapping[str, object]) -> dict:
        if not isinstance(record, Mapping) or set(record) != cls._FIELDS or record.get("schema_version") != 1:
            raise ValueError("Gateway OpenOCD owner record is invalid.")
        pid = record["pid"]
        generation = record["gateway_generation"]
        lease_generation = record["lease_generation"]
        if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                or not isinstance(generation, int) or isinstance(generation, bool) or generation < 1
                or not isinstance(lease_generation, int) or isinstance(lease_generation, bool) or lease_generation < 1):
            raise ValueError("Gateway OpenOCD owner record has invalid numeric identity.")
        result = dict(record)
        for field in cls._FIELDS - {"schema_version", "pid", "gateway_generation", "lease_generation"}:
            if not isinstance(result[field], str) or not result[field].strip() or len(result[field]) > 4096:
                raise ValueError("Gateway OpenOCD owner record has invalid identity.")
        result["executable"] = _canonical_executable(result["executable"])
        for endpoint in ("gdb_endpoint", "tcl_endpoint"):
            host, separator, port = result[endpoint].rpartition(":")
            if host != "127.0.0.1" or not separator or not 1 <= int(port) <= 65535:
                raise ValueError("Gateway OpenOCD owner endpoints must be loopback.")
        return result


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
                 requested_serial: Optional[str] = None,
                 owner_record_path: Optional[Path] = None,
                 process_identity: Callable[[int], object] = _process_identity,
                 shutdown_openocd: Optional[Callable[[str], None]] = None,
                 endpoints_closed: Callable[[str, str], bool] = _closed_endpoints,
                 endpoint_owner_pid: Callable[[str], object] = _endpoint_owner_pid,
                 recovery_timeout_seconds: float = 1.0) -> None:
        if not 1 <= int(gdb_port) <= 65535 or not 1 <= int(tcl_port) <= 65535:
            raise ValueError("Gateway ports must be in range 1..65535.")
        if int(gdb_port) == int(tcl_port):
            raise ValueError("Gateway GDB and TCL ports must be distinct.")
        if not 0.01 <= float(recovery_timeout_seconds) <= 10.0:
            raise ValueError("Gateway recovery timeout must be in [0.01, 10].")
        self._service_factory = service_factory
        self._probe_discovery = probe_discovery
        self._target_state_probe = target_state_probe or self._probe_target_state
        self._remote_guard_factory = remote_guard_factory or self._create_remote_guard
        self._sink = snapshot_sink
        self._clock = clock
        self._gdb_port = int(gdb_port)
        self._tcl_port = int(tcl_port)
        self._requested_serial = requested_serial
        self._owner_store = _GatewayOpenOcdOwnerStore(
            owner_record_path or (gateway_runtime_root() / "openocd-owner.json")
        )
        self._process_identity = process_identity
        self._shutdown_openocd = shutdown_openocd or self._safe_shutdown_openocd
        self._endpoints_closed = endpoints_closed
        self._endpoint_owner_pid = endpoint_owner_pid
        self._recovery_timeout = float(recovery_timeout_seconds)
        self._lease_owner_context = None
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
        self._gdb_connection_count = 0
        self._gdb_activity_generation = 0
        self._gdb_ever_attached = False
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
            ready = self._publish("READY", "TARGET_VERIFIED", cpu_state=cpu_state)
            self._persist_lease_owner_locked(ready, service)
            return ready

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

    def reconcile_lease_owner(self, lease: object) -> bool:
        """Reclaim only the exact persisted B300 OpenOCD owner after restart."""
        with self._lock:
            record = self._owner_store.read()
            if (record is None or not self._record_matches_lease(record, lease)
                    or not self._record_matches_config(record)):
                return False
            failure = []
            outcome = []
            deadline = time.monotonic() + self._recovery_timeout
            worker = threading.Thread(
                target=lambda: outcome.append(self._reconcile_owner_worker(record, failure, deadline)),
                name="b300-openocd-recovery-shutdown", daemon=True,
            )
            worker.start(); worker.join(timeout=max(0.0, deadline - time.monotonic()))
            if worker.is_alive() or failure or not outcome:
                return False
            return outcome[0] is True

    def prepare_lease_owner(self, lease_id: str, lease_token: str, lease_generation: int) -> None:
        """Bind the next locally created OpenOCD process to one private lease."""
        if (not isinstance(lease_id, str) or not lease_id or not isinstance(lease_token, str)
                or not lease_token or not isinstance(lease_generation, int) or isinstance(lease_generation, bool)
                or lease_generation < 1):
            raise ValueError("Gateway lease owner identity is invalid.")
        with self._lock:
            from .gateway_lease import token_digest
            self._lease_owner_context = (lease_id, token_digest(lease_token), lease_generation)

    def has_lease_owner_record(self, lease: object) -> bool:
        with self._lock:
            record = self._owner_store.read()
            return bool(record is not None and self._record_matches_lease(record, lease))

    def forget_lease_owner(self, _lease: object = None) -> None:
        """Clear private evidence only after coordinator cleanup succeeded."""
        with self._lock:
            self._owner_store.clear()
            self._lease_owner_context = None

    def confirm_lease_owner_stopped(self, lease: object, timeout_seconds: float) -> bool:
        """Prove normal release removed the recorded process and listeners."""
        if not 0 < float(timeout_seconds) <= 10.0:
            return False
        deadline = time.monotonic() + float(timeout_seconds)
        with self._lock:
            record = self._owner_store.read()
            if record is None:
                # A reservation can fail before OpenOCD starts (for example,
                # when no probe is present).  In that case absence of the
                # private owner record together with no retained service is
                # positive proof that there is nothing to clean up.
                return self._service is None and self._snapshot.state == "STOPPED"
            if not self._record_matches_lease(record, lease):
                return False
        while time.monotonic() < deadline:
            if not self._record_matches_live_process(record):
                try:
                    return bool(self._endpoints_closed(record["gdb_endpoint"], record["tcl_endpoint"]))
                except Exception:
                    return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return False

    def stop(self) -> GatewaySnapshot:
        with self._lock:
            self._manual_stop = True
            if self._service is not None:
                self._stop_service("server_shutdown")
            self._hardware_error = False
            return self._publish("STOPPED", "USER_STOPPED")

    def _persist_lease_owner_locked(self, snapshot: GatewaySnapshot, service: object) -> None:
        context = self._lease_owner_context
        process = getattr(service, "process", None)
        pid = getattr(process, "pid", None)
        if context is None or not isinstance(pid, int) or isinstance(pid, bool):
            return
        try:
            identity = self._validated_identity(self._process_identity(pid), pid)
            # The executable reported by the live PID is authoritative. The
            # configured command may be a bare PATH name such as ``openocd``.
            executable = identity["executable"]
            lease_id, lease_token_digest, lease_generation = context
            self._owner_store.write({
                "schema_version": 1, "pid": pid,
                "start_identity": identity["start_identity"], "executable": executable,
                "boot_identity": identity["boot_identity"],
                "gateway_instance_id": snapshot.instance_id,
                "gateway_generation": snapshot.generation,
                "lease_id": lease_id, "lease_generation": lease_generation,
                "lease_token_digest": lease_token_digest,
                "gdb_endpoint": snapshot.gdb_endpoint, "tcl_endpoint": snapshot.tcl_endpoint,
            })
        except (OSError, ValueError, TypeError):
            # The coordinator checks the record before it grants the lease.
            # Never publish this private evidence or fall back to PID-only proof.
            return

    @staticmethod
    def _validated_identity(value: object, expected_pid: int) -> dict:
        if not isinstance(value, Mapping):
            raise ValueError("OpenOCD process identity is unavailable.")
        fields = {"pid", "start_identity", "executable", "boot_identity"}
        if set(value) != fields or value.get("pid") != expected_pid:
            raise ValueError("OpenOCD process identity is invalid.")
        result = dict(value)
        for field in ("start_identity", "boot_identity"):
            if not isinstance(result[field], str) or not result[field]:
                raise ValueError("OpenOCD process identity is invalid.")
        result["executable"] = _canonical_executable(result["executable"])
        return result

    def _record_matches_lease(self, record: Mapping[str, object], lease: object) -> bool:
        try:
            return bool(
                record["lease_id"] == getattr(lease, "lease_id", None)
                and record["lease_generation"] == getattr(lease, "generation", None)
                and isinstance(record["lease_token_digest"], str)
                and isinstance(getattr(lease, "token_digest", None), str)
                and hmac.compare_digest(record["lease_token_digest"], getattr(lease, "token_digest"))
                and record["gateway_instance_id"] == getattr(lease, "gateway_instance_id", None)
                and record["gateway_generation"] == getattr(lease, "gateway_generation", None)
            )
        except (TypeError, ValueError):
            return False

    def _record_matches_live_process(self, record: Mapping[str, object]) -> bool:
        try:
            identity = self._validated_identity(self._process_identity(record["pid"]), record["pid"])
            return all(identity[field] == record[field] for field in (
                "start_identity", "executable", "boot_identity",
            ))
        except (TypeError, ValueError):
            return False

    def _record_matches_config(self, record: Mapping[str, object]) -> bool:
        return bool(
            record["gdb_endpoint"] == "127.0.0.1:%d" % self._gdb_port
            and record["tcl_endpoint"] == "127.0.0.1:%d" % self._tcl_port
        )

    def _reconcile_owner_worker(self, record: Mapping[str, object], failures: list,
                                deadline: float) -> bool:
        if time.monotonic() >= deadline or not self._record_matches_live_process(record):
            return False
        try:
            if self._endpoint_owner_pid(record["tcl_endpoint"]) != record["pid"]:
                return False
        except Exception:
            return False
        if time.monotonic() >= deadline:
            return False
        try:
            self._shutdown_openocd(record["tcl_endpoint"])
        except Exception as error:
            failures.append(error)
            return False
        # A late shutdown may still complete after the caller gave up; it must
        # never clear recovery evidence after that deadline.
        if time.monotonic() >= deadline or self._record_matches_live_process(record):
            return False
        try:
            closed = self._endpoints_closed(record["gdb_endpoint"], record["tcl_endpoint"])
        except Exception:
            return False
        if time.monotonic() >= deadline or not closed:
            return False
        self._owner_store.clear()
        return True

    @staticmethod
    def _safe_shutdown_openocd(endpoint: str) -> None:
        host, separator, port_text = str(endpoint).rpartition(":")
        if host != "127.0.0.1" or not separator:
            raise ValueError("Gateway recovery endpoint is invalid.")
        SafeTclClient(TclEndpoint(host, int(port_text)), timeout_seconds=1.0).shutdown()

    def _on_openocd_line(self, line: str) -> None:
        lowered = str(line).lower()
        with self._lock:
            activity_changed = False
            if "accepting 'gdb' connection" in lowered:
                self._gdb_connection_count += 1
                self._gdb_activity_generation += 1
                self._gdb_ever_attached = True
                activity_changed = True
            elif "dropped 'gdb' connection" in lowered and self._gdb_connection_count > 0:
                self._gdb_connection_count -= 1
                self._gdb_activity_generation += 1
                activity_changed = True
            if activity_changed:
                current = self._snapshot
                self._publish(
                    current.state, current.reason_code,
                    cpu_state=current.cpu_state if current.state == "READY" else "unknown",
                )
        guard = self._remote_guard
        if guard is not None:
            guard.handle_openocd_line(line)
        if any(marker in lowered for marker in (
                "libusb", "target not examined", "swd fault")):
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
            "gdb_connection_count": self._gdb_connection_count,
            "gdb_activity_generation": self._gdb_activity_generation,
            "gdb_ever_attached": self._gdb_ever_attached,
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
