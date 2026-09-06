"""Continuously surface authenticated Gateway health to the production GUI."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from b300_core.gateway_protocol import GATEWAY_STATUS_COMMAND
from b300_core.gateway_status import GatewaySnapshot, GatewaySnapshotTracker
from b300_core.remote_profile import RemoteGatewayProfile

from .workers import FunctionWorker


_REASON_MESSAGES = {
    "PROBE_REMOVED": "Mất kết nối ST-Link trên Gateway.",
    "NO_PROBE": "Gateway chưa phát hiện ST-Link.",
    "WAITING_SELECTION": "Gateway cần chọn đúng ST-Link trước khi gỡ lỗi.",
    "TARGET_UNAVAILABLE": "Gateway đã mất kết nối với MCU đích.",
    "OPENOCD_EXITED": "OpenOCD trên Gateway đã dừng.",
    "SERVICE_FAILED": "Dịch vụ gỡ lỗi trên Gateway bị lỗi.",
    "STOPPED": "Gateway chưa chạy.",
}


class GatewayHealthController(QObject):
    """Poll Gateway state off the GUI thread and report state transitions once."""

    snapshot_changed = Signal(object)
    warning_changed = Signal(str)
    recovered = Signal(object)

    def __init__(self, sessions, parent=None, *, worker_factory=FunctionWorker,
                 interval_ms: int = 1000, failure_threshold: int = 3) -> None:
        super().__init__(parent)
        if interval_ms < 250:
            raise ValueError("Gateway health interval must be at least 250 ms.")
        if failure_threshold < 1:
            raise ValueError("Gateway health failure threshold must be positive.")
        self._sessions = sessions
        self._worker_factory = worker_factory
        self._profile: Optional[RemoteGatewayProfile] = None
        self._tracker = GatewaySnapshotTracker(freshness_timeout_seconds=5.0)
        self._worker = None
        self._failures = 0
        self._transport_stale = False
        self._warning = ""
        self._had_snapshot = False
        self._timer = QTimer(self)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self.poll_now)
        self._failure_threshold = int(failure_threshold)

    @property
    def snapshot(self) -> Optional[GatewaySnapshot]:
        return self._tracker.snapshot

    @property
    def attach_ready(self) -> bool:
        return not self._transport_stale and self._tracker.attach_ready

    @property
    def warning(self) -> str:
        return self._warning

    def bind(self, profile: Optional[RemoteGatewayProfile]) -> None:
        selected = profile.validate() if profile is not None else None
        if selected == self._profile:
            return
        self.stop()
        self._profile = selected
        self._tracker = GatewaySnapshotTracker(freshness_timeout_seconds=5.0)
        self._failures = 0
        self._transport_stale = False
        self._had_snapshot = False
        self._set_warning("")

    def start(self) -> None:
        if self._profile is None:
            return
        if not self._timer.isActive():
            self._timer.start()
        self.poll_now()

    def stop(self) -> None:
        self._timer.stop()
        worker = self._worker
        self._worker = None
        if worker is not None and worker.isRunning():
            worker.cancel()

    def poll_now(self) -> None:
        if self._profile is None or self._worker_factory is None or self._worker is not None:
            return
        profile = self._profile

        def operation(_log, _phase, _cancel):
            session = self._sessions.session(profile)
            status = getattr(session, "gateway_status", None)
            if callable(status):
                return status(timeout_seconds=5.0)
            # Compatibility with the first protocol implementation.
            return session._run_gateway_cli(GATEWAY_STATUS_COMMAND, timeout_seconds=5.0)

        worker = self._worker_factory(operation, self)
        self._worker = worker
        worker.completed.connect(self._poll_completed)
        worker.failed.connect(lambda failure: self.accept_failure(failure.message))
        worker.finished.connect(self._poll_finished)
        worker.start()

    def _poll_completed(self, value) -> None:
        if isinstance(value, GatewaySnapshot):
            self.accept_snapshot(value)
        else:
            self.accept_failure("Gateway trả về trạng thái không hợp lệ.")

    def _poll_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

    def accept_snapshot(self, value: GatewaySnapshot) -> bool:
        previous_ready = self.attach_ready
        previous = self._tracker.snapshot
        had_snapshot = self._had_snapshot
        accepted = self._tracker.accept(value)
        if not accepted:
            return False
        self._failures = 0
        self._transport_stale = False
        self._had_snapshot = True
        current_ready = self.attach_ready
        if current_ready:
            self._set_warning("")
            endpoint_changed = previous is not None and (
                previous.instance_id,
                previous.generation,
                previous.gdb_endpoint,
            ) != (
                value.instance_id,
                value.generation,
                value.gdb_endpoint,
            )
            if had_snapshot and (not previous_ready or endpoint_changed):
                self.recovered.emit(value)
        else:
            message = _REASON_MESSAGES.get(
                value.reason_code,
                "Gateway chưa sẵn sàng: %s." % value.reason_code,
            )
            self._set_warning(message)
        self.snapshot_changed.emit(value)
        return True

    def accept_failure(self, message: str) -> None:
        self._failures += 1
        if self._failures < self._failure_threshold:
            return
        self._transport_stale = True
        self._set_warning("Mất liên lạc Gateway: %s" % (str(message).strip() or "không có phản hồi"))

    def _set_warning(self, message: str) -> None:
        selected = str(message)
        if selected == self._warning:
            return
        self._warning = selected
        self.warning_changed.emit(selected)


__all__ = ["GatewayHealthController"]
