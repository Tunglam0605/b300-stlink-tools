"""Integrated, state-aware GUI debug workstation surface for B300 STM32F407."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from b300_core.debug_service import DebugConfig, DebugService, DebugState
from b300_core.debug_session import DebugSession, DebugSessionConfig, DebugSessionInfo
from b300_core.elf_matcher import discover_symbol_files, find_matching_symbol_file
from b300_core.gdb_mi import GdbMiBackend
from b300_core.models import ProbeRef
from b300_core.live_monitor import LiveSample
from b300_core.offline_symbols import OfflineSymbolTable
from b300_core.live_session import (
    ClientLiveMonitorConfig, LiveMonitorSession, LocalLiveMonitorConfig,
)
from b300_core.remote_debug_guard import RemoteDebugGuard
from b300_core.ssh_host_trust import trusted_known_hosts_file
from b300_core.ssh_identity import managed_identity_file
from b300_core.remote_profile import load_remote_profile
from b300_core.ssh_debug_tunnel import (
    SshDebugTunnel, SshDebugTunnelConfig, find_available_loopback_port,
)
from b300_core.tcl_client import SafeTclClient, TclEndpoint

from .workers import FunctionWorker, WorkerFailure
from .log_highlighter import format_log_html
from .debug_connection_panel import DebugConnectionPanel
from .debug_live_panel import DebugLivePanel
from .debug_plot_panel import DebugPlotPanel
from .debug_interactive_panel import DebugInteractivePanel
from .debug_log_panel import DebugLogPanel
from .remote_vscode_dialog import RemoteVsCodeDialog
from .symbol_browser_dialog import SymbolBrowserDialog


class DebugTab(QWidget):
    """Integrated Engineering Workstation for OpenOCD + TCL + GDB debug & zero-halt monitoring."""

    operation_state_changed = Signal(bool)
    log = Signal(str)

    def __init__(self, service: DebugService,
                 selected_probe: Callable[[], ProbeRef], parent=None,
                 gdb_backend: Optional[GdbMiBackend] = None,
                 debug_session: Optional[DebugSession] = None,
                 tcl_factory=SafeTclClient, settings=None,
                 probe_count: Optional[Callable[[], int]] = None,
                 tunnel_factory=SshDebugTunnel, live_session_factory=LiveMonitorSession,
                 profile_loader=load_remote_profile) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_probe = selected_probe
        if debug_session is not None:
            self.session = debug_session
            self.gdb_backend = debug_session.gdb
        else:
            self.gdb_backend = gdb_backend or GdbMiBackend()
            self.session = DebugSession(service=service, gdb=self.gdb_backend)
        self._worker = None
        self._retired_workers = []
        self._external_blocked = False
        self._target_state: Optional[str] = None
        self._initial_target_state: Optional[str] = None
        self._status_override: Optional[tuple[str, str]] = None
        self._remote_server_active = False
        self._remote_tcl = None
        self._remote_guard = None
        self._remote_vscode_dialog = None
        self._client_tunnel = None
        self._client_mode_active = False
        self._symbol_root: Optional[Path] = None
        self._tcl_factory = tcl_factory
        self._settings = settings
        self._probe_count = probe_count
        self._tunnel_factory = tunnel_factory
        self._live_session_factory = live_session_factory
        self._profile_loader = profile_loader
        self._managed_profile_loaded = False
        self._sampling_active = False
        self._live_session: Optional[LiveMonitorSession] = None

        self._watchdog = QTimer(self)
        self._watchdog.setInterval(750)
        self._watchdog.timeout.connect(self._poll_debug_service)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Responsive workstation scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("debugScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("debugScrollContent")
        layout = QVBoxLayout(self.scroll_content)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll_area.setWidget(self.scroll_content)
        root_layout.addWidget(self.scroll_area)

        # Safety-first mode guide. Keep the non-halting path visually primary.
        self.safety_guide = QFrame(self.scroll_content)
        self.safety_guide.setObjectName("debugSafetyGuide")
        safety_layout = QHBoxLayout(self.safety_guide)
        safety_layout.setContentsMargins(10, 7, 10, 7)
        safety_layout.setSpacing(10)
        safe = QLabel("● LIVE MONITOR · KHUYẾN NGHỊ · MCU tiếp tục RUNNING")
        safe.setObjectName("debugSafeModeBadge")
        intrusive = QLabel("▲ INTERACTIVE DEBUG · Có thể HALT/STEP/RESET MCU")
        intrusive.setObjectName("debugIntrusiveModeBadge")
        safety_layout.addWidget(safe)
        safety_layout.addWidget(intrusive)
        safety_layout.addStretch(1)
        layout.addWidget(self.safety_guide)

        # 1. Top Section: Connection & Environment Panel
        self.conn_panel = DebugConnectionPanel(self.scroll_content)
        layout.addWidget(self.conn_panel)

        # Compatibility aliases for Connection Panel
        self.probe_display = self.conn_panel.probe_display
        self.status_label = self.conn_panel.status_label
        self.mode_combo = self.conn_panel.mode_combo
        self.role_summary = self.conn_panel.role_summary
        self.symbols_box = self.conn_panel.symbols_box
        self.symbol_path = self.conn_panel.symbol_path
        self.symbol_browse_button = self.conn_panel.symbol_browse_button
        self.symbol_auto_button = self.conn_panel.symbol_auto_button
        self.client_box = self.conn_panel.client_box
        self.client_host = self.conn_panel.client_host
        self.client_user = self.conn_panel.client_user
        self.client_ssh_port = self.conn_panel.client_ssh_port
        self.connection_box = self.conn_panel.connection_box
        self.bind_address = self.conn_panel.bind_address
        self.gdb_port = self.conn_panel.gdb_port
        self.tcl_display = self.conn_panel.tcl_display
        self.start_button = self.conn_panel.start_button
        self.remote_server_button = self.conn_panel.remote_server_button
        self.remote_kit_button = self.conn_panel.remote_kit_button
        self.stop_button = self.conn_panel.stop_button

        self.symbol_browse_button.clicked.connect(self.choose_symbol_file)
        self.symbol_auto_button.clicked.connect(self.auto_match_symbols)
        self.start_button.clicked.connect(self.start_selected_mode)
        self.remote_server_button.clicked.connect(self.start_remote_server)
        self.remote_kit_button.clicked.connect(self.show_remote_vscode_dialog)
        self.stop_button.clicked.connect(self.stop_debug)

        # 2. Realtime Live Monitor Section (Zero-Halt SWD)
        self.live_panel = DebugLivePanel(self.scroll_content)
        self.live_box = self.live_panel
        layout.addWidget(self.live_panel)

        # Compatibility aliases for Live Panel
        self.sample_expressions = self.live_panel.expressions
        self.sample_cycles = self.live_panel.cycles
        self.sample_interval = self.live_panel.interval
        self.sample_start_button = self.live_panel.start_button
        self.sample_stop_button = self.live_panel.stop_button
        self.sample_clear_button = self.live_panel.clear_button
        self.sample_export_button = self.live_panel.export_button
        self.sample_status = self.live_panel.status
        self.sample_impact = self.live_panel.impact
        self.sample_table = self.live_panel.table
        self._sample_buffer = self.live_panel.buffer
        self._sample_rows = self.live_panel.rows

        self.sample_start_button.clicked.connect(self.start_live_sampling)
        self.sample_stop_button.clicked.connect(self.stop_live_sampling)
        self.sample_clear_button.clicked.connect(self.clear_live_samples)
        self.sample_export_button.clicked.connect(self.export_live_samples)
        self.live_panel.symbol_browser_requested.connect(self.browse_live_symbols)

        # 3. Live Waveform Plot Section (Collapsible)
        self.plot_panel = DebugPlotPanel(self.scroll_content)
        self.live_plot = self.plot_panel.plot_widget
        layout.addWidget(self.plot_panel)

        # 4. Interactive Debug Section (Intrusive / Collapsible)
        self.interactive_panel = DebugInteractivePanel(self.scroll_content)
        self.diagnostics_box = self.interactive_panel
        layout.addWidget(self.interactive_panel)

        # Compatibility aliases for Interactive Panel
        self.halt_button = self.interactive_panel.halt_button
        self.continue_button = self.interactive_panel.continue_button
        self.reset_button = self.interactive_panel.reset_button
        self.step_into_button = self.interactive_panel.step_into_button
        self.step_over_button = self.interactive_panel.step_over_button
        self.where_button = self.interactive_panel.where_button
        self.stack_button = self.interactive_panel.stack_button
        self.registers_button = self.interactive_panel.registers_button
        self.variable_expression = self.interactive_panel.variable_expression
        self.variable_button = self.interactive_panel.variable_button
        self.break_location = self.interactive_panel.break_location
        self.break_once_button = self.interactive_panel.break_once_button
        self.watch_expression = self.interactive_panel.watch_expression
        self.watch_once_button = self.interactive_panel.watch_once_button
        self.stop_timeout = self.interactive_panel.stop_timeout
        self.diagnostic_view = self.interactive_panel.diagnostic_view

        self.halt_button.clicked.connect(self.halt_target)
        self.continue_button.clicked.connect(self.continue_target)
        self.reset_button.clicked.connect(self.reset_halt_target)
        self.step_into_button.clicked.connect(self.step_into_target)
        self.step_over_button.clicked.connect(self.step_over_target)
        self.where_button.clicked.connect(self.inspect_where)
        self.stack_button.clicked.connect(self.inspect_stack)
        self.registers_button.clicked.connect(self.inspect_registers)
        self.variable_button.clicked.connect(self.inspect_variable)
        self.break_once_button.clicked.connect(self.break_once)
        self.watch_once_button.clicked.connect(self.watch_once)

        # 5. Technical Log Section (Collapsible)
        self.log_panel = DebugLogPanel(self.scroll_content)
        self.log_box = self.log_panel
        self.log_view = self.log_panel.log_view
        layout.addWidget(self.log_panel)

        self.log.connect(self._append_log)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.client_host.textChanged.connect(self._save_debug_preferences)
        self.client_user.textChanged.connect(self._save_debug_preferences)
        self.client_ssh_port.valueChanged.connect(self._save_debug_preferences)
        self.symbol_path.textChanged.connect(self._save_debug_preferences)
        self.sample_expressions.textChanged.connect(self._save_debug_preferences)
        self.sample_cycles.valueChanged.connect(self._save_debug_preferences)
        self.sample_interval.valueChanged.connect(self._save_debug_preferences)
        self._restore_debug_preferences()
        self._refresh_controls()

    def _setting_value(self, key: str, default=None):
        if self._settings is None:
            return default
        try:
            return self._settings.value(key, default)
        except Exception:
            return default

    def _restore_debug_preferences(self) -> None:
        mode = str(self._setting_value("debug/mode", "auto") or "auto")
        host = str(self._setting_value("debug/gateway_host", "") or "")
        user = str(self._setting_value("debug/gateway_user", "") or "")
        self._managed_profile_loaded = False
        if not host or not user:
            try:
                profile = self._profile_loader()
            except Exception:
                profile = None
            if profile is not None:
                host = profile.host
                user = profile.user
                ssh_port = profile.port
                self._managed_profile_loaded = True
            else:
                ssh_port = None
        else:
            ssh_port = None
        last_symbols = str(self._setting_value("debug/last_symbols", "") or "")
        root_text = str(self._setting_value("debug/symbol_root", "") or "")
        if ssh_port is None:
            try:
                ssh_port = int(self._setting_value("debug/gateway_ssh_port", 22) or 22)
            except (TypeError, ValueError):
                ssh_port = 22
        sample_expressions = str(self._setting_value("debug/sample_expressions", "") or "")
        try:
            sample_cycles = int(self._setting_value("debug/sample_cycles", 100) or 100)
        except (TypeError, ValueError):
            sample_cycles = 100
        try:
            sample_interval = float(self._setting_value("debug/sample_interval", 0.5) or 0.5)
        except (TypeError, ValueError):
            sample_interval = 0.5
        widgets = (
            self.mode_combo, self.client_host, self.client_user, self.client_ssh_port, self.symbol_path,
            self.sample_expressions, self.sample_cycles, self.sample_interval,
        )
        for widget in widgets:
            widget.blockSignals(True)
        try:
            index = self.mode_combo.findData(mode)
            self.mode_combo.setCurrentIndex(index if index >= 0 else 0)
            self.client_host.setText(host)
            self.client_user.setText(user)
            self.client_ssh_port.setValue(max(1, min(65535, ssh_port)))
            self.sample_expressions.setText(sample_expressions)
            self.sample_cycles.setValue(max(1, min(100000, sample_cycles)))
            self.sample_interval.setValue(max(0.1, min(60.0, sample_interval)))
            if last_symbols and Path(last_symbols).is_file():
                self.symbol_path.setText(last_symbols)
            if root_text and Path(root_text).is_dir():
                self._symbol_root = Path(root_text).expanduser().resolve()
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self._update_role_ui()

    def _save_debug_preferences(self, *_args) -> None:
        if self._settings is None:
            return
        try:
            self._settings.setValue("debug/mode", self.mode_combo.currentData() or "auto")
            self._settings.setValue("debug/gateway_host", self.client_host.text().strip())
            self._settings.setValue("debug/gateway_user", self.client_user.text().strip())
            self._settings.setValue("debug/gateway_ssh_port", self.client_ssh_port.value())
            self._settings.setValue("debug/sample_expressions", self.sample_expressions.text().strip())
            self._settings.setValue("debug/sample_cycles", self.sample_cycles.value())
            self._settings.setValue("debug/sample_interval", self.sample_interval.value())
            symbol_text = self.symbol_path.text().strip()
            if symbol_text:
                self._settings.setValue("debug/last_symbols", symbol_text)
            if self._symbol_root is not None:
                self._settings.setValue("debug/symbol_root", str(self._symbol_root))
        except Exception:
            pass

    def _local_probe_count(self) -> int:
        if self._probe_count is None:
            return 1
        try:
            return max(0, int(self._probe_count()))
        except Exception:
            return 0

    def _resolved_role(self) -> str:
        selected = str(self.mode_combo.currentData() or "auto")
        if selected in {"local", "gateway", "client"}:
            return selected
        if self._local_probe_count() > 0:
            return "local"
        return "client"

    def _mode_changed(self, *_args) -> None:
        self._save_debug_preferences()
        self._update_role_ui()
        self._refresh_controls()

    def _update_role_ui(self) -> None:
        role = self._resolved_role()
        selected = str(self.mode_combo.currentData() or "auto")
        if selected == "auto":
            if role == "local":
                summary = "AUTO → Local vì phát hiện ST-Link trên máy này."
            else:
                summary = "AUTO → Client vì không phát hiện ST-Link local."
        elif role == "gateway":
            summary = "Gateway giữ ST-Link/OpenOCD; máy khác kết nối qua SSH."
        elif role == "client":
            if self._managed_profile_loaded:
                summary = "Client · dùng saved Gateway profile đã xác minh · SSH strict trust + managed key."
            else:
                summary = "Client tự mở SSH tunnel rồi GDB/MI attach tới Gateway. Thiết lập profile ở Gateway Setup để không nhập lại endpoint."
        else:
            summary = "Local debug trực tiếp ST-Link trên máy này."
        self.role_summary.setText(summary)
        self.client_box.setVisible(role == "client")
        self.symbols_box.setVisible(role in {"local", "client"})
        self.connection_box.setVisible(role == "gateway")
        self.diagnostics_box.setVisible(role in {"local", "client"})
        self.remote_kit_button.setVisible(role == "gateway")
        if role == "gateway":
            self.tcl_display.setText("GDB 3333 · TCL 6666 · loopback only")
        elif role == "client":
            self.tcl_display.setText("GDB/TCL: SSH tunnel tự chọn")
        else:
            self.tcl_display.setText("GDB/TCL: tự chọn loopback")
        self.start_button.setText(
            {"local": "BẮT ĐẦU LOCAL", "gateway": "KHỞI ĐỘNG GATEWAY", "client": "KẾT NỐI GATEWAY"}[role]
        )

    def refresh_environment(self) -> None:
        """Refresh Auto role after probe discovery without changing explicit user choice."""
        self._update_role_ui()
        self._refresh_controls()

    def _append_log(self, line: str) -> None:
        self.log_panel.append_log(line)

    @property
    def has_active_operation(self) -> bool:
        tunnel_active = self._client_tunnel is not None and self._client_tunnel.active
        live_active = self._live_session is not None and self._live_session.active
        return self._worker is not None or self.session.active or tunnel_active or live_active or self.service.state in (
            DebugState.STARTING, DebugState.READY, DebugState.CONNECTED,
        )

    def set_external_blocked(self, blocked: bool) -> None:
        self._external_blocked = bool(blocked)
        self._refresh_controls()

    def choose_symbol_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn ELF/AXF có debug symbols", "", "Debug symbols (*.elf *.axf)"
        )
        if path:
            self.symbol_path.setText(str(Path(path)))

    def browse_live_symbols(self) -> None:
        """Browse symbols from the selected AXF/ELF without touching the STM32 target."""
        symbol_text = self.symbol_path.text().strip()
        if not symbol_text:
            self._operation_failed_message(
                "Choose or auto-match an AXF/ELF symbol file before browsing Live Variables."
            )
            return
        path = Path(symbol_text).expanduser().resolve()
        if path.suffix.lower() not in {".axf", ".elf"} or not path.is_file():
            self._operation_failed_message("The selected AXF/ELF symbol file does not exist.")
            return
        symbols = None
        try:
            symbols = OfflineSymbolTable(path)
            dialog = SymbolBrowserDialog(symbols, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected = dialog.selected_symbol_name()
                if selected:
                    self.live_panel.select_symbol(selected)
                    self.log.emit(
                        "Selected offline Live Watch symbol: %s (data type still explicit)." % selected
                    )
        except (OSError, RuntimeError, ValueError) as error:
            self._operation_failed_message(str(error))
        finally:
            if symbols is not None:
                symbols.close()

    def auto_match_symbols(self) -> None:
        root_text = QFileDialog.getExistingDirectory(
            self, "Chọn thư mục project để tìm AXF/ELF", ""
        )
        if not root_text:
            return
        root = Path(root_text).expanduser().resolve()
        self._symbol_root = root
        self._save_debug_preferences()
        if self._resolved_role() == "client" and not self.session.active:
            candidates = discover_symbol_files([root], max_files=128, max_depth=8)
            if not candidates:
                self._operation_failed_message(
                    "Project đã lưu nhưng chưa tìm thấy .elf/.axf. Hãy build firmware trên máy Client trước."
                )
                return
            self._status_override = None
            self.diagnostic_view.setPlainText(
                "Đã lưu project root: %s\nTìm thấy %d AXF/ELF candidate.\n"
                "Khi bấm KẾT NỐI GATEWAY, tool sẽ tự so Flash qua SSH và chọn đúng file." %
                (root, len(candidates))
            )
            self.log.emit("Saved Client symbol root for automatic match: %s" % root)
            self._refresh_controls()
            return

        def operation(log, _phase, _cancel):
            candidates = discover_symbol_files([root], max_files=128, max_depth=8)
            if not candidates:
                raise RuntimeError("Không tìm thấy file .elf/.axf trong phạm vi project đã chọn.")
            temporary_server = not self.session.active
            tcl = None
            state_before = None
            try:
                if temporary_server:
                    try:
                        port = int(self.gdb_port.text().strip())
                    except ValueError as error:
                        raise ValueError("GDB port phải là số nguyên hợp lệ.") from error
                    config = DebugConfig(
                        self.selected_probe(), "127.0.0.1", port, None, 6666,
                    )
                    config.validate()
                    self.service.start(config, event_sink=log)
                    tcl = self._tcl_factory(TclEndpoint("127.0.0.1", 6666))
                    state_before = tcl.wait_target_state()
                    reader = tcl.read_words
                else:
                    state_before = self.session.target_poll()
                    reader = self.session.read_words

                selected, results = find_matching_symbol_file(candidates, reader)
                if temporary_server:
                    assert tcl is not None
                    state_after = tcl.wait_target_state()
                else:
                    state_after = self.session.target_poll()
                if state_after != state_before:
                    raise RuntimeError(
                        "Symbol matching changed target state unexpectedly: %s -> %s" %
                        (state_before, state_after)
                    )
                return selected, results, state_before, root
            finally:
                if temporary_server:
                    self.service.stop()

        self._begin_worker(
            operation, self._symbol_match_completed,
            "Đang đối chiếu AXF/ELF với Flash...",
        )

    def _symbol_match_completed(self, result) -> None:
        selected, results, state, root = result
        lines = ["Project root: %s" % root, "Target state preserved: %s" % state.upper(), ""]
        for item in results:
            lines.append(
                "%s  %d/%d  %.0f%%  %s" % (
                    "MATCH" if item.matched else "MISS",
                    item.matched_samples, item.total_samples, item.score * 100.0, item.path,
                )
            )
        self.diagnostic_view.setPlainText("\n".join(lines))
        if selected is None:
            exact_count = sum(1 for item in results if item.matched)
            if exact_count > 1:
                self._status_override = (
                    "Nhiều AXF/ELF khớp hoàn toàn · hãy chọn thủ công", "failed",
                )
                self.log.emit("Symbol auto-match is ambiguous: %d exact matches." % exact_count)
            else:
                self._status_override = (
                    "Không có AXF/ELF khớp firmware đang chạy", "failed",
                )
                self.log.emit("Symbol auto-match found no exact firmware match.")
        else:
            self.symbol_path.setText(str(selected.path))
            self._status_override = None
            self.log.emit("Verified matching debug symbols: %s" % selected.path)
            if self.session.active:
                self.log.emit("Restart Debug to load the newly matched symbols into GDB.")
        self._refresh_controls()

    def _update_probe_display(self) -> None:
        try:
            probe = self.selected_probe()
            serial = getattr(probe, "serial", str(probe)) if probe else "Chưa chọn probe"
            self.probe_display.setText("ST-Link: %s" % (serial or "Tự động chọn"))
        except Exception:
            self.probe_display.setText("ST-Link: Tự động chọn")

    def _set_target_state(self, state: Optional[str]) -> None:
        normalized = (state or "").strip().lower()
        self._target_state = normalized if normalized in {"running", "halted"} else None
        if self._status_override is not None:
            text, badge = self._status_override
            self.status_label.setText(text)
            self.status_label.setProperty("state", badge)
            if self.status_label.style() is not None:
                self.status_label.style().unpolish(self.status_label)
                self.status_label.style().polish(self.status_label)
            return
        if self._target_state == "running":
            if self._client_mode_active:
                self.status_label.setText("CLIENT CONNECTED · TARGET RUNNING")
            elif self._remote_server_active:
                self.status_label.setText("GATEWAY READY · TARGET RUNNING")
            else:
                self.status_label.setText("LOCAL CONNECTED · TARGET RUNNING")
            badge = "running"
        elif self._target_state == "halted":
            if self._client_mode_active:
                self.status_label.setText("CLIENT CONNECTED · TARGET HALTED")
            elif self._remote_server_active:
                self.status_label.setText("GATEWAY · TARGET HALTED")
            else:
                self.status_label.setText("LOCAL CONNECTED · TARGET HALTED")
            badge = "halted"
        elif self.session.active:
            self.status_label.setText(
                "CLIENT CONNECTED · TARGET UNKNOWN" if self._client_mode_active
                else "LOCAL CONNECTED · TARGET UNKNOWN"
            )
            badge = "connected"
        elif self._remote_server_active:
            self.status_label.setText("GATEWAY READY")
            badge = "ready"
        elif self.service.state == DebugState.READY:
            self.status_label.setText("OpenOCD READY")
            badge = "ready"
        elif self.service.state == DebugState.FAILED:
            self.status_label.setText("DEBUG FAILED")
            badge = "failed"
        else:
            self.status_label.setText("ĐÃ DỪNG")
            badge = "stopped"
        self.status_label.setProperty("state", badge)
        if self.status_label.style() is not None:
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)

    def _refresh_controls(self) -> None:
        self._update_probe_display()
        worker_busy = self._worker is not None
        active = self.session.active
        self.interactive_panel.set_target_state(
            self._target_state if self._target_state is not None else ("unknown" if active else None)
        )
        server_active = self.service.state in (
            DebugState.STARTING, DebugState.READY, DebugState.CONNECTED,
        )
        tunnel_active = self._client_tunnel is not None and self._client_tunnel.active
        live_active = self._live_session is not None and self._live_session.active
        can_start = (
            not worker_busy and not self._external_blocked and not server_active and not active
            and not tunnel_active and not live_active
        )
        self.start_button.setEnabled(can_start)
        self.remote_server_button.setEnabled(can_start)
        self.remote_kit_button.setEnabled(not worker_busy)
        self.stop_button.setEnabled(not worker_busy and (server_active or active or tunnel_active))
        self.mode_combo.setEnabled(not worker_busy and not server_active and not active and not tunnel_active and not live_active)
        client_editable = not worker_busy and not active and not tunnel_active and not live_active
        self.client_host.setEnabled(client_editable)
        self.client_user.setEnabled(client_editable)
        self.client_ssh_port.setEnabled(client_editable)
        self.halt_button.setEnabled(not worker_busy and active and self._target_state == "running")
        self.continue_button.setEnabled(not worker_busy and active and self._target_state == "halted")
        self.reset_button.setEnabled(not worker_busy and active)
        halted_controls = not worker_busy and active and self._target_state == "halted"
        self.step_into_button.setEnabled(halted_controls)
        self.step_over_button.setEnabled(halted_controls)
        diagnostic_enabled = not worker_busy and active
        self.where_button.setEnabled(diagnostic_enabled)
        self.stack_button.setEnabled(diagnostic_enabled)
        self.registers_button.setEnabled(diagnostic_enabled)
        self.variable_expression.setEnabled(diagnostic_enabled)
        self.variable_button.setEnabled(diagnostic_enabled)
        sample_ready = (
            not worker_busy and not self._external_blocked and not self._sampling_active
            and not active and not server_active and not tunnel_active and not live_active
            and self._resolved_role() in {"local", "client"}
        )
        sample_history_ready = len(self._sample_buffer) > 0 and not self._sampling_active
        self.live_panel.set_control_state(
            start_enabled=sample_ready,
            stop_enabled=worker_busy and self._sampling_active,
            history_enabled=sample_history_ready,
        )
        one_shot_enabled = diagnostic_enabled and self._target_state == "running"
        self.break_location.setEnabled(diagnostic_enabled)
        self.watch_expression.setEnabled(diagnostic_enabled)
        self.stop_timeout.setEnabled(diagnostic_enabled)
        self.break_once_button.setEnabled(one_shot_enabled)
        self.watch_once_button.setEnabled(one_shot_enabled)
        self.gdb_port.setEnabled(False)
        self.symbol_path.setEnabled(not worker_busy and not server_active and not active and not live_active)
        self.symbol_browse_button.setEnabled(not worker_busy and not server_active and not active and not live_active)
        self.symbol_auto_button.setEnabled(
            not worker_busy and not self._external_blocked and (active or not server_active)
        )
        if not worker_busy:
            self._update_role_ui()
            self._set_target_state(self._target_state)

    def _begin_worker(self, operation, completed, status_text: str, failed=None, phase_handler=None):
        if self._worker is not None:
            return None
        self._status_override = None
        self.status_label.setText(status_text)
        self.status_label.setProperty("state", "connected" if self.session.active else "ready")
        worker = FunctionWorker(operation, self)
        self._worker = worker
        worker.log.connect(self.log)
        if phase_handler is not None:
            worker.phase.connect(phase_handler)
        worker.completed.connect(completed)
        worker.failed.connect(failed or self._operation_failed)
        worker.finished.connect(self._worker_finished)
        self._refresh_controls()
        self.operation_state_changed.emit(True)
        worker.start()
        return worker

    def _worker_finished(self) -> None:
        worker = self.sender()
        if worker is not None:
            worker.wait()
        if worker is self._worker:
            self._worker = None
        if worker is not None:
            self._retired_workers.append(worker)
        self._refresh_controls()
        self.operation_state_changed.emit(self.has_active_operation)

    def start_selected_mode(self) -> None:
        role = self._resolved_role()
        self._save_debug_preferences()
        if role == "gateway":
            self.start_remote_server()
        elif role == "client":
            self.start_client_debug()
        else:
            self.start_debug()

    def start_client_debug(self) -> None:
        host = self.client_host.text().strip()
        user = self.client_user.text().strip()
        if not host or not user:
            self._start_failed_message(
                "Client cần Gateway host và SSH user. Chỉ phải nhập lần đầu; tool sẽ ghi nhớ."
            )
            return
        symbol_text = self.symbol_path.text().strip()
        symbols = Path(symbol_text).expanduser() if symbol_text else None
        if symbols is not None and not symbols.is_file():
            if self._symbol_root is not None and self._symbol_root.is_dir():
                self.log.emit("Stored AXF/ELF no longer exists; falling back to project auto-match.")
                symbols = None
            else:
                self._start_failed_message("AXF/ELF đã lưu không còn tồn tại: %s" % symbols)
                return
        try:
            local_gdb = find_available_loopback_port(13333)
            local_tcl = find_available_loopback_port(16666, avoid=(local_gdb,))
            tunnel_config = SshDebugTunnelConfig(
                host=host, user=user, ssh_port=self.client_ssh_port.value(),
                local_gdb_port=local_gdb, local_tcl_port=local_tcl,
                gateway_gdb_port=3333, gateway_tcl_port=6666,
                identity_file=managed_identity_file(),
                known_hosts_file=trusted_known_hosts_file(host, self.client_ssh_port.value()),
            )
            tunnel_config.validate()
        except (ValueError, RuntimeError) as error:
            self._start_failed_message(str(error))
            return

        def operation(log, _phase, _cancel):
            tunnel = self._tunnel_factory(tunnel_config)
            try:
                log(
                    "Opening managed SSH tunnel to %s@%s; local GDB=%d TCL=%d." %
                    (user, host, local_gdb, local_tcl)
                )
                version = tunnel.start()
                tcl = self._tcl_factory(TclEndpoint("127.0.0.1", local_tcl))
                state_before_match = tcl.wait_target_state()
                selected_symbols = symbols
                if selected_symbols is not None:
                    selected, results = find_matching_symbol_file((selected_symbols,), tcl.read_words)
                    if selected is None:
                        detail = results[0].reason if results else "ELF/AXF could not be parsed or sampled"
                        raise RuntimeError(
                            "AXF/ELF đã chọn không khớp firmware đang chạy trên Gateway: %s" % detail
                        )
                    selected_symbols = selected.path
                    log("Verified selected AXF/ELF against remote Application Flash: %s" % selected_symbols)
                elif self._symbol_root is not None and self._symbol_root.is_dir():
                    candidates = discover_symbol_files([self._symbol_root], max_files=128, max_depth=8)
                    if not candidates:
                        raise RuntimeError("Project root đã lưu không chứa AXF/ELF; hãy build firmware trước.")
                    selected, results = find_matching_symbol_file(candidates, tcl.read_words)
                    if selected is None:
                        exact_count = sum(1 for item in results if item.matched)
                        if exact_count > 1:
                            raise RuntimeError(
                                "Có nhiều AXF/ELF cùng khớp firmware; hãy chọn đúng file một lần để ghim Client."
                            )
                        raise RuntimeError(
                            "Không tìm được AXF/ELF khớp firmware đang chạy trong project đã lưu."
                        )
                    selected_symbols = selected.path
                    log("Auto-selected verified Client symbols: %s" % selected_symbols)
                state_after_match = tcl.wait_target_state()
                if state_after_match != state_before_match:
                    raise RuntimeError(
                        "Remote symbol matching changed target state unexpectedly: %s -> %s" %
                        (state_before_match, state_after_match)
                    )
                info = self.session.start_external(
                    symbol_file=selected_symbols,
                    gdb_host="127.0.0.1", gdb_port=local_gdb,
                    tcl_host="127.0.0.1", tcl_port=local_tcl,
                )
                state = self.session.target_poll()
                return tunnel, info, state, version
            except BaseException:
                try:
                    self.session.stop()
                finally:
                    tunnel.stop()
                raise

        self._begin_worker(
            operation, self._client_started,
            "Đang tự kết nối Gateway qua SSH...", self._start_failed,
        )

    def _client_started(self, result) -> None:
        tunnel, info, state, version = result
        assert isinstance(info, DebugSessionInfo)
        self._status_override = None
        self._client_tunnel = tunnel
        self._client_mode_active = True
        self._initial_target_state = info.initial_target_state
        self._set_target_state(state)
        self.log.emit(
            "CLIENT CONNECTED · %s · GDB %s · TCL %s · target initially %s." %
            (version, info.gdb_endpoint, info.tcl_endpoint, info.initial_target_state)
        )
        if info.symbols:
            self.symbol_path.setText(info.symbols)
            self.log.emit("Loaded verified local debug symbols: %s" % info.symbols)
        else:
            self.log.emit("Client connected without AXF/ELF; source-level names may be unavailable.")
        self._watchdog.start()
        self._save_debug_preferences()
        self._refresh_controls()

    def show_remote_vscode_dialog(self) -> None:
        if self._remote_vscode_dialog is None:
            self._remote_vscode_dialog = RemoteVsCodeDialog(self.selected_probe, self)
        self._remote_vscode_dialog.refresh_preview()
        self._remote_vscode_dialog.show()
        self._remote_vscode_dialog.raise_()
        self._remote_vscode_dialog.activateWindow()

    def start_remote_server(self) -> None:
        try:
            port = 3333
            self.gdb_port.setText(str(port))
            config = DebugConfig(
                self.selected_probe(), "127.0.0.1", port, None, 6666,
            )
            config.validate()
        except (ValueError, TypeError) as error:
            self._start_failed_message(str(error))
            return

        def operation(log, _phase, _cancel):
            guard_holder = [None]

            def server_log(line):
                log(line)
                guard = guard_holder[0]
                if guard is not None:
                    try:
                        guard.handle_openocd_line(line)
                    except Exception as error:
                        log("Remote debug guard warning: %s" % error)

            try:
                self.service.start(config, event_sink=server_log)
                tcl = self._tcl_factory(TclEndpoint("127.0.0.1", 6666))
                version = tcl.version()
                guard = RemoteDebugGuard(
                    tcl,
                    lambda event, message: log("Remote guard %s: %s" % (event, message)),
                )
                state = guard.capture_initial_state()
                guard_holder[0] = guard
                return tcl, guard, state, version
            except BaseException:
                self.service.stop()
                raise

        self._begin_worker(
            operation, self._remote_started,
            "Đang khởi động Debug Gateway...", self._start_failed,
        )

    def _remote_started(self, result) -> None:
        self._status_override = None
        tcl, guard, state, version = result
        self._remote_server_active = True
        self._remote_tcl = tcl
        self._remote_guard = guard
        self._initial_target_state = state
        self._set_target_state(state)
        self.log.emit(
            "GATEWAY READY: OpenOCD 127.0.0.1:%s · TCL loopback 6666 · initial target %s." %
            (self.gdb_port.text().strip(), state)
        )
        self.log.emit("TCL remains loopback-only on the Gateway; B300 Client may forward it only inside SSH. %s" % version)
        self._watchdog.start()
        self._refresh_controls()

    def start_debug(self) -> None:
        symbol_text = self.symbol_path.text().strip()
        symbols = Path(symbol_text).expanduser() if symbol_text else None
        if symbols is not None and not symbols.is_file():
            if self._symbol_root is not None and self._symbol_root.is_dir():
                self.log.emit("Stored AXF/ELF no longer exists; Local will fall back to project auto-match.")
                symbols = None
            else:
                self._start_failed_message("AXF/ELF đã chọn không tồn tại: %s" % symbols)
                return
        try:
            gdb_port = find_available_loopback_port(3333)
            tcl_port = find_available_loopback_port(6666, avoid=(gdb_port,))
            self.gdb_port.setText(str(gdb_port))
            config = DebugSessionConfig(
                probe=self.selected_probe(), symbol_file=None, bind_address="127.0.0.1",
                gdb_port=gdb_port, tcl_port=tcl_port,
            )
            config.validate()
        except (ValueError, TypeError, RuntimeError) as error:
            self._start_failed_message(str(error))
            return

        def operation(log, _phase, _cancel):
            try:
                info = self.session.start(config, event_sink=log)
                state_before_match = self.session.target_poll()
                if info.initial_target_state.lower() == "running" and state_before_match == "halted":
                    log("GDB attach halted a previously running target; restoring RUNNING state.")
                    state_before_match = self.session.continue_execution()
                selected_symbols = symbols
                if selected_symbols is not None:
                    selected, results = find_matching_symbol_file((selected_symbols,), self.session.read_words)
                    if selected is None:
                        detail = results[0].reason if results else "ELF/AXF could not be parsed or sampled"
                        raise RuntimeError("AXF/ELF đã chọn không khớp firmware đang chạy: %s" % detail)
                    selected_symbols = selected.path
                    log("Verified selected Local AXF/ELF against Application Flash: %s" % selected_symbols)
                elif self._symbol_root is not None and self._symbol_root.is_dir():
                    candidates = discover_symbol_files([self._symbol_root], max_files=128, max_depth=8)
                    if candidates:
                        selected, results = find_matching_symbol_file(candidates, self.session.read_words)
                        if selected is None:
                            exact_count = sum(1 for item in results if item.matched)
                            if exact_count > 1:
                                raise RuntimeError("Có nhiều AXF/ELF cùng khớp firmware; hãy chọn đúng file một lần để ghim Local Debug.")
                            raise RuntimeError("Không tìm được AXF/ELF khớp firmware đang chạy trong project đã lưu.")
                        selected_symbols = selected.path
                        log("Auto-selected verified Local symbols: %s" % selected_symbols)
                if selected_symbols is not None:
                    self.session.load_symbols(selected_symbols)
                state_after_match = self.session.target_poll()
                if state_after_match != state_before_match:
                    raise RuntimeError(
                        "Local symbol matching changed target state unexpectedly: %s -> %s" %
                        (state_before_match, state_after_match)
                    )
                return info, state_after_match, selected_symbols
            except BaseException:
                self.session.stop()
                raise

        self._begin_worker(
            operation, self._started, "Đang tự kết nối ST-Link và xác minh firmware...",
            self._start_failed,
        )

    def _started(self, result) -> None:
        self._status_override = None
        info, state, selected_symbols = result
        assert isinstance(info, DebugSessionInfo)
        self._initial_target_state = info.initial_target_state
        self._set_target_state(state)
        if selected_symbols is not None:
            self.symbol_path.setText(str(selected_symbols))
            self.log.emit("Loaded verified debug symbols: %s" % selected_symbols)
        else:
            self.log.emit("Local connected without AXF/ELF; source-level names may be unavailable.")
        self.log.emit(
            "LOCAL CONNECTED: GDB %s · TCL %s · initial target %s." %
            (info.gdb_endpoint, info.tcl_endpoint, info.initial_target_state)
        )
        self._watchdog.start()
        self._save_debug_preferences()
        self._refresh_controls()

    def _start_failed(self, failure: WorkerFailure) -> None:
        self._watchdog.stop()
        self._target_state = None
        self._initial_target_state = None
        self._remote_server_active = False
        self._remote_tcl = None
        self._remote_guard = None
        if self._client_tunnel is not None:
            try:
                self._client_tunnel.stop()
            except Exception:
                pass
        self._client_tunnel = None
        self._client_mode_active = False
        self._start_failed_message(failure.message)

    def _start_failed_message(self, message: str) -> None:
        self.log.emit("Debug start failed: %s" % message)
        self.operation_state_changed.emit(False)
        self._status_override = ("Không thể bắt đầu Debug: %s" % message, "failed")
        self._refresh_controls()

    def _operation_failed(self, failure: WorkerFailure) -> None:
        self.log.emit("GDB operation failed: %s" % failure.message)
        try:
            self._target_state = self.session.target_poll() if self.session.active else None
        except Exception as error:
            self._target_state = None
            self.log.emit("Unable to verify target state after failure: %s" % error)
        self._status_override = ("Debug operation failed: %s" % failure.message, "failed")
        self._refresh_controls()

    def _poll_debug_service(self) -> None:
        if self._client_mode_active and self._client_tunnel is not None and not self._client_tunnel.active:
            self._watchdog.stop()
            try:
                self.session.stop()
            except Exception as error:
                self.log.emit("Client cleanup after SSH tunnel loss: %s" % error)
            self._client_tunnel = None
            self._client_mode_active = False
            self._target_state = None
            self._initial_target_state = None
            self._status_override = (
                "Mất SSH tunnel tới Gateway · kiểm tra mạng/SSH rồi bấm Kết nối lại", "failed",
            )
            self.operation_state_changed.emit(False)
            self._refresh_controls()
            return
        if not self._client_mode_active and self.service.state == DebugState.FAILED:
            self._watchdog.stop()
            try:
                self.session.stop()
            except Exception as error:
                self.log.emit("Debug cleanup after OpenOCD failure: %s" % error)
            self._target_state = None
            self._initial_target_state = None
            self._remote_server_active = False
            self._remote_tcl = None
            self._remote_guard = None
            self.log.emit("OpenOCD exited unexpectedly; hardware interlock released.")
            self.operation_state_changed.emit(False)
            self._set_target_state(None)
            self._refresh_controls()
            return
        if self.session.active and self._worker is None:
            try:
                state = self.session.target_poll()
            except Exception as error:
                self.log.emit("Target-state poll failed: %s" % error)
                self._target_state = None
            else:
                if state != self._target_state:
                    self.log.emit("Target state changed: %s" % state.upper())
                self._target_state = state
            self._refresh_controls()
        elif self._remote_server_active and self._remote_tcl is not None and self._worker is None:
            try:
                state = self._remote_tcl.target_state()
            except Exception as error:
                self.log.emit("Remote target-state poll failed: %s" % error)
                self._target_state = None
            else:
                if state != self._target_state:
                    self.log.emit("Remote target state changed: %s" % state.upper())
                self._target_state = state
            self._refresh_controls()

    def halt_target(self) -> None:
        self._run_control("Halt", self.session.halt)

    def continue_target(self) -> None:
        self._run_control("Continue", self.session.continue_execution)

    def reset_halt_target(self) -> None:
        self._run_control("Reset + Halt", self.session.reset_halt)

    def step_into_target(self) -> None:
        self._run_control("Step Into", self.session.step_once)

    def step_over_target(self) -> None:
        self._run_control("Step Over", self.session.next_once)

    def _run_control(self, label: str, command) -> None:
        if not self.session.active:
            self._operation_failed_message("GDB chưa ở trạng thái CONNECTED.")
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: command(),
            lambda state, action=label: self._control_completed(action, state),
            "Đang thực hiện %s..." % label,
        )

    def _operation_failed_message(self, message: str) -> None:
        self.log.emit("Debug operation failed: %s" % message)
        self._status_override = ("Debug lỗi: %s" % message, "failed")
        self._refresh_controls()

    def _control_completed(self, label: str, state: str) -> None:
        self._status_override = None
        self._set_target_state(state)
        self.interactive_panel.set_last_action(label)
        self.log.emit("GDB control completed: %s · target=%s" % (label, state.upper()))
        self._refresh_controls()

    @staticmethod
    def _format_frame(frame) -> str:
        address = getattr(frame, "address", None) or "?"
        function = getattr(frame, "function", None) or "?"
        file_name = getattr(frame, "file", None) or getattr(frame, "fullname", None) or "?"
        line = getattr(frame, "line", None)
        return "%s  %s  %s:%s" % (
            address, function, file_name, line if line is not None else "?",
        )

    def _run_diagnostic(self, label: str, operation, formatter) -> None:
        if not self.session.active:
            self._operation_failed_message("Debug session chưa CONNECTED.")
            return

        def execute(_log, _phase, _cancel):
            result = operation()
            state = self.session.target_poll()
            return result, state

        self._begin_worker(
            execute,
            lambda result, name=label, render=formatter: self._diagnostic_completed(
                name, result[0], result[1], render,
            ),
            "Đang đọc %s..." % label,
        )

    def _diagnostic_completed(self, label: str, result, state: str, formatter) -> None:
        self._status_override = None
        self._set_target_state(state)
        text = formatter(result)
        self.interactive_panel.set_diagnostic_result(label, text, state)
        self.log.emit("Diagnostic completed: %s · target restored=%s" % (label, state.upper()))
        self._refresh_controls()

    def inspect_where(self) -> None:
        self._run_diagnostic(
            "Where", self.session.capture_where, self._format_frame,
        )

    def inspect_stack(self) -> None:
        self._run_diagnostic(
            "Call Stack", lambda: self.session.capture_stack(12),
            lambda frames: "\n".join(
                "#%d  %s" % (index, self._format_frame(frame))
                for index, frame in enumerate(frames)
            ) or "Không có stack frame.",
        )

    def inspect_registers(self) -> None:
        self._run_diagnostic(
            "Registers", self.session.capture_registers,
            lambda registers: "\n".join(
                "%s = %s" % (getattr(item, "name", "reg?"), getattr(item, "value", "?"))
                for item in registers
            ) or "Không có register value.",
        )

    def inspect_variable(self) -> None:
        expression = self.variable_expression.text().strip()
        if not expression:
            self._operation_failed_message("Hãy nhập tên biến cần đọc.")
            return
        self._run_diagnostic(
            "Variable", lambda: self.session.capture_variable(expression),
            lambda value: "%s = %s" % (
                getattr(value, "expression", expression), getattr(value, "value", value),
            ),
        )

    def _live_sampling_expressions(self):
        return self.live_panel.watch_specs()

    def _build_live_monitor_config(self, watch_specs):
        role = self._resolved_role()
        if role == "gateway":
            raise ValueError("Realtime Live Monitor uses Local or Client mode; switch from Gateway first.")
        interval = float(self.sample_interval.value())
        samples = int(self.sample_cycles.value())
        symbol_text = self.symbol_path.text().strip()
        symbols = Path(symbol_text).expanduser().resolve() if symbol_text else None
        if role == "local":
            if symbols is None or not symbols.is_file():
                raise ValueError("Local Live Monitor requires a verified AXF/ELF symbol file.")
            return role, LocalLiveMonitorConfig(
                probe=self.selected_probe(), symbols=symbols, interval_seconds=interval,
                sample_limit=samples, watch_specs=tuple(watch_specs), tcl_port=6666,
            )
        host = self.client_host.text().strip()
        user = self.client_user.text().strip()
        if not host or not user:
            raise ValueError("Client Live Monitor requires Gateway host and SSH user.")
        if symbols is not None and not symbols.is_file():
            symbols = None
        roots = ()
        if symbols is None and self._symbol_root is not None and self._symbol_root.is_dir():
            roots = (self._symbol_root,)
        if symbols is None and not roots:
            raise ValueError("Client Live Monitor requires AXF/ELF or a saved project symbol root.")
        return role, ClientLiveMonitorConfig(
            host=host, user=user, symbols=symbols, interval_seconds=interval,
            sample_limit=samples, watch_specs=tuple(watch_specs),
            ssh_port=self.client_ssh_port.value(), symbol_roots=roots,
        )

    def start_live_sampling(self) -> None:
        if self._worker is not None or self.session.active or self.service.state in (
            DebugState.STARTING, DebugState.READY, DebugState.CONNECTED,
        ):
            self._operation_failed_message(
                "Stop Interactive Debug/Gateway before starting non-halting Live Monitor."
            )
            return
        if self._client_tunnel is not None and self._client_tunnel.active:
            self._operation_failed_message("Disconnect Interactive Client before starting Live Monitor.")
            return
        try:
            watch_specs = self._live_sampling_expressions()
            role, config = self._build_live_monitor_config(watch_specs)
        except (ValueError, RuntimeError) as error:
            self._operation_failed_message(str(error))
            return
        self.live_panel.reset_for_sampling()
        self.plot_panel.clear()
        self._sampling_active = True
        live = self._live_session_factory(openocd_executable=str(self.service.executable))
        self._live_session = live

        def execute(log, phase, _cancel_event):
            try:
                info = live.start_local(config) if role == "local" else live.start_client(config)
                log(
                    "LIVE MONITOR CONNECTED: role=%s transport=%s TCL=%s target=%s" %
                    (info.role, info.transport, info.tcl_endpoint, info.initial_target_state.upper())
                )
                summary = live.run(phase)
                analytics = live.analytics_snapshot()
                return summary, analytics, info
            finally:
                live.close()

        worker = self._begin_worker(
            execute, self._live_sampling_completed, "LIVE MONITOR · NON-HALTING...",
            failed=self._live_sampling_failed, phase_handler=self._live_sampling_cycle,
        )
        if worker is None:
            self._sampling_active = False
            self._live_session = None
            live.close()
            self._refresh_controls()

    def stop_live_sampling(self) -> None:
        if not self._sampling_active:
            return
        self.live_panel.mark_stopping()
        if self._live_session is not None:
            self._live_session.cancel()
        if self._worker is not None:
            self._worker.cancel()

    def _live_sampling_cycle(self, sample) -> None:
        if not isinstance(sample, LiveSample):
            return
        self.live_panel.append_live_sample(sample)
        live = self._live_session
        if live is not None:
            try:
                self.live_panel.apply_analytics(live.analytics_snapshot())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                # Analytics are presentation-only; never fail an otherwise valid live session.
                pass
        self.plot_panel.set_samples(self._sample_buffer.snapshot())

    def _live_sampling_completed(self, result) -> None:
        summary, analytics, info = result
        self._sampling_active = False
        self._live_session = None
        self._target_state = None
        self._status_override = (
            "LIVE COMPLETE · TARGET %s" % str(summary.final_target_state).upper(), "ready",
        )
        self.live_panel.apply_analytics(analytics)
        self.live_panel.mark_live_completed(summary)
        self.plot_panel.set_samples(self._sample_buffer.snapshot())
        top = analytics.functions[:5]
        if top:
            self.log.emit(
                "Live execution hits: " + ", ".join(
                    "%s=%d(%.0f%%)" % (item.function, item.samples, item.share * 100.0) for item in top
                )
            )
        self.log.emit(
            "Live Monitor completed: role=%s samples=%d overruns=%d target=%s read_mean=%.1fms lag_max=%.1fms" %
            (info.role, summary.samples, summary.overruns, summary.final_target_state.upper(),
             analytics.timing.mean_read_duration_seconds * 1000.0,
             analytics.timing.max_schedule_lag_seconds * 1000.0)
        )
        self._refresh_controls()

    def _live_sampling_failed(self, failure: WorkerFailure) -> None:
        self._sampling_active = False
        self._live_session = None
        self._target_state = None
        self.live_panel.mark_failed(failure.message)
        self.log.emit("Live Monitor failed: %s" % failure.message)
        self._status_override = ("LIVE MONITOR FAILED · %s" % failure.message, "failed")
        self._refresh_controls()

    def clear_live_samples(self) -> None:
        if self._sampling_active:
            return
        self.live_panel.clear_history()
        self.plot_panel.clear()
        self._refresh_controls()

    def export_live_samples(self) -> None:
        try:
            saved = self.live_panel.export_samples(self)
        except Exception as error:
            self._operation_failed_message(str(error))
            return
        if saved is None:
            return
        self.log.emit("Live sampling exported: %s" % saved)
        self._refresh_controls()

    def _format_stop_snapshot(self, snapshot) -> str:
        lines = [
            "%s #%s · %s" % (snapshot.kind, snapshot.number, snapshot.reason),
            "Location: %s" % snapshot.location,
            "Frame: %s" % self._format_frame(snapshot.frame),
        ]
        if getattr(snapshot, "value", None) is not None:
            value = snapshot.value
            lines.append("Value: %s = %s" % (
                getattr(value, "expression", snapshot.location),
                getattr(value, "value", value),
            ))
        lines.append("Resource deleted automatically; target restored to RUNNING.")
        return "\n".join(lines)

    def _run_one_shot(self, label: str, operation) -> None:
        if not self.session.active:
            self._operation_failed_message("Debug session chưa CONNECTED.")
            return
        if self._target_state != "running":
            self._operation_failed_message("Break/Watch Once chỉ chạy khi target đang RUNNING.")
            return

        def execute(_log, _phase, _cancel):
            result = operation()
            state = self.session.target_poll()
            return result, state

        self._begin_worker(
            execute,
            lambda result, name=label: self._diagnostic_completed(
                name, result[0], result[1], self._format_stop_snapshot,
            ),
            "Đang chờ %s..." % label,
        )

    def break_once(self) -> None:
        location = self.break_location.text().strip()
        if not location:
            self._operation_failed_message("Hãy nhập hàm hoặc file.c:line cho breakpoint.")
            return
        timeout = float(self.stop_timeout.value())
        self._run_one_shot(
            "Hardware Break Once", lambda: self.session.break_once(location, timeout_seconds=timeout),
        )

    def watch_once(self) -> None:
        expression = self.watch_expression.text().strip()
        if not expression:
            self._operation_failed_message("Hãy nhập biến cần watch.")
            return
        timeout = float(self.stop_timeout.value())
        self._run_one_shot(
            "Hardware Watch Once", lambda: self.session.watch_once(expression, timeout_seconds=timeout),
        )

    def stop_debug(self) -> None:
        if self._worker is not None or not self.has_active_operation:
            return
        gateway_mode = self._remote_server_active
        client_mode = self._client_mode_active

        def operation(log, _phase, _cancel):
            before = None
            if gateway_mode:
                if self._remote_tcl is not None:
                    before = self._remote_tcl.target_state()
                if self._remote_guard is not None:
                    snapshot = self._remote_guard.restore_initial_state(reason="gateway_gui_shutdown")
                    log(
                        "Gateway guard restore: initial=%s final=%s restored=%s" %
                        (snapshot.initial_target_state, snapshot.final_target_state, snapshot.restored)
                    )
                self.service.stop()
                log("Gateway stopped; GDB/TCL endpoints released.")
            elif client_mode:
                if self.session.active:
                    try:
                        before = self.session.target_poll()
                    except Exception:
                        pass
                self.session.stop()
                if self._client_tunnel is not None:
                    self._client_tunnel.stop()
                log("Client disconnected safely; target restoration ran before SSH tunnel close.")
            else:
                if self.session.active:
                    try:
                        before = self.session.target_poll()
                    except Exception:
                        pass
                self.session.stop()
                log("Local debug stopped; initial target state restoration attempted.")
            return before

        self._begin_worker(
            operation, self._stopped, "Đang dừng Debug an toàn...",
            self._remote_stop_failed if gateway_mode else None,
        )

    def _remote_stop_failed(self, failure: WorkerFailure) -> None:
        self.log.emit("Gateway stop failed: %s" % failure.message)
        self._status_override = ("Gateway vẫn chạy · %s" % failure.message, "failed")
        self._refresh_controls()

    def _stopped(self, _before) -> None:
        self._status_override = None
        self._watchdog.stop()
        self._target_state = None
        self._initial_target_state = None
        self._remote_server_active = False
        self._remote_tcl = None
        self._remote_guard = None
        if self._client_tunnel is not None:
            try:
                self._client_tunnel.stop()
            except Exception:
                pass
        self._client_tunnel = None
        self._client_mode_active = False
        self.log.emit("Debug session stopped and endpoints released.")
        self.operation_state_changed.emit(False)
        self._set_target_state(None)
        self._refresh_controls()

    def prepare_shutdown(self) -> bool:
        """Stop GUI-owned timers/workers on the GUI thread before Qt destroys children."""
        self._watchdog.stop()
        if self._live_session is not None:
            self._live_session.cancel()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            if not worker.wait(3000):
                return False
        if self._live_session is not None:
            try:
                self._live_session.close()
            except Exception:
                pass
        self._live_session = None
        self._sampling_active = False
        self._worker = None
        self._retired_workers.clear()
        return True

    def closeEvent(self, event) -> None:
        if not self.prepare_shutdown():
            event.ignore()
            return
        super().closeEvent(event)
