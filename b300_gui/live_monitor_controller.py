"""Production ownership boundary for the v0.18 zero-halt Monitor page."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional
from types import SimpleNamespace
import time
import uuid
import hashlib
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from b300_core.live_monitor import LiveSample
from b300_core.live_session import (
    ClientLiveMonitorConfig, LiveMonitorSession, LocalLiveMonitorConfig,
)
from b300_core.gateway_client import GatewayClientCoordinator
from b300_core.gateway_lease_client import GatewayLeaseClient
from b300_core.models import ProbeRef
from .debug_live_panel import DebugLivePanel
from .workers import FunctionWorker


@dataclass(frozen=True)
class LiveMonitorRequest:
    """Operator-selected Live Monitor endpoint without raw transport controls."""

    role: str
    symbols: Optional[Path]
    host: str = ""
    user: str = ""
    ssh_port: int = 22
    symbol_roots: tuple[Path, ...] = ()
    profile_id: str = ""

    @classmethod
    def local(cls, symbols: Path) -> "LiveMonitorRequest":
        return cls("LOCAL", Path(symbols).expanduser().resolve())

    @classmethod
    def client(
        cls,
        symbols: Optional[Path],
        *,
        host: str,
        user: str,
        ssh_port: int = 22,
        symbol_roots: tuple[Path, ...] = (),
        profile_id: str = "",
    ) -> "LiveMonitorRequest":
        selected = Path(symbols).expanduser().resolve() if symbols is not None else None
        roots = tuple(Path(root).expanduser().resolve() for root in symbol_roots)
        return cls("CLIENT", selected, host.strip(), user.strip(), int(ssh_port), roots, str(profile_id).strip())


class LiveMonitorController(QObject):
    """Own the production Live Monitor panel and its complete lifecycle."""

    operation_state_changed = Signal(bool)
    log = Signal(str)
    RENDER_INTERVAL_MS = 250

    def __init__(
        self,
        panel: DebugLivePanel,
        parent: Optional[QObject] = None,
        *,
        selected_probe: Optional[Callable[[], ProbeRef]] = None,
        openocd_executable: Optional[str] = None,
        remote_session_provider=None,
        hardware_busy: Optional[Callable[[], bool]] = None,
        session_factory=LiveMonitorSession,
        worker_factory=FunctionWorker,
        recovery_worker_factory=FunctionWorker,
        coordinator_factory=GatewayClientCoordinator,
        lease_client_factory=GatewayLeaseClient,
        context=None,
        ui_dispatcher=None,
    ) -> None:
        super().__init__(parent)
        self.panel = panel
        self._selected_probe = selected_probe
        self._openocd_executable = openocd_executable
        self._remote_session_provider = remote_session_provider
        self._hardware_busy = hardware_busy
        self._session_factory = session_factory
        self._worker_factory = worker_factory
        self._recovery_worker_factory = recovery_worker_factory
        self._coordinator_factory = coordinator_factory
        self._lease_client_factory = lease_client_factory
        self._gateway_lease_client = None
        self._gateway_coordinator = None
        self._gateway_binding = None
        self._epoch = 0
        self._last_request = None
        self._last_symbols_revision = None
        self._last_symbols_digest = None
        self._active = False
        self._stopping = False
        self._live_session = None
        self._worker = None
        self._invalidated_reason = ""
        self._stopping = False
        self._pending_samples = []
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._flush_pending_samples)
        self._context = context
        self._ui_dispatcher = ui_dispatcher
        self._lease_token = None
        self._recovery_worker = None
        self._recovery_token = 0
        self._recovery_dirty = False
        self._startup_lock = threading.RLock()
        self._starting = False
        self._startup_lease_lost = False

    def set_context(self, context) -> None:
        self._context = context

    def set_ui_dispatcher(self, dispatcher) -> None:
        self._ui_dispatcher = dispatcher

    def _publish_monitor(self, *, live: bool = False) -> None:
        apply = getattr(self._context, "apply_device_state", None)
        if callable(apply):
            if self._lease_token is None:
                self._lease_token = uuid.uuid4().hex
            updates = {"owner_kind": "MONITORING", "lease_token": self._lease_token,
                       "reason": "Live Monitor active"}
            if live:
                updates["target_state"] = "running"
            apply(**updates)

    def _release_monitor(self, reason: str, *, stale: bool = True) -> None:
        apply = getattr(self._context, "apply_device_state", None)
        if callable(apply):
            updates = {"owner_kind": None, "reason": reason}
            if self._lease_token is not None:
                updates["lease_token"] = self._lease_token
            if stale:
                updates.update(target_state=None, gdb_endpoint=None, tcl_endpoint=None)
            apply(**updates)
        self._lease_token = None

    def _on_gateway_lease_lost(self, epoch=None) -> None:
        with self._startup_lock:
            if epoch is not None and epoch != self._epoch:
                return
            if self._starting:
                self._startup_lease_lost = True
        def teardown():
            if epoch is not None and epoch != self._epoch:
                return
            try:
                if self._starting:
                    self._rollback_failed_startup()
                else:
                    self.stop()
            except Exception:
                self._release_monitor("Gateway lease lost")
        dispatcher = getattr(self, "_ui_dispatcher", None)
        if dispatcher is None:
            teardown()
        else:
            dispatcher.submit(teardown)

    @property
    def active(self) -> bool:
        return self._active

    def _rollback_failed_startup(self) -> None:
        """Release resources created before a Monitor start becomes active."""
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass
        live, self._live_session = self._live_session, None
        if live is not None:
            try:
                live.close()
            except Exception:
                pass
        coordinator, self._gateway_coordinator = self._gateway_coordinator, None
        self._gateway_binding = None
        if coordinator is not None:
            try:
                closer = getattr(coordinator, "close", None)
                if callable(closer):
                    closer()
            except Exception:
                pass
        lease_client, self._gateway_lease_client = self._gateway_lease_client, None
        if lease_client is not None:
            try:
                lease_client.close()
            except Exception:
                pass
        was_active = self._active
        self._active = False
        self._stopping = False
        self._starting = False
        self._pending_samples.clear()
        self._render_timer.stop()
        try:
            self.panel.set_control_state(
                start_enabled=True, stop_enabled=False, history_enabled=False,
            )
        except Exception:
            pass
        if was_active:
            self._release_monitor("Live Monitor startup failed")
            self.operation_state_changed.emit(False)

    def _require_startup_lease(self) -> None:
        with self._startup_lock:
            if self._startup_lease_lost:
                raise RuntimeError("Gateway lease lost during Monitor startup.")

    def start(self, request: LiveMonitorRequest, *, _coordinator=None, _binding=None) -> None:
        if self._active or self._worker is not None:
            raise RuntimeError("Live Monitor is already active.")
        if self._hardware_busy is not None and self._hardware_busy():
            raise RuntimeError("Hardware is busy; stop the current operation before starting Monitor.")
        if request.role not in {"LOCAL", "CLIENT"}:
            raise ValueError("Live Monitor request role must be LOCAL or CLIENT.")

        self._invalidated_reason = ""
        self._epoch += 1
        epoch = self._epoch
        with self._startup_lock:
            self._starting = False
            self._startup_lease_lost = False
        self._last_request = request
        if request.symbols is not None:
            info = Path(request.symbols).stat()
            self._last_symbols_revision = (int(info.st_size), int(info.st_mtime_ns))
            self._last_symbols_digest = self._sha256(request.symbols)
        self._pending_samples.clear()
        self._render_timer.stop()
        watch_specs = self.panel.watch_specs()
        compiled_watches = (
            tuple(self.panel.compiled_watches())
            if hasattr(self.panel, "compiled_watches") else ()
        )
        watch_policies = (
            tuple(self.panel.watch_policies())
            if hasattr(self.panel, "watch_policies") else ()
        )
        common = {
            "interval_seconds": float(self.panel.interval.value()),
            "sample_limit": self.panel.sample_limit(),
            "watch_specs": tuple(watch_specs),
            "compiled_watches": compiled_watches,
            "watch_policies": watch_policies,
        }
        if request.role == "LOCAL":
            if self._selected_probe is None:
                raise RuntimeError("Live Monitor has no ST-Link probe selector.")
            config = LocalLiveMonitorConfig(
                probe=self._selected_probe(), symbols=request.symbols,
                tcl_port=6666, **common,
            )
        else:
            config = ClientLiveMonitorConfig(
                host=request.host, user=request.user, symbols=request.symbols,
                ssh_port=request.ssh_port, preferred_local_tcl_port=16666,
                gateway_tcl_port=6666, symbol_roots=request.symbol_roots,
                show_console=False, **common,
            )
        config.validate()
        remote_session = None
        coordinator = _coordinator
        if request.role == "CLIENT" and self._remote_session_provider is not None:
            remote_session = self._remote_session_provider(request)
            if remote_session is None:
                raise RuntimeError("Client Live Monitor requires an authenticated session.")
            if (coordinator is None and self._gateway_lease_client is None
                    and getattr(remote_session, "supports_gateway_leases", False) is True
                    and callable(getattr(remote_session, "ensure_gateway_agent", None))
                    and callable(getattr(remote_session, "acquire_gateway", None))):
                on_lost = lambda value=epoch: self._on_gateway_lease_lost(value)
                try:
                    lease_client = self._lease_client_factory(
                        remote_session, client_id=request.profile_id or request.host,
                        client_label=request.user or request.host,
                        on_lost=on_lost,
                    )
                except TypeError:
                    lease_client = self._lease_client_factory(
                        remote_session, client_id=request.profile_id or request.host,
                        client_label=request.user or request.host,
                    )
                with self._startup_lock:
                    self._starting = True
                    self._startup_lease_lost = False
                self._gateway_lease_client = lease_client
                try:
                    lease_client.start("LIVE_WATCH", probe_serial=None)
                    grant = lease_client.grant
                    if grant is None or not grant.public.get("tcl_endpoint"):
                        raise RuntimeError("Gateway lease did not provide a valid TCL endpoint.")
                    config = replace(config, bound_tcl_endpoint=grant.public["tcl_endpoint"])
                except BaseException:
                    self._rollback_failed_startup()
                    raise
            if coordinator is not None:
                if _binding is None:
                    raise RuntimeError("Gateway restart requires a fresh binding.")
                config = replace(config, bound_tcl_endpoint=_binding.tcl_endpoint)
            elif (self._gateway_lease_client is None
                  and callable(getattr(remote_session, "gateway_status", None))):
                coordinator = self._coordinator_factory(
                    remote_session, request.profile_id or request.host,
                )
                coordinated = coordinator.ensure_ready()
                if getattr(coordinated, "state", None) != "READY" or getattr(coordinated, "binding", None) is None:
                    raise RuntimeError("Gateway %s; %s" % (
                        getattr(coordinated, "reason_code", "GATEWAY_NOT_READY"),
                        getattr(coordinated, "next_action", "retry when it is ready."),
                    ))
                config = replace(config, bound_tcl_endpoint=coordinated.binding.tcl_endpoint)
        try:
            live = self._session_factory(openocd_executable=self._openocd_executable)
            self._live_session = live
            self._require_startup_lease()
            self._gateway_coordinator = coordinator
            self._gateway_binding = (_binding if _binding is not None else coordinated.binding) if coordinator is not None else (
                self._gateway_lease_client.grant.public if self._gateway_lease_client and self._gateway_lease_client.grant else None)
            self.panel.reset_for_sampling()
            self.panel.set_control_state(
                start_enabled=False, stop_enabled=True, history_enabled=False,
            )
            self._require_startup_lease()
            self._active = True
            self._publish_monitor()
            self.operation_state_changed.emit(True)

            def execute(log, phase, cancel_event):
                try:
                    selected_config = config
                    if request.role == "CLIENT" and remote_session is not None:
                        if coordinator is not None or self._gateway_lease_client is not None:
                            selected_config = config
                        else:
                            ensure_ready = getattr(remote_session, "ensure_gateway_ready", None)
                            if not callable(ensure_ready):
                                raise RuntimeError("Gateway lease binding is missing.")
                            snapshot = ensure_ready()
                            endpoint = getattr(snapshot, "tcl_endpoint", None)
                            if not getattr(snapshot, "attach_ready", False) or not endpoint:
                                raise RuntimeError("Gateway did not provide a READY TCL endpoint.")
                            selected_config = replace(config, gateway_tcl_port=int(str(endpoint).rpartition(":")[2]))
                    if request.role == "LOCAL":
                        info = live.start_local(selected_config)
                    elif remote_session is not None:
                        info = live.start_client(selected_config, remote_session=remote_session)
                    else:
                        info = live.start_client(selected_config)
                    log(
                        "LIVE MONITOR CONNECTED: role=%s transport=%s target=%s" %
                        (info.role, info.transport, info.initial_target_state.upper())
                    )
                    # Session startup resets its own event; retain Stop requested
                    # while connecting via the worker's durable cancellation event.
                    if cancel_event.is_set():
                        live.cancel()
                    def emit_sample(sample):
                        phase((sample, epoch, self._gateway_binding))
                    summary = live.run(emit_sample)
                    return summary, live.analytics_snapshot(), info
                finally:
                    live.close()

            worker = self._worker_factory(execute, self)
            self._worker = worker
            worker.log.connect(self.log.emit)
            worker.phase.connect(self._sample_received)
            worker.completed.connect(lambda result, value=epoch: self._completed_for_epoch(value, result))
            worker.failed.connect(lambda failure, value=epoch: self._failed_for_epoch(value, failure))
            worker.finished.connect(lambda value=epoch: self._worker_finished_for_epoch(value))
            with self._startup_lock:
                self._require_startup_lease()
                worker.start()
                self._starting = False
        except BaseException:
            self._rollback_failed_startup()
            raise

    def _sample_received(self, sample) -> None:
        if self._invalidated_reason:
            return
        binding = None
        if isinstance(sample, tuple) and len(sample) == 3:
            sample, epoch, binding = sample
            if epoch != self._epoch:
                return
        elif isinstance(sample, tuple) and len(sample) == 2:
            sample, binding = sample
        coordinator = self._gateway_coordinator
        if coordinator is not None and not getattr(coordinator, "accept_sample", lambda _binding: False)(binding):
            return
        if not isinstance(sample, LiveSample) and not hasattr(sample, "cycle"):
            return
        self._publish_monitor(live=True)
        append_many = getattr(self.panel, "append_live_samples", None)
        if callable(append_many):
            self._pending_samples.append(sample)
            if not self._render_timer.isActive():
                self._render_timer.start(self.RENDER_INTERVAL_MS)
            return
        self.panel.append_live_sample(sample)
        self._apply_live_analytics()

    def _apply_live_analytics(self) -> None:
        live = self._live_session
        if live is not None:
            try:
                self.panel.apply_analytics(live.analytics_snapshot())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass

    def _flush_pending_samples(self) -> None:
        """Render queued worker samples in one bounded GUI update."""
        self._render_timer.stop()
        pending = tuple(self._pending_samples)
        self._pending_samples.clear()
        if not pending or self._invalidated_reason:
            return
        append_many = getattr(self.panel, "append_live_samples", None)
        if callable(append_many):
            started = time.monotonic()
            append_many(pending)
            rendered = getattr(self.panel, "set_render_duration", None)
            if callable(rendered):
                rendered(time.monotonic() - started)
        else:
            for sample in pending:
                self.panel.append_live_sample(sample)
        self._apply_live_analytics()

    def _completed(self, result) -> None:
        summary, analytics, info = result
        self._flush_pending_samples()
        if self._invalidated_reason:
            self.log.emit("Live Monitor invalidated: %s" % self._invalidated_reason)
            self._finish_operation(history_enabled=True)
            return
        try:
            self.panel.apply_analytics(analytics)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            self.log.emit("Live Monitor analytics view unavailable: %s" % error)
        self.panel.mark_live_completed(summary)
        scheduler_metrics = getattr(self.panel, "set_scheduler_metrics", None)
        if callable(scheduler_metrics):
            scheduler_metrics(summary)
        self.log.emit(
            "Live Monitor completed: role=%s samples=%d target=%s" %
            (info.role, summary.samples, summary.final_target_state.upper())
        )
        self._finish_operation(history_enabled=True)

    def _completed_for_epoch(self, epoch, result) -> None:
        if epoch == self._epoch:
            self._completed(result)

    def _failed(self, failure) -> None:
        self._flush_pending_samples()
        message = getattr(failure, "message", str(failure))
        if self._invalidated_reason:
            self.log.emit("Live Monitor invalidated: %s" % self._invalidated_reason)
            self._finish_operation(history_enabled=True)
            return
        self.panel.mark_failed(message)
        self.log.emit("Live Monitor failed: %s" % message)
        self._finish_operation(history_enabled=False)

    def _failed_for_epoch(self, epoch, failure) -> None:
        if epoch == self._epoch:
            self._failed(failure)

    def _finish_operation(self, *, history_enabled: bool) -> None:
        was_active = self._active
        self._active = False
        self._stopping = False
        self._live_session = None
        coordinator, self._gateway_coordinator = self._gateway_coordinator, None
        self._gateway_binding = None
        if coordinator is not None:
            closer = getattr(coordinator, "close", None)
            if callable(closer):
                closer()
        lease_client, self._gateway_lease_client = self._gateway_lease_client, None
        if lease_client is not None:
            lease_client.close()
        self.panel.set_control_state(
            start_enabled=True, stop_enabled=False,
            history_enabled=history_enabled,
        )
        if was_active:
            self._release_monitor("Live Monitor stopped")
            self.operation_state_changed.emit(False)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        if self._active:
            self._finish_operation(history_enabled=False)

    def _worker_finished_for_epoch(self, epoch) -> None:
        if epoch == self._epoch:
            self._worker_finished()

    def stop(self) -> None:
        """Request bounded cooperative shutdown without changing target state."""
        if not self._active:
            return
        self._stopping = True
        self._recovery_dirty = False
        self._recovery_token += 1
        if self._recovery_worker is not None:
            self._recovery_worker.cancel()
        self.panel.mark_stopping()
        if self._live_session is not None:
            self._live_session.cancel()
        if self._worker is not None:
            self._worker.cancel()
        self._release_monitor("Live Monitor stop requested", stale=True)

    def invalidate(self, reason: str) -> None:
        """Fail closed on lost Gateway evidence and reject late worker samples."""
        selected = str(reason or "Gateway không còn cung cấp bằng chứng mới.").strip()
        self._flush_pending_samples()
        self._invalidated_reason = selected
        self._release_monitor(selected, stale=True)
        marker = getattr(self.panel, "mark_stale", None)
        if callable(marker):
            marker(selected)
        if self._active:
            if self._live_session is not None:
                self._live_session.cancel()
            if self._worker is not None:
                self._worker.cancel()

    def check_gateway_health(self):
        """Fail closed when the coordinator no longer owns the active binding."""
        coordinator = self._gateway_coordinator
        if coordinator is None or not self._active:
            return None
        state = coordinator.health()
        if getattr(state, "state", None) == "READY" and getattr(state, "binding", None) != self._gateway_binding:
            return self._restart_client_binding(coordinator, state)
        if getattr(state, "state", None) != "READY":
            self.invalidate("Gateway %s · %s" % (
                getattr(state, "reason_code", "GATEWAY_BINDING_CHANGED"),
                getattr(state, "next_action", "retry Monitor when it is ready."),
            ))
        return state

    def accept_gateway_health_snapshot(self, snapshot):
        """GUI-safe health input; it never performs an SSH status request."""
        coordinator = self._gateway_coordinator
        if coordinator is None or not self._active or self._stopping:
            return None
        accept = getattr(coordinator, "accept_health_snapshot", None)
        if not callable(accept):
            return None
        state = accept(snapshot)
        if getattr(state, "state", None) != "READY" or getattr(state, "binding", None) != self._gateway_binding:
            self._queue_gateway_recovery(coordinator)
        return state

    def _queue_gateway_recovery(self, coordinator):
        """Queue one SSH recovery; callers stay on the Qt GUI thread."""
        if self._recovery_worker is not None:
            # accept_health_snapshot already stored the newest evidence in the
            # coordinator. Coalesce another recovery after this I/O completes.
            self._recovery_dirty = True
            return
        self._recovery_dirty = False
        self._recovery_token += 1
        token, epoch = self._recovery_token, self._epoch
        def recover(_log, _phase, _cancel):
            return coordinator.health()
        worker = self._recovery_worker_factory(recover, self)
        self._recovery_worker = worker
        def completed(state):
            if (token != self._recovery_token or epoch != self._epoch or not self._active
                    or coordinator is not self._gateway_coordinator):
                return
            if getattr(state, "state", None) == "READY" and getattr(state, "binding", None) != self._gateway_binding:
                self._restart_client_binding(coordinator, state)
            elif getattr(state, "state", None) != "READY":
                self.invalidate("Gateway %s · %s" % (getattr(state, "reason_code", "GATEWAY_RECOVERY_EXHAUSTED"), getattr(state, "next_action", "retry Monitor when it is ready.")))
        def finished():
            if self._recovery_worker is worker:
                self._recovery_worker = None
            worker.deleteLater()
            if (self._recovery_dirty and self._active and not self._stopping
                    and coordinator is self._gateway_coordinator):
                self._queue_gateway_recovery(coordinator)
        worker.completed.connect(completed)
        worker.failed.connect(lambda failure: completed(SimpleNamespace(state="STALE", binding=None, reason_code="GATEWAY_RECOVERY_FAILED", next_action=str(failure))))
        worker.finished.connect(finished)
        worker.start()

    def _restart_client_binding(self, coordinator, state):
        """Replace a stopped Client worker only after a fresh coordinator rebind."""
        request = self._last_request
        worker = self._worker
        if request is None or request.role != "CLIENT" or worker is None:
            self.invalidate("Gateway GATEWAY_BINDING_CHANGED · restart Monitor manually.")
            return state
        if request.symbols is not None:
            info = Path(request.symbols).stat()
            if ((int(info.st_size), int(info.st_mtime_ns)) != self._last_symbols_revision
                    or self._sha256(request.symbols) != self._last_symbols_digest):
                self.invalidate("Gateway MONITOR_RESTART_AXF_CHANGED · nạp lại AXF/ELF rồi bắt đầu Monitor.")
                return state
        self._flush_pending_samples()
        if self._live_session is not None:
            self._live_session.cancel()
        worker.cancel()
        if worker.isRunning() and not worker.wait(3000):
            self.invalidate("Gateway MONITOR_RESTART_TIMEOUT · worker cũ chưa dừng; hãy thử lại.")
            return state
        worker.deleteLater()
        live = self._live_session
        if live is not None:
            live.close()
        self._worker = None
        self._live_session = None
        self._active = False
        self._gateway_coordinator = coordinator
        self._gateway_binding = state.binding
        try:
            self.start(request, _coordinator=coordinator, _binding=state.binding)
        except (OSError, RuntimeError, ValueError) as error:
            self._invalidated_reason = "Gateway MONITOR_RESTART_FAILED · %s" % error
            marker = getattr(self.panel, "mark_stale", None)
            if callable(marker):
                marker(self._invalidated_reason)
            self._worker = None
            self._live_session = None
            self._active = True
            self._finish_operation(history_enabled=True)
        return state

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def clear(self) -> None:
        if self._active:
            return
        self.panel.clear_history()
        self.panel.set_control_state(
            start_enabled=True, stop_enabled=False, history_enabled=False,
        )

    def export(self, parent=None) -> Optional[Path]:
        if self._active:
            raise RuntimeError("Stop Live Monitor before exporting samples.")
        saved = self.panel.export_samples(parent)
        if saved is not None:
            self.log.emit("Live sampling exported: %s" % Path(saved).name)
        return saved

    def prepare_shutdown(self) -> bool:
        """Cooperatively finish Monitor work before Qt destroys its children."""
        live = self._live_session
        worker = self._worker
        self.stop()
        if worker is not None and worker.isRunning() and not worker.wait(3000):
            return False
        if live is not None:
            live.close()
        if worker is not None:
            worker.deleteLater()
        self._worker = None
        if self._active:
            self._finish_operation(history_enabled=False)
        return True


__all__ = ["LiveMonitorController", "LiveMonitorRequest"]
