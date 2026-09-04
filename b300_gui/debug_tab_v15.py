"""v0.15 integrated Debug Studio using the shared backend workstation facade.

This class intentionally layers over the v0.14-compatible DebugTab instead of
rewriting it.  Normal v0.15 Local/Client flows use DebugWorkstationController;
legacy ssh.exe tunnel code remains available only through the base class for
backward compatibility and is never selected by this subclass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog

from b300_core.client_symbols import verify_client_symbols
from b300_core.debug_session import DebugSessionConfig, DebugSessionInfo
from b300_core.debug_workstation import DebugWorkstationController
from b300_core.elf_matcher import discover_symbol_files, find_matching_symbol_file
from b300_core.live_session import ClientLiveMonitorConfig
from b300_core.remote_profile import RemoteGatewayProfile

from .debug_tab import DebugTab
from .debug_view_models import (
    DebugBreakpoint,
    DebugConnectionState,
    DebugFrame,
    DebugRegister,
    DebugVariableNode,
)
from .remote_login_dialog import RemoteLoginDialog
from .workers import WorkerFailure


def _hex_address(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, int):
        return "0x%08X" % value
    text = str(value)
    return text


def _gui_variable(node) -> DebugVariableNode:
    return DebugVariableNode(
        id=str(node.id),
        name=str(node.name),
        value=str(node.value or ""),
        type=str(node.type or ""),
        address=str(node.address or ""),
        editable=bool(node.editable),
        has_children=bool(node.has_children),
        children_loaded=bool(node.children_loaded),
        changed=bool(node.changed),
        in_scope=bool(node.in_scope),
    )


def _gui_frame(frame) -> DebugFrame:
    return DebugFrame(
        level=int(getattr(frame, "level", 0)),
        function=str(getattr(frame, "function", "") or ""),
        file=str(getattr(frame, "fullname", "") or getattr(frame, "file", "") or ""),
        line=int(getattr(frame, "line", 0) or 0),
        address=_hex_address(getattr(frame, "address", None)),
    )


def _gui_register(register) -> DebugRegister:
    return DebugRegister(
        name=str(register.name),
        value=str(register.value),
        changed=bool(getattr(register, "changed", False)),
    )


def _gui_breakpoint(bp) -> DebugBreakpoint:
    return DebugBreakpoint(
        number=int(bp.number),
        enabled=bool(bp.enabled),
        kind=str(bp.kind),
        location=str(bp.location),
        address=str(bp.address or ""),
        hit_count=int(getattr(bp, "hit_count", 0) or 0),
    )


class DebugTabV15(DebugTab):
    """Engineering Debug Studio with one-login Client SSH and structured panes."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._workstation_controller = DebugWorkstationController(debug_session=self.session)
        self._v15_snapshot_applying = False
        self._v15_last_target_state: Optional[str] = None
        self._v15_watches = {}

        # Replace the panel-owned login launcher so remembered credentials can be
        # advertised before the dialog is shown. Password text never enters QSettings.
        try:
            self.conn_panel.btn_open_login_dialog.clicked.disconnect()
        except Exception:
            pass
        self.conn_panel.btn_open_login_dialog.clicked.connect(self._open_client_login_dialog_v15)

        variables = self.workstation.variables_pane
        variables.add_watch_requested.connect(self._v15_add_watch)
        variables.variable_write_requested.connect(self._v15_assign_variable)

        breakpoints = self.workstation.breakpoints_pane
        breakpoints.add_requested.connect(self._v15_add_breakpoint)
        breakpoints.toggle_requested.connect(self._v15_toggle_breakpoint)
        breakpoints.delete_requested.connect(self._v15_delete_breakpoint)

        self.workstation.btn_read_memory.clicked.connect(self._v15_read_memory)

        # User requirement: every new Debug visit begins with an explicit role choice.
        # Saved host/user/port remain populated after selecting CLIENT.
        self.main_stack.setCurrentWidget(self.mode_selector)
        self._sync_workstation_state()

    # ------------------------------------------------------------------
    # Remote login/session ownership
    # ------------------------------------------------------------------
    def _remote_profile(self, host: Optional[str] = None, user: Optional[str] = None,
                        port: Optional[int] = None) -> RemoteGatewayProfile:
        return RemoteGatewayProfile(
            host if host is not None else self.client_host.text().strip(),
            user if user is not None else self.client_user.text().strip(),
            int(port if port is not None else self.client_ssh_port.value()),
        ).validate()

    def _ensure_remote_session(self, profile: RemoteGatewayProfile):
        current = self._workstation_controller.remote_session
        if current is not None and current.profile == profile:
            return current
        if current is not None:
            if self._workstation_controller.interactive_active or self._workstation_controller.live_active:
                raise RuntimeError("Stop Debug/Live before changing the Gateway endpoint.")
            current.disconnect()
        return self._workstation_controller.configure_internal_remote(profile)

    def _open_client_login_dialog_v15(self) -> None:
        dialog = RemoteLoginDialog(
            default_host=self.client_host.text().strip(),
            default_user=self.client_user.text().strip(),
            default_port=self.client_ssh_port.value(),
            parent=self.window(),
        )
        self.conn_panel.login_dialog = dialog
        try:
            profile = self._remote_profile(
                dialog.host_input.text().strip(),
                dialog.user_input.text().strip(),
                dialog.port_input.value(),
            )
            self._ensure_remote_session(profile)
            dialog.set_has_remembered_credential(
                self._workstation_controller.has_remembered_remote_password()
            )
        except Exception:
            pass
        dialog.login_requested.connect(self.conn_panel.client_login_requested.emit)
        dialog.exec()

    def _on_client_login_requested(
        self, host: str, user: str, password: str, port: int, remember: bool
    ) -> None:
        self.client_host.setText(host)
        self.client_user.setText(user)
        self.client_ssh_port.setValue(port)
        self._save_debug_preferences()
        dialog = getattr(self.conn_panel, "login_dialog", None)
        if dialog is not None:
            dialog.set_connecting(True)
        try:
            profile = self._remote_profile(host, user, port)
            self._ensure_remote_session(profile)
        except Exception as error:
            if dialog is not None:
                dialog.set_login_error(str(error))
            return

        secret = password if password else None

        def operation(_log, _phase, _cancel):
            return self._workstation_controller.remote_login(
                secret, remember=remember, timeout_seconds=30.0,
            )

        self._begin_worker(
            operation,
            self._v15_login_completed,
            "SSH CONNECTING...",
            failed=self._v15_login_failed,
        )

    def _v15_login_completed(self, state) -> None:
        self._status_override = None
        self._client_mode_active = False
        dialog = getattr(self.conn_panel, "login_dialog", None)
        if dialog is not None:
            dialog.set_login_success()
        self.status_label.setText("SSH CONNECTED")
        self.status_label.setProperty("state", "ready")
        self.log.emit("SSH CONNECTED: %s" % state.endpoint)
        self._save_debug_preferences()
        self._sync_workstation_state()
        self._refresh_controls()

    def _v15_login_failed(self, failure: WorkerFailure) -> None:
        dialog = getattr(self.conn_panel, "login_dialog", None)
        if dialog is not None:
            dialog.set_login_error(failure.message)
        self._status_override = ("SSH ERROR · %s" % failure.message, "failed")
        self.log.emit("SSH login failed: %s" % failure.message)
        self._refresh_controls()

    def _client_symbol_inputs(self):
        symbol_text = self.symbol_path.text().strip()
        exact = Path(symbol_text).expanduser() if symbol_text else None
        roots = ()
        if exact is None and self._symbol_root is not None and self._symbol_root.is_dir():
            roots = (self._symbol_root,)
        return exact, roots

    # ------------------------------------------------------------------
    # Client Interactive Debug - no ssh.exe / no second password prompt
    # ------------------------------------------------------------------
    def start_client_debug(self) -> None:
        try:
            profile = self._remote_profile()
            remote = self._ensure_remote_session(profile)
        except Exception as error:
            self._start_failed_message(str(error))
            return

        if not remote.connected:
            if self._workstation_controller.has_remembered_remote_password():
                self._on_client_login_requested(
                    profile.host, profile.user, "", profile.port, True,
                )
            else:
                self._open_client_login_dialog_v15()
            return

        exact, roots = self._client_symbol_inputs()

        def operation(log, _phase, _cancel):
            selected = verify_client_symbols(
                remote, symbol_file=exact, symbol_roots=roots,
            )
            if selected is not None:
                log("AXF/ELF VERIFIED: %s" % selected)
            info = self._workstation_controller.start_client(selected)
            state = self._workstation_controller.connection_state()
            symbols = self._v15_load_symbol_catalog(selected)
            return info, state, selected, symbols

        self._begin_worker(
            operation,
            self._v15_client_started,
            "CLIENT DEBUG CONNECTING...",
            failed=self._v15_client_start_failed,
        )

    def _v15_load_symbol_catalog(self, selected: Optional[Path]):
        if selected is None:
            return ()
        browser = self._workstation_controller.open_symbol_browser(selected)
        rows = list(browser.functions("", limit=256))
        rows.extend(browser.data_symbols("", limit=256))
        return tuple({
            "name": item.name,
            "address": item.address,
            "size": item.size,
            "kind": item.kind,
            "category": item.category,
        } for item in rows)

    def _v15_client_started(self, result) -> None:
        info, state, selected, symbols = result
        assert isinstance(info, DebugSessionInfo)
        self._status_override = None
        self._client_mode_active = True
        self._client_tunnel = None
        self._initial_target_state = info.initial_target_state
        self._set_target_state(state.target)
        if selected is not None:
            self.symbol_path.setText(str(selected))
        self.workstation.symbols_pane.set_symbols(symbols)
        self.log.emit(
            "CLIENT DEBUG CONNECTED · GDB %s · TCL %s · SSH session reused." %
            (info.gdb_endpoint, info.tcl_endpoint)
        )
        self._watchdog.start()
        self._save_debug_preferences()
        self.show_workstation()
        self._sync_workstation_state()
        if state.target == "halted":
            QTimer.singleShot(0, self._v15_capture_snapshot)
        self._refresh_controls()

    def _v15_client_start_failed(self, failure: WorkerFailure) -> None:
        # Keep an authenticated SSH session alive so the user can correct AXF/ELF
        # or GDB configuration and retry without typing the password again.
        try:
            self._workstation_controller.stop_interactive()
        except Exception:
            pass
        self._client_mode_active = False
        self._status_override = ("CLIENT DEBUG ERROR · %s" % failure.message, "failed")
        self.log.emit("Client Debug start failed: %s" % failure.message)
        self._refresh_controls()

    # ------------------------------------------------------------------
    # Local Interactive Debug through the same workstation facade
    # ------------------------------------------------------------------
    def start_debug(self) -> None:
        symbol_text = self.symbol_path.text().strip()
        exact = Path(symbol_text).expanduser() if symbol_text else None
        if exact is not None and not exact.is_file():
            exact = None
        try:
            from b300_core.ssh_debug_tunnel import find_available_loopback_port
            gdb_port = find_available_loopback_port(3333)
            tcl_port = find_available_loopback_port(6666, avoid=(gdb_port,))
            config = DebugSessionConfig(
                probe=self.selected_probe(), symbol_file=None,
                bind_address="127.0.0.1", gdb_port=gdb_port, tcl_port=tcl_port,
            )
            config.validate()
        except Exception as error:
            self._start_failed_message(str(error))
            return

        def operation(log, _phase, _cancel):
            info = self._workstation_controller.start_local(config)
            state_before = self.session.target_poll()
            selected = exact
            if selected is not None:
                matched, results = find_matching_symbol_file((selected,), self.session.read_words)
                if matched is None:
                    detail = results[0].reason if results else "AXF/ELF could not be sampled"
                    raise RuntimeError("Selected AXF/ELF does not match running firmware: %s" % detail)
                selected = matched.path
            elif self._symbol_root is not None and self._symbol_root.is_dir():
                candidates = discover_symbol_files((self._symbol_root,), max_files=128, max_depth=8)
                if candidates:
                    matched, results = find_matching_symbol_file(candidates, self.session.read_words)
                    if matched is None:
                        exact_count = sum(1 for item in results if item.matched)
                        if exact_count > 1:
                            raise RuntimeError("Multiple AXF/ELF files match firmware; select one explicitly.")
                        raise RuntimeError("No AXF/ELF under the project matches running firmware.")
                    selected = matched.path
            if selected is not None:
                self.session.load_symbols(selected)
                self._workstation_controller._symbols = str(selected)
                log("AXF/ELF VERIFIED: %s" % selected)
            state_after = self.session.target_poll()
            if state_after != state_before:
                raise RuntimeError("Symbol verification changed target state unexpectedly.")
            symbols = self._v15_load_symbol_catalog(selected)
            return info, state_after, selected, symbols

        self._begin_worker(
            operation,
            self._v15_local_started,
            "LOCAL DEBUG CONNECTING...",
            failed=self._v15_local_start_failed,
        )

    def _v15_local_started(self, result) -> None:
        info, state, selected, symbols = result
        self._status_override = None
        self._client_mode_active = False
        self._initial_target_state = info.initial_target_state
        self._set_target_state(state)
        if selected is not None:
            self.symbol_path.setText(str(selected))
        self.workstation.symbols_pane.set_symbols(symbols)
        self.log.emit("LOCAL DEBUG CONNECTED · GDB %s · TCL %s" % (info.gdb_endpoint, info.tcl_endpoint))
        self._watchdog.start()
        self._save_debug_preferences()
        self.show_workstation()
        self._sync_workstation_state()
        if state == "halted":
            QTimer.singleShot(0, self._v15_capture_snapshot)
        self._refresh_controls()

    def _v15_local_start_failed(self, failure: WorkerFailure) -> None:
        try:
            self._workstation_controller.stop_interactive()
        except Exception:
            pass
        self._start_failed_message(failure.message)

    # ------------------------------------------------------------------
    # Structured workstation state/panes
    # ------------------------------------------------------------------
    def _sync_workstation_state(self) -> None:
        controller = getattr(self, "_workstation_controller", None)
        if controller is None:
            return super()._sync_workstation_state()
        try:
            state = controller.connection_state()
        except Exception:
            return super()._sync_workstation_state()
        mode = state.mode if state.mode in {"local", "client"} else self._resolved_role()
        pc = _hex_address(state.pc) or "—"
        gui = DebugConnectionState(
            mode=mode,
            ssh=state.ssh == "connected",
            gdb=state.gdb == "connected",
            tcl=state.tcl == "connected",
            target=str(state.target or "disconnected").upper(),
            pc=pc,
            sample_rate=("%g Hz" % state.sample_rate_hz) if state.sample_rate_hz else "—",
            error_state=None,
        )
        self.workstation.update_connection_state(gui)
        self.workstation.btn_read_memory.setEnabled(
            state.gdb == "connected" and state.target == "halted"
        )

    def _v15_capture_snapshot(self) -> None:
        if self._worker is not None or not self._workstation_controller.interactive_active:
            return
        try:
            if self._workstation_controller.connection_state().target != "halted":
                return
        except Exception:
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: self._workstation_controller.capture_halted(max_frames=16),
            self._v15_apply_snapshot,
            "DEBUG SNAPSHOT...",
        )

    def _v15_apply_snapshot(self, snapshot) -> None:
        self._v15_snapshot_applying = True
        try:
            frames = [_gui_frame(frame) for frame in snapshot.frames]
            self.workstation.callstack_pane.set_frames(frames)
            roots = [_gui_variable(node) for node in snapshot.locals]
            roots.extend(_gui_variable(node) for node in self._v15_watches.values())
            self.workstation.variables_pane.set_variables(roots)
            self.workstation.registers_pane.set_registers(
                [_gui_register(item) for item in snapshot.registers]
            )
            self.workstation.breakpoints_pane.set_breakpoints(
                [_gui_breakpoint(item) for item in snapshot.breakpoints]
            )
            usage = snapshot.breakpoint_usage
            self.workstation.breakpoints_pane.update_usage(
                usage.breakpoints, usage.breakpoint_limit,
                usage.watchpoints, usage.watchpoint_limit,
            )
            loc = snapshot.location
            self._last_pc = loc.address
            self.workstation.source_view.show_location(
                str(loc.fullname or loc.file or ""), int(loc.line or 0),
                _hex_address(loc.address), function=str(loc.function or ""),
            )
        finally:
            self._v15_snapshot_applying = False
        self._sync_workstation_state()
        self._refresh_controls()

    def select_stack_frame(self, level: int) -> None:
        if self._v15_snapshot_applying or self._worker is not None:
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: self._workstation_controller.select_frame_and_capture(level),
            self._v15_apply_snapshot,
            "FRAME %d..." % int(level),
        )

    def request_variable_children(self, variable_id: str) -> None:
        if self._worker is not None:
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: self._workstation_controller.list_variable_children(variable_id),
            lambda rows, parent=variable_id: self.workstation.variables_pane.insert_children(
                parent, [_gui_variable(item) for item in rows]
            ),
            "EXPAND VARIABLE...",
        )

    def _v15_add_watch(self, expression: str) -> None:
        if self._worker is not None:
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: self._workstation_controller.create_watch(expression),
            self._v15_watch_created,
            "ADD WATCH...",
        )

    def _v15_watch_created(self, node) -> None:
        self._v15_watches[node.id] = node
        roots = list(getattr(self.workstation.variables_pane.model, "_root_nodes", ()))
        roots.append(_gui_variable(node))
        self.workstation.variables_pane.set_variables(roots)

    def _v15_assign_variable(self, variable_id: str, _name: str, value: str) -> None:
        if self._worker is not None:
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: self._workstation_controller.assign_variable(variable_id, value),
            lambda _value: QTimer.singleShot(0, self._v15_capture_snapshot),
            "WRITE VARIABLE...",
            failed=self._operation_failed,
        )

    def _v15_refresh_breakpoints(self) -> None:
        if self._worker is not None:
            return
        def operation(_log, _phase, _cancel):
            return (
                self._workstation_controller.list_breakpoints(),
                self._workstation_controller.breakpoint_usage(),
            )
        def completed(result):
            rows, usage = result
            self.workstation.breakpoints_pane.set_breakpoints([_gui_breakpoint(row) for row in rows])
            self.workstation.breakpoints_pane.update_usage(
                usage.breakpoints, usage.breakpoint_limit, usage.watchpoints, usage.watchpoint_limit,
            )
        self._begin_worker(operation, completed, "BREAKPOINTS...")

    def _v15_add_breakpoint(self) -> None:
        if not self._workstation_controller.interactive_active:
            return
        kinds = ("Hardware Breakpoint", "Watchpoint")
        kind, ok = QInputDialog.getItem(self, "Add Debug Resource", "Type", kinds, 0, False)
        if not ok:
            return
        label = "Function or file.c:line" if kind == kinds[0] else "Variable expression"
        value, ok = QInputDialog.getText(self, "Add Debug Resource", label)
        if not ok or not value.strip():
            return
        operation = (
            (lambda: self._workstation_controller.create_hardware_breakpoint(value.strip()))
            if kind == kinds[0]
            else (lambda: self._workstation_controller.create_watchpoint(value.strip()))
        )
        self._begin_worker(
            lambda _log, _phase, _cancel: operation(),
            lambda _number: QTimer.singleShot(0, self._v15_refresh_breakpoints),
            "ADD BREAKPOINT...",
        )

    def _v15_toggle_breakpoint(self, number: int, enabled: bool) -> None:
        self._begin_worker(
            lambda _log, _phase, _cancel: self._workstation_controller.set_breakpoint_enabled(number, enabled),
            lambda _result: QTimer.singleShot(0, self._v15_refresh_breakpoints),
            "BREAKPOINT STATE...",
        )

    def _v15_delete_breakpoint(self, number: int) -> None:
        self._begin_worker(
            lambda _log, _phase, _cancel: self._workstation_controller.delete_breakpoint(number),
            lambda _result: QTimer.singleShot(0, self._v15_refresh_breakpoints),
            "DELETE BREAKPOINT...",
        )

    def _v15_read_memory(self) -> None:
        try:
            address = int(self.workstation.memory_addr_input.text().strip(), 0)
            length = int(self.workstation.memory_len_spin.value())
        except ValueError as error:
            self._operation_failed_message(str(error))
            return
        self._begin_worker(
            lambda _log, _phase, _cancel: self._workstation_controller.read_memory(address, length),
            self._v15_memory_completed,
            "READ MEMORY...",
        )

    def _v15_memory_completed(self, block) -> None:
        data = bytes(block.data)
        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            hex_part = " ".join("%02X" % byte for byte in chunk)
            ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
            lines.append("%08X  %-47s  |%s|" % (block.address + offset, hex_part, ascii_part))
        self.workstation.memory_view.setPlainText("\n".join(lines))

    def _on_workstation_symbol_activated(self, name: str, address: str,
                                         _file_path: str, _line: int) -> None:
        browser = self._workstation_controller.symbol_browser
        if browser is None:
            return
        try:
            selected = int(address, 0)
        except (TypeError, ValueError):
            return
        def operation(_log, _phase, _cancel):
            return browser.resolve_address(selected)
        def completed(target):
            self.workstation.source_view.show_location(
                str(target.file or ""), int(target.line or 0),
                _hex_address(target.address), function=str(target.function or name),
            )
        self._begin_worker(operation, completed, "SOURCE...")

    # ------------------------------------------------------------------
    # Target controls refresh coherent panes after every HALT/step.
    # ------------------------------------------------------------------
    def halt_target(self) -> None:
        self._run_controller_control("Halt", self._workstation_controller.halt_target)

    def continue_target(self) -> None:
        self._run_controller_control("Run", self._workstation_controller.run_target)

    def reset_halt_target(self) -> None:
        self._run_controller_control("Reset", self._workstation_controller.reset_halt_target)

    def step_into_target(self) -> None:
        self._run_controller_control("Step In", self._workstation_controller.step_in)

    def step_over_target(self) -> None:
        self._run_controller_control("Step Over", self._workstation_controller.step_over)

    def step_out_target(self) -> None:
        self._run_controller_control("Step Out", self._workstation_controller.step_out)

    def _run_controller_control(self, label: str, command) -> None:
        if not self._workstation_controller.interactive_active:
            self._operation_failed_message("Interactive Debug is not CONNECTED.")
            return
        def operation(_log, _phase, _cancel):
            command()
            return self._workstation_controller.connection_state().target
        self._begin_worker(operation, lambda state: self._v15_control_completed(label, state), label.upper() + "...")

    def _v15_control_completed(self, label: str, state: str) -> None:
        self._status_override = None
        self._set_target_state(state)
        self.interactive_panel.set_last_action(label)
        self.log.emit("%s · TARGET %s" % (label.upper(), str(state).upper()))
        self._sync_workstation_state()
        self._refresh_controls()
        if state == "halted":
            QTimer.singleShot(0, self._v15_capture_snapshot)

    # ------------------------------------------------------------------
    # Client Live Monitor reuses the authenticated RemoteSession.
    # Local Live remains the proven base zero-halt path.
    # ------------------------------------------------------------------
    def start_live_sampling(self) -> None:
        if self._resolved_role() != "client":
            return super().start_live_sampling()
        if self._worker is not None or self._sampling_active:
            return
        remote = self._workstation_controller.remote_session
        if remote is None or not remote.connected:
            self._operation_failed_message("SSH Client is not connected. Login once before starting Live Monitor.")
            return
        try:
            watch_specs = self._live_sampling_expressions()
            interval = float(self.sample_interval.value())
            samples = self.live_panel.sample_limit()
            exact, roots = self._client_symbol_inputs()
            config = ClientLiveMonitorConfig(
                host=remote.profile.host,
                user=remote.profile.user,
                symbols=exact.resolve() if exact is not None and exact.is_file() else None,
                interval_seconds=interval,
                sample_limit=samples,
                watch_specs=tuple(watch_specs),
                ssh_port=remote.profile.port,
                symbol_roots=tuple(roots),
                show_console=False,
            )
            config.validate()
        except Exception as error:
            self._operation_failed_message(str(error))
            return

        self.live_panel.reset_for_sampling()
        self.plot_panel.clear()
        self._sampling_active = True
        self._live_session = self._workstation_controller.live_session

        def execute(log, phase, _cancel):
            try:
                info = self._workstation_controller.start_live_client(config)
                log("LIVE ● %s · shared SSH" % info.tcl_endpoint)
                summary = self._workstation_controller.run_live(phase)
                analytics = self._workstation_controller.live_session.analytics_snapshot()
                return summary, analytics, info
            finally:
                self._workstation_controller.stop_live()

        worker = self._begin_worker(
            execute,
            self._live_sampling_completed,
            "LIVE MONITOR · ZERO-HALT...",
            failed=self._live_sampling_failed,
            phase_handler=self._live_sampling_cycle,
        )
        if worker is None:
            self._sampling_active = False
            self._live_session = None
            self._workstation_controller.stop_live()

    def stop_live_sampling(self) -> None:
        if self._resolved_role() == "client" and self._sampling_active:
            self.live_panel.mark_stopping()
            self._workstation_controller.cancel_live()
            if self._worker is not None:
                self._worker.cancel()
            return
        super().stop_live_sampling()

    # ------------------------------------------------------------------
    # Lifecycle / watchdog
    # ------------------------------------------------------------------
    def _poll_debug_service(self) -> None:
        if self._resolved_role() == "client" and self._workstation_controller.remote_session is not None:
            try:
                state = self._workstation_controller.connection_state()
            except Exception as error:
                self.log.emit("Client state poll failed: %s" % error)
                return
            if self._workstation_controller.interactive_active:
                if state.ssh != "connected":
                    self._workstation_controller.stop_interactive()
                    self._client_mode_active = False
                    self._target_state = None
                    self._status_override = ("SSH SESSION LOST", "failed")
                    self._watchdog.stop()
                    self._refresh_controls()
                    return
                previous = self._target_state
                self._target_state = state.target if state.target in {"running", "halted"} else None
                self._sync_workstation_state()
                self._refresh_controls()
                if self._target_state == "halted" and previous != "halted":
                    QTimer.singleShot(0, self._v15_capture_snapshot)
                return
        super()._poll_debug_service()

    def _set_target_state(self, state: Optional[str]) -> None:
        super()._set_target_state(state)
        controller = getattr(self, "_workstation_controller", None)
        if controller is None or self.session.active:
            return
        remote = controller.remote_session
        if self._resolved_role() == "client" and remote is not None and remote.connected:
            self.status_label.setText("SSH CONNECTED")
            self.status_label.setProperty("state", "ready")
            if self.status_label.style() is not None:
                self.status_label.style().unpolish(self.status_label)
                self.status_label.style().polish(self.status_label)

    def stop_debug(self) -> None:
        if self._resolved_role() == "client" and self._workstation_controller.interactive_active:
            if self._worker is not None:
                return
            def operation(_log, _phase, _cancel):
                self._workstation_controller.stop_interactive()
                return None
            def completed(_result):
                self._client_mode_active = False
                self._target_state = None
                self._initial_target_state = None
                self._watchdog.stop()
                self.show_setup()
                self._set_target_state(None)
                self._sync_workstation_state()
                self._refresh_controls()
                self.log.emit("GDB DISCONNECTED · SSH session kept for Live/Debug reuse.")
            self._begin_worker(operation, completed, "GDB DISCONNECTING...")
            return
        super().stop_debug()

    def prepare_shutdown(self) -> bool:
        ready = super().prepare_shutdown()
        if not ready:
            return False
        try:
            self._workstation_controller.close(disconnect_remote=True)
        except Exception:
            pass
        return True
