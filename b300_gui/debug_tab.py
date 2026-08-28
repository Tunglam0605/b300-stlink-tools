"""Minimal safe GUI surface for OpenOCD + verified GDB/MI control."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from b300_core.debug_service import DebugConfig, DebugService, DebugState
from b300_core.gdb_mi import GdbMiBackend
from b300_core.models import ProbeRef

from .workers import FunctionWorker, WorkerFailure
from .log_highlighter import format_log_html


class DebugTab(QWidget):
    """Debug controls only; firmware programming remains in provisioning services."""

    operation_state_changed = Signal(bool)
    log = Signal(str)

    def __init__(self, service: DebugService,
                 selected_probe: Callable[[], ProbeRef], parent=None,
                 gdb_backend: Optional[GdbMiBackend] = None) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_probe = selected_probe
        self.gdb_backend = gdb_backend or GdbMiBackend()
        self._worker = None
        self._retired_workers = []
        self._active_config: Optional[DebugConfig] = None
        self._pending_config: Optional[DebugConfig] = None
        self._external_blocked = False
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(500)
        self._watchdog.timeout.connect(self._poll_debug_service)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Target & State Banner
        header_card = QGroupBox("Mục tiêu & Trạng thái Debug")
        header_layout = QHBoxLayout(header_card)

        probe_info_layout = QVBoxLayout()
        self.probe_display = QLabel("ST-Link Probe: Tự động chọn")
        self.probe_display.setStyleSheet("font-weight: 700; color: #0F172A;")
        safety = QLabel(
            "Debug chỉ điều khiển target qua OpenOCD/GDB · Không xóa flash, không nạp firmware "
            "và không sửa Option Bytes."
        )
        safety.setStyleSheet("color: #64748B; font-size: 12px;")
        safety.setWordWrap(True)
        probe_info_layout.addWidget(self.probe_display)
        probe_info_layout.addWidget(safety)
        header_layout.addLayout(probe_info_layout, 1)

        self.status_label = QLabel("ĐÃ DỪNG")
        self.status_label.setObjectName("debugStateBadge")
        self.status_label.setProperty("state", "stopped")
        header_layout.addWidget(self.status_label)
        layout.addWidget(header_card)

        # Symbols & Connections Grid
        config_grid = QHBoxLayout()

        symbols_box = QGroupBox("Debug symbols (.elf / .axf)")
        symbols_layout = QHBoxLayout(symbols_box)
        self.symbol_path = QLineEdit()
        self.symbol_path.setObjectName("debugSymbolPath")
        self.symbol_path.setPlaceholderText("Tùy chọn: firmware.elf hoặc firmware.axf")
        self.symbol_browse_button = QPushButton("Chọn ELF/AXF")
        self.symbol_browse_button.setObjectName("debugSymbolBrowseButton")
        self.symbol_browse_button.clicked.connect(self.choose_symbol_file)
        symbols_layout.addWidget(self.symbol_path, 1)
        symbols_layout.addWidget(self.symbol_browse_button)
        config_grid.addWidget(symbols_box, 3)

        connection_box = QGroupBox("Cấu hình cổng kết nối")
        form = QHBoxLayout(connection_box)

        self.bind_address = QLineEdit("127.0.0.1")
        self.bind_address.setObjectName("debugBindAddress")
        self.bind_address.setToolTip("Địa chỉ bind mạng")

        self.gdb_port = QLineEdit("3333")
        self.gdb_port.setObjectName("debugGdbPort")
        self.gdb_port.setToolTip("Cổng TCP cho GDB")

        self.telnet_port = QLineEdit()
        self.telnet_port.setObjectName("debugTelnetPort")
        self.telnet_port.setPlaceholderText("Telnet tắt")
        self.telnet_port.setToolTip("Cổng Telnet (để trống để tắt)")

        form.addWidget(QLabel("Host:"))
        form.addWidget(self.bind_address)
        form.addWidget(QLabel("GDB:"))
        form.addWidget(self.gdb_port)
        form.addWidget(QLabel("Telnet:"))
        form.addWidget(self.telnet_port)
        config_grid.addWidget(connection_box, 2)
        layout.addLayout(config_grid)

        # Actions Toolbar
        actions_box = QGroupBox("Điều khiển phiên Debug")
        actions_layout = QHBoxLayout(actions_box)

        self.start_button = QPushButton("Khởi động Server")
        self.start_button.setObjectName("debugStartButton")
        self.connect_button = QPushButton("Kết nối GDB")
        self.connect_button.setObjectName("debugConnectButton")
        self.stop_button = QPushButton("Dừng Debug")
        self.stop_button.setObjectName("debugStopButton")
        self.start_button.clicked.connect(self.start_debug)
        self.connect_button.clicked.connect(self.connect_gdb)
        self.stop_button.clicked.connect(self.stop_debug)

        actions_layout.addWidget(self.start_button)
        actions_layout.addWidget(self.connect_button)
        actions_layout.addWidget(self.stop_button)

        actions_layout.addSpacing(16)

        self.halt_button = QPushButton("Tạm dừng (Halt)")
        self.halt_button.setObjectName("debugHaltButton")
        self.continue_button = QPushButton("Tiếp tục (Run)")
        self.continue_button.setObjectName("debugContinueButton")
        self.reset_button = QPushButton("Reset + Halt")
        self.reset_button.setObjectName("debugResetButton")
        self.halt_button.clicked.connect(self.halt_target)
        self.continue_button.clicked.connect(self.continue_target)
        self.reset_button.clicked.connect(self.reset_halt_target)

        actions_layout.addWidget(self.halt_button)
        actions_layout.addWidget(self.continue_button)
        actions_layout.addWidget(self.reset_button)
        actions_layout.addStretch(1)
        layout.addWidget(actions_box)

        # Log Console
        log_box = QGroupBox("Nhật ký OpenOCD / GDB")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("debugLogView")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_box, 1)
        self.log.connect(self._append_log)

        self._refresh_controls()

    def _append_log(self, line: str) -> None:
        html_line = format_log_html(str(line))
        self.log_view.appendHtml(html_line)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )
        self.log_view.horizontalScrollBar().setValue(0)

    @property
    def has_active_operation(self) -> bool:
        return self._worker is not None or self.service.state in (
            DebugState.STARTING, DebugState.READY, DebugState.CONNECTED,
        )

    def set_external_blocked(self, blocked: bool) -> None:
        """Block creation of a debug session while Flash/Factory/Memory owns ST-Link."""
        self._external_blocked = bool(blocked)
        self._refresh_controls()

    def _poll_debug_service(self) -> None:
        state = self.service.state
        if state == DebugState.FAILED:
            self._watchdog.stop()
            try:
                self.gdb_backend.stop()
            except Exception as error:
                self.log.emit("GDB cleanup after OpenOCD failure: %s" % error)
            self._active_config = None
            self._pending_config = None
            self.status_label.setText("OpenOCD đã dừng bất ngờ; debug session được giải phóng")
            self.log.emit("OpenOCD exited unexpectedly; hardware interlock released.")
            self.operation_state_changed.emit(False)
            self._refresh_controls()

    def choose_symbol_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn ELF/AXF có debug symbols", "", "Debug symbols (*.elf *.axf)"
        )
        if path:
            self.symbol_path.setText(str(Path(path)))

    def _update_probe_display(self) -> None:
        try:
            probe = self.selected_probe()
            serial = getattr(probe, "serial", str(probe)) if probe else "Chưa chọn probe"
            self.probe_display.setText("ST-Link Probe: %s" % (serial or "Tự động chọn"))
        except Exception:
            self.probe_display.setText("ST-Link Probe: Tự động chọn")

    def _refresh_controls(self) -> None:
        self._update_probe_display()
        state = self.service.state
        worker_busy = self._worker is not None
        server_active = state in (DebugState.STARTING, DebugState.READY, DebugState.CONNECTED)
        connected = state == DebugState.CONNECTED
        self.start_button.setEnabled(
            not worker_busy and not self._external_blocked and
            state in (DebugState.STOPPED, DebugState.FAILED)
        )
        self.connect_button.setEnabled(not worker_busy and state == DebugState.READY)
        self.stop_button.setEnabled(not worker_busy and server_active)
        self.halt_button.setEnabled(not worker_busy and connected)
        self.continue_button.setEnabled(not worker_busy and connected)
        self.reset_button.setEnabled(not worker_busy and connected)
        self.bind_address.setEnabled(not worker_busy and not server_active)
        self.gdb_port.setEnabled(not worker_busy and not server_active)
        self.telnet_port.setEnabled(not worker_busy and not server_active)
        self.symbol_path.setEnabled(not worker_busy and not connected)
        self.symbol_browse_button.setEnabled(not worker_busy and not connected)

        badge_state = "stopped"
        if state == DebugState.READY:
            badge_state = "ready"
        elif state == DebugState.CONNECTED:
            badge_state = "connected"
        elif state == DebugState.FAILED:
            badge_state = "failed"
        elif worker_busy:
            badge_state = "running"
        self.status_label.setProperty("state", badge_state)
        if self.status_label.style() is not None:
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)

    def _begin_worker(self, operation, completed, status_text: str,
                      failed=None) -> None:
        if self._worker is not None:
            return
        self.status_label.setText(status_text)
        worker = FunctionWorker(operation, self)
        self._worker = worker
        worker.log.connect(self.log)
        worker.completed.connect(completed)
        worker.failed.connect(failed or self._gdb_failed)
        worker.finished.connect(self._worker_finished)
        self._refresh_controls()
        self.operation_state_changed.emit(True)
        worker.start()

    def _worker_finished(self) -> None:
        worker = self.sender()
        if worker is not None:
            # QThread.finished can be delivered while Qt is still completing teardown;
            # join before dropping the last Python reference to avoid "QThread destroyed" aborts.
            worker.wait()
        if worker is self._worker:
            self._worker = None
        if worker is not None:
            # Keep a Python reference to the finished QThread for the widget lifetime.
            # PySide/Qt can abort if a queued delete races final QThread teardown.
            self._retired_workers.append(worker)
        self._refresh_controls()
        self.operation_state_changed.emit(self.has_active_operation)

    def start_debug(self) -> None:
        try:
            port = int(self.gdb_port.text().strip())
            telnet_text = self.telnet_port.text().strip()
            telnet = int(telnet_text) if telnet_text else None
            config = DebugConfig(
                self.selected_probe(), self.bind_address.text().strip(), port, telnet
            )
            config.validate()
        except (ValueError, TypeError) as error:
            self._start_failed_message(str(error))
            return
        self._pending_config = config
        self._begin_worker(
            lambda log, _phase, _cancel: self.service.start(config, event_sink=log),
            self._started,
            "Đang khởi động OpenOCD...",
            self._start_failed,
        )

    def _started(self, _state) -> None:
        self._active_config = self._pending_config
        self._pending_config = None
        config = self._active_config
        if config is None:
            self._start_failed_message("Debug configuration was lost during startup.")
            return
        self.status_label.setText(
            "OpenOCD sẵn sàng; GDB: %s:%d" % (config.bind_address, config.gdb_port)
        )
        self.log.emit("Debug server ready.")
        self._watchdog.start()
        self._refresh_controls()

    def _start_failed(self, failure: WorkerFailure) -> None:
        self._watchdog.stop()
        self._pending_config = None
        self._active_config = None
        self._start_failed_message(failure.message)

    def _start_failed_message(self, message: str) -> None:
        self.status_label.setText("Không thể khởi động Debug: %s" % message)
        self.log.emit("Debug start failed: %s" % message)
        self.operation_state_changed.emit(False)
        self._refresh_controls()

    def connect_gdb(self) -> None:
        if self._active_config is None or self.service.state != DebugState.READY:
            self._gdb_failed_message("OpenOCD chưa sẵn sàng để GDB kết nối.")
            return
        config = self._active_config
        symbol_text = self.symbol_path.text().strip()

        def operation(log, _phase, _cancel):
            self.gdb_backend.start()
            if symbol_text:
                self.gdb_backend.load_symbols(Path(symbol_text))
                log("Loaded debug symbols: %s" % symbol_text)
            self.gdb_backend.connect(config.bind_address, config.gdb_port)
            self.service.mark_connected()
            return True

        self._begin_worker(operation, self._gdb_connected, "Đang kết nối GDB...")

    def _gdb_connected(self, _result) -> None:
        config = self._active_config
        if config is None:
            self._gdb_failed_message("Missing active debug configuration.")
            return
        self.status_label.setText(
            "GDB đã kết nối: %s:%d" % (config.bind_address, config.gdb_port)
        )
        self.log.emit("GDB connected and verified by backend.")
        self._refresh_controls()

    def _gdb_failed(self, failure: WorkerFailure) -> None:
        # A GDB command failure must not pretend the still-running OpenOCD session is idle.
        try:
            if self.service.state == DebugState.READY:
                self.gdb_backend.stop()
        finally:
            self._gdb_failed_message(failure.message)

    def _gdb_failed_message(self, message: str) -> None:
        state = self.service.state
        if state in (DebugState.READY, DebugState.CONNECTED):
            prefix = "GDB lỗi (OpenOCD vẫn đang chạy)"
        else:
            prefix = "Debug lỗi"
        self.status_label.setText("%s: %s" % (prefix, message))
        self.log.emit("GDB operation failed: %s" % message)
        self.operation_state_changed.emit(self.has_active_operation)
        self._refresh_controls()

    def halt_target(self) -> None:
        self._run_control("Halt", self.gdb_backend.interrupt)

    def continue_target(self) -> None:
        self._run_control("Continue", self.gdb_backend.continue_execution)

    def reset_halt_target(self) -> None:
        self._run_control("Reset + Halt", self.gdb_backend.reset_halt)

    def _run_control(self, label: str, command) -> None:
        if self.service.state != DebugState.CONNECTED:
            self._gdb_failed_message("GDB chưa ở trạng thái CONNECTED.")
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: command(),
            lambda _result, action=label: self._control_completed(action),
            "Đang thực hiện %s..." % label,
        )

    def _control_completed(self, label: str) -> None:
        self.status_label.setText("GDB CONNECTED; thao tác %s hoàn tất" % label)
        self.log.emit("GDB control completed: %s" % label)
        self._refresh_controls()

    def stop_debug(self) -> None:
        if self._worker is not None:
            return
        try:
            self.gdb_backend.stop()
        finally:
            self.service.stop()
        self._watchdog.stop()
        self._active_config = None
        self._pending_config = None
        self.status_label.setText("Đã dừng")
        self.log.emit("Debug server stopped.")
        self.operation_state_changed.emit(False)
        self._refresh_controls()
