"""Production ownership boundary for the v0.18 zero-halt Monitor page."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from b300_core.live_monitor import LiveSample
from b300_core.live_session import (
    ClientLiveMonitorConfig, LiveMonitorSession, LocalLiveMonitorConfig,
)
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
    ) -> "LiveMonitorRequest":
        selected = Path(symbols).expanduser().resolve() if symbols is not None else None
        roots = tuple(Path(root).expanduser().resolve() for root in symbol_roots)
        return cls("CLIENT", selected, host.strip(), user.strip(), int(ssh_port), roots)


class LiveMonitorController(QObject):
    """Own the production Live Monitor panel and its complete lifecycle."""

    operation_state_changed = Signal(bool)
    log = Signal(str)

    def __init__(
        self,
        panel: DebugLivePanel,
        parent: Optional[QObject] = None,
        *,
        selected_probe: Optional[Callable[[], ProbeRef]] = None,
        openocd_executable: Optional[str] = None,
        session_factory=LiveMonitorSession,
        worker_factory=FunctionWorker,
    ) -> None:
        super().__init__(parent)
        self.panel = panel
        self._selected_probe = selected_probe
        self._openocd_executable = openocd_executable
        self._session_factory = session_factory
        self._worker_factory = worker_factory
        self._active = False
        self._live_session = None
        self._worker = None

    @property
    def active(self) -> bool:
        return self._active

    def start(self, request: LiveMonitorRequest) -> None:
        if self._active or self._worker is not None:
            raise RuntimeError("Live Monitor is already active.")
        if request.role not in {"LOCAL", "CLIENT"}:
            raise ValueError("Live Monitor request role must be LOCAL or CLIENT.")

        watch_specs = self.panel.watch_specs()
        common = {
            "interval_seconds": float(self.panel.interval.value()),
            "sample_limit": self.panel.sample_limit(),
            "watch_specs": tuple(watch_specs),
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
        live = self._session_factory(openocd_executable=self._openocd_executable)
        self._live_session = live
        self.panel.reset_for_sampling()
        self.panel.set_control_state(
            start_enabled=False, stop_enabled=True, history_enabled=False,
        )
        self._active = True
        self.operation_state_changed.emit(True)

        def execute(log, phase, _cancel_event):
            try:
                info = (
                    live.start_local(config) if request.role == "LOCAL"
                    else live.start_client(config)
                )
                log(
                    "LIVE MONITOR CONNECTED: role=%s transport=%s target=%s" %
                    (info.role, info.transport, info.initial_target_state.upper())
                )
                summary = live.run(phase)
                return summary, live.analytics_snapshot(), info
            finally:
                live.close()

        worker = self._worker_factory(execute, self)
        self._worker = worker
        worker.log.connect(self.log.emit)
        worker.phase.connect(self._sample_received)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        try:
            worker.start()
        except BaseException:
            live.close()
            worker.deleteLater()
            self._worker = None
            self._finish_operation(history_enabled=False)
            raise

    def _sample_received(self, sample) -> None:
        if not isinstance(sample, LiveSample) and not hasattr(sample, "cycle"):
            return
        self.panel.append_live_sample(sample)
        live = self._live_session
        if live is not None:
            try:
                self.panel.apply_analytics(live.analytics_snapshot())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass

    def _completed(self, result) -> None:
        summary, analytics, info = result
        try:
            self.panel.apply_analytics(analytics)
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            self.log.emit("Live Monitor analytics view unavailable: %s" % error)
        self.panel.mark_live_completed(summary)
        self.log.emit(
            "Live Monitor completed: role=%s samples=%d target=%s" %
            (info.role, summary.samples, summary.final_target_state.upper())
        )
        self._finish_operation(history_enabled=True)

    def _failed(self, failure) -> None:
        message = getattr(failure, "message", str(failure))
        self.panel.mark_failed(message)
        self.log.emit("Live Monitor failed: %s" % message)
        self._finish_operation(history_enabled=False)

    def _finish_operation(self, *, history_enabled: bool) -> None:
        was_active = self._active
        self._active = False
        self._live_session = None
        self.panel.set_control_state(
            start_enabled=True, stop_enabled=False,
            history_enabled=history_enabled,
        )
        if was_active:
            self.operation_state_changed.emit(False)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        if self._active:
            self._finish_operation(history_enabled=False)

    def stop(self) -> None:
        """Request bounded cooperative shutdown without changing target state."""
        if not self._active:
            return
        self.panel.mark_stopping()
        if self._live_session is not None:
            self._live_session.cancel()
        if self._worker is not None:
            self._worker.cancel()

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
