"""High-level lifecycle facade for non-halting B300 Live Monitor consumers."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from .elf_matcher import discover_symbol_files, find_matching_symbol_file
from .live_analytics import LiveAnalyticsSnapshot, LiveMonitorStore, LiveSeriesPoint, LiveExecutionTransition
from .live_monitor import (
    LiveSample, LiveSummary, run_live_monitor, validate_live_request, validate_live_watch_specs,
)
from .live_service import LiveMonitorService
from .models import ProbeRef
from .offline_symbols import OfflineSymbolTable
from .ssh_debug_tunnel import find_available_loopback_port
from .ssh_host_trust import trusted_known_hosts_file
from .ssh_identity import managed_identity_file
from .ssh_live_tunnel import SshLiveTunnel, SshLiveTunnelConfig
from .tcl_client import SafeTclClient, TclEndpoint


@dataclass(frozen=True)
class LocalLiveMonitorConfig:
    probe: ProbeRef
    symbols: Path
    interval_seconds: float = 0.5
    sample_limit: Optional[int] = None
    watch_specs: Tuple[str, ...] = ()
    tcl_port: int = 6666

    def validate(self) -> None:
        _validate_symbol_file(self.symbols)
        _validate_monitor_request(self.interval_seconds, self.sample_limit, self.watch_specs)
        if not 1 <= int(self.tcl_port) <= 65535:
            raise ValueError("Live Monitor TCL port must be in range 1..65535.")


@dataclass(frozen=True)
class ClientLiveMonitorConfig:
    host: str
    user: str
    symbols: Optional[Path] = None
    interval_seconds: float = 0.5
    sample_limit: Optional[int] = None
    watch_specs: Tuple[str, ...] = ()
    ssh_port: int = 22
    preferred_local_tcl_port: int = 16666
    gateway_tcl_port: int = 6666
    symbol_roots: Tuple[Path, ...] = ()
    symbol_max_files: int = 128

    def validate(self) -> None:
        if self.symbols is not None:
            _validate_symbol_file(self.symbols)
        elif not self.symbol_roots:
            raise ValueError("Client Live Monitor requires an ELF/AXF file or at least one symbol root.")
        for root in self.symbol_roots:
            if not Path(root).expanduser().resolve().is_dir():
                raise ValueError("Live Monitor symbol root does not exist or is not a directory: %s" % root)
        if not 1 <= int(self.symbol_max_files) <= 512:
            raise ValueError("Live Monitor symbol_max_files must be in range 1..512.")
        _validate_monitor_request(self.interval_seconds, self.sample_limit, self.watch_specs)
        SshLiveTunnelConfig(
            host=self.host, user=self.user, ssh_port=self.ssh_port,
            local_tcl_port=self.preferred_local_tcl_port, gateway_tcl_port=self.gateway_tcl_port,
        ).validate()


@dataclass(frozen=True)
class LiveMonitorSessionInfo:
    role: str
    transport: str
    tcl_endpoint: str
    symbols: str
    initial_target_state: str
    zero_halt: bool = True


def _validate_symbol_file(path: Path) -> Path:
    selected = Path(path).expanduser().resolve()
    if selected.suffix.lower() not in {".elf", ".axf"} or not selected.is_file():
        raise ValueError("Live Monitor symbols must reference an existing ELF/AXF file.")
    return selected


def _validate_monitor_request(interval_seconds: float, sample_limit: Optional[int],
                              watch_specs: Tuple[str, ...]) -> None:
    validate_live_watch_specs(watch_specs)
    validate_live_request(interval_seconds, sample_limit, ())


class LiveMonitorSession:
    """Own Live Monitor transports/symbols and expose cooperative cancellation.

    The normal GUI pattern is: create/start/run in a worker thread, call ``cancel``
    from the UI thread, then ``close`` in the worker's ``finally`` block. ``cancel``
    never halts or resumes the STM32 target.
    """

    def __init__(self, *, openocd_executable: Optional[str] = None,
                 service_factory=LiveMonitorService, tunnel_factory=SshLiveTunnel,
                 tcl_factory=SafeTclClient, symbol_table_factory=OfflineSymbolTable,
                 port_allocator=find_available_loopback_port, history_capacity: int = 5000) -> None:
        self._openocd_executable = openocd_executable
        self._service_factory = service_factory
        self._tunnel_factory = tunnel_factory
        self._tcl_factory = tcl_factory
        self._symbol_table_factory = symbol_table_factory
        self._port_allocator = port_allocator
        self._store = LiveMonitorStore(history_capacity)
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._active = False
        self._running = False
        self._role: Optional[str] = None
        self._config = None
        self._service = None
        self._tunnel = None
        self._tcl = None
        self._symbols = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def analytics_snapshot(self, top_functions: int = 20) -> LiveAnalyticsSnapshot:
        return self._store.snapshot(top_functions=top_functions)

    def history(self, limit: Optional[int] = None) -> Tuple[LiveSample, ...]:
        return self._store.samples(limit)

    def execution_transitions(self, limit: Optional[int] = None) -> Tuple[LiveExecutionTransition, ...]:
        return self._store.transitions(limit)

    def variable_series(self, name: str, limit: Optional[int] = None) -> Tuple[LiveSeriesPoint, ...]:
        return self._store.variable_series(name, limit)

    def target_state(self) -> str:
        """Read the current target state without changing RUN/HALT state."""
        with self._lock:
            if not self._active or self._tcl is None:
                raise RuntimeError("Live Monitor session is not started.")
            tcl = self._tcl
        return tcl.wait_target_state()

    def start_local(self, config: LocalLiveMonitorConfig) -> LiveMonitorSessionInfo:
        config.validate()
        self._require_inactive()
        selected_symbols = _validate_symbol_file(config.symbols)
        service = self._service_factory(executable=self._openocd_executable)
        try:
            service.start(config.probe, config.tcl_port)
            tcl = self._tcl_factory(TclEndpoint("127.0.0.1", config.tcl_port))
            matched = _require_matching_symbols(selected_symbols, tcl)
            state = tcl.wait_target_state()
            if state != "running":
                raise RuntimeError(
                    "Realtime Live Monitor requires a RUNNING target; current state is %s." % state
                )
            symbols = self._symbol_table_factory(matched)
        except BaseException:
            service.stop()
            raise
        with self._lock:
            self._store.clear()
            self._cancel.clear()
            self._role = "local"
            self._config = config
            self._service = service
            self._tcl = tcl
            self._symbols = symbols
            self._active = True
        return LiveMonitorSessionInfo(
            role="local", transport="swd-tcl-loopback",
            tcl_endpoint="127.0.0.1:%d" % config.tcl_port,
            symbols=str(matched), initial_target_state=state,
        )

    def start_client(self, config: ClientLiveMonitorConfig) -> LiveMonitorSessionInfo:
        config.validate()
        self._require_inactive()
        selected_symbols = (_validate_symbol_file(config.symbols)
                            if config.symbols is not None else None)
        local_tcl = self._port_allocator(config.preferred_local_tcl_port)
        tunnel_config = SshLiveTunnelConfig(
            host=config.host, user=config.user, ssh_port=config.ssh_port,
            local_tcl_port=local_tcl, gateway_tcl_port=config.gateway_tcl_port,
            identity_file=managed_identity_file(),
            known_hosts_file=trusted_known_hosts_file(config.host, config.ssh_port),
        )
        tunnel = self._tunnel_factory(tunnel_config)
        try:
            tunnel.start()
            tcl = self._tcl_factory(TclEndpoint("127.0.0.1", local_tcl))
            matched = _resolve_client_symbols(config, selected_symbols, tcl)
            state = tcl.wait_target_state()
            if state != "running":
                raise RuntimeError(
                    "Realtime Live Monitor requires a RUNNING target; current state is %s." % state
                )
            symbols = self._symbol_table_factory(matched)
        except BaseException:
            tunnel.stop()
            raise
        with self._lock:
            self._store.clear()
            self._cancel.clear()
            self._role = "client"
            self._config = config
            self._tunnel = tunnel
            self._tcl = tcl
            self._symbols = symbols
            self._active = True
        return LiveMonitorSessionInfo(
            role="client", transport="ssh-tcl-local-forwarding",
            tcl_endpoint="127.0.0.1:%d" % local_tcl,
            symbols=str(matched), initial_target_state=state,
        )

    def run(self, on_sample: Optional[Callable[[LiveSample], None]] = None) -> LiveSummary:
        with self._lock:
            if not self._active or self._tcl is None or self._symbols is None or self._config is None:
                raise RuntimeError("Live Monitor session is not started.")
            if self._running:
                raise RuntimeError("Live Monitor session is already running.")
            self._running = True
            config = self._config
            tcl = self._tcl
            symbols = self._symbols
        def accept(sample: LiveSample) -> None:
            self._store.append(sample)
            if on_sample is not None:
                on_sample(sample)
        try:
            return run_live_monitor(
                tcl, symbols, interval_seconds=config.interval_seconds,
                sample_limit=config.sample_limit, watch_specs=config.watch_specs,
                cancelled=self._cancel.is_set, wait=self._cancel.wait, on_sample=accept,
            )
        finally:
            with self._lock:
                self._running = False

    def cancel(self) -> None:
        """Cooperatively stop sampling without touching target RUN/HALT state."""
        self._cancel.set()

    def close(self) -> None:
        """Release symbols/transports. Prefer calling after ``run`` has returned."""
        self.cancel()
        with self._lock:
            symbols, tunnel, service = self._symbols, self._tunnel, self._service
            self._symbols = None
            self._tcl = None
            self._tunnel = None
            self._service = None
            self._config = None
            self._role = None
            self._active = False
        if symbols is not None:
            symbols.close()
        if tunnel is not None:
            tunnel.stop()
        if service is not None:
            service.stop()

    def _require_inactive(self) -> None:
        with self._lock:
            if self._active or self._service is not None or self._tunnel is not None:
                raise RuntimeError("Live Monitor session is already active.")

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()


def _resolve_client_symbols(config: ClientLiveMonitorConfig, exact: Optional[Path],
                            tcl: SafeTclClient) -> Path:
    if exact is not None:
        return _require_matching_symbols(exact, tcl)
    roots = tuple(Path(root).expanduser().resolve() for root in config.symbol_roots)
    candidates = discover_symbol_files(roots, max_files=config.symbol_max_files, max_depth=8)
    selected, results = find_matching_symbol_file(candidates, tcl.read_words)
    if selected is None:
        exact_count = sum(1 for item in results if item.matched)
        if exact_count > 1:
            raise RuntimeError("Multiple AXF/ELF files match target firmware; select one explicitly.")
        raise RuntimeError("No AXF/ELF under the configured symbol roots matches target firmware.")
    return selected.path


def _require_matching_symbols(path: Path, tcl: SafeTclClient) -> Path:
    selected, results = find_matching_symbol_file((path,), tcl.read_words)
    if selected is None:
        detail = results[0].reason if results else "ELF/AXF could not be sampled"
        raise RuntimeError("Live Monitor AXF/ELF does not match target firmware: %s" % detail)
    return selected.path
