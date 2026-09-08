"""Continuously surface authenticated Gateway health to the production GUI."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from b300_core.gateway_protocol import GATEWAY_STATUS_COMMAND
from b300_core.gateway_status import GatewaySnapshot, GatewaySnapshotTracker
from b300_core.remote_profile import RemoteGatewayProfile
from b300_core.models import ProbeInfo

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
                 interval_ms: int = 1000, failure_threshold: int = 3,
                 context=None) -> None:
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
        self._context = context
        self._bind_token = 0

    def set_context(self, context) -> None:
        self._context = context

    def _publish_snapshot(self, value: GatewaySnapshot) -> None:
        context = self._context
        if context is None:
            return
        probe = value.selected_probe or {}
        updates = dict(
            connection_id=context.selected_connection.connection_id,
            probe_identity=str(probe.get("identity") or probe.get("serial") or "") or None,
            probe_serial=str(probe.get("serial") or "") or None,
            gateway_instance_id=value.instance_id,
            gateway_generation=value.generation,
            sequence=value.sequence,
            gdb_endpoint=value.gdb_endpoint,
            tcl_endpoint=value.tcl_endpoint,
            target_state=value.cpu_state if value.attach_ready else None,
            reason=value.reason_code,
        )
        ssh_generation = getattr(value, "ssh_generation", None)
        if ssh_generation is not None:
            updates["ssh_generation"] = ssh_generation
        context.apply_device_state(**updates)
        context.set_gateway_health(value, self._warning)
        # Remote session adapters may attach the authenticated public lease
        # snapshot to the health result; publish only that bounded object.
        context.set_gateway_agent_status(getattr(value, "agent_status", None))
        context.set_gateway_lease_snapshot(getattr(value, "lease_snapshot", None))
        if value.selected_probe is not None:
            serial = str(probe.get("serial") or "") or None
            if serial is not None and not any(item.serial == serial for item in context.probes):
                context.set_probes((ProbeInfo(serial, "ST-Link", "gateway"),), serial)
        elif not context.selected_connection.is_local:
            # Only the Gateway owns this derived list.  A remote removal must
            # never leave DeviceView selecting the former sole probe.
            context.set_probes((), None)

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
        self._bind_token += 1
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
        self._bind_token += 1
        self._timer.stop()
        worker = self._worker
        self._worker = None
        if worker is not None and worker.isRunning():
            worker.cancel()

    def poll_now(self) -> None:
        if self._profile is None or self._worker_factory is None or self._worker is not None:
            return
        profile = self._profile
        bind_token = self._bind_token

        def operation(_log, _phase, _cancel):
            session = self._sessions.session(profile)
            status = getattr(session, "gateway_status", None)
            if callable(status):
                return status(timeout_seconds=5.0)
            # Compatibility with the first protocol implementation.
            return session._run_gateway_cli(GATEWAY_STATUS_COMMAND, timeout_seconds=5.0)

        worker = self._worker_factory(operation, self)
        self._worker = worker
        worker.completed.connect(lambda value: self._poll_completed(value, bind_token))
        worker.failed.connect(lambda failure: self._poll_failed(failure, bind_token))
        worker.finished.connect(lambda: self._poll_finished(worker, bind_token))
        worker.start()

    def _poll_completed(self, value, bind_token=None) -> None:
        if bind_token is not None and bind_token != self._bind_token:
            return
        if isinstance(value, GatewaySnapshot):
            self.accept_snapshot(value)
        else:
            self.accept_failure("Gateway trả về trạng thái không hợp lệ.")

    def _poll_failed(self, failure, bind_token) -> None:
        if bind_token == self._bind_token:
            self.accept_failure(failure.message)

    def _poll_finished(self, worker, bind_token) -> None:
        if bind_token == self._bind_token and worker is self._worker:
            self._worker = None
        if worker is not None:
            worker.deleteLater()

    def accept_snapshot(self, value: GatewaySnapshot) -> bool:
        # Activity evidence is carried with this immutable snapshot; client
        # lifecycle policy is enforced by VsCodeDebugController.
        previous_ready = self.attach_ready
        previous = self._tracker.snapshot
        had_snapshot = self._had_snapshot
        accepted = self._tracker.accept(value)
        if not accepted:
            return False
        self._failures = 0
        self._transport_stale = False
        self._had_snapshot = True
        self._publish_snapshot(value)
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
        warning = "Mất liên lạc Gateway: %s" % (str(message).strip() or "không có phản hồi")
        context = self._context
        if context is not None:
            context.apply_device_state(
                owner_kind=None, target_state=None, gdb_endpoint=None,
                tcl_endpoint=None, reason=warning,
            )
        self._set_warning(warning)

    def _set_warning(self, message: str) -> None:
        selected = str(message)
        if selected == self._warning:
            return
        self._warning = selected
        self.warning_changed.emit(selected)


__all__ = ["GatewayHealthController"]
