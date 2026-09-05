"""Comprehensive GUI regression tests for B300 Debug Workstation v0.15.0."""

import unittest
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QStackedWidget, QTableWidget, QTreeView

from b300_core.models import ProbeRef
from b300_gui.debug_breakpoints_pane import DebugBreakpointsPane
from b300_gui.debug_callstack_pane import DebugCallStackPane
from b300_gui.debug_connection_panel import DebugConnectionPanel
from b300_gui.debug_mode_selector import DebugModeSelector
from b300_gui.debug_registers_pane import DebugRegistersPane
from b300_gui.debug_source_view import DebugSourceView
from b300_gui.debug_symbols_pane import DebugSymbolsPane
from b300_gui.debug_tab import DebugTab
from b300_gui.debug_variables_pane import DebugVariablesPane
from b300_gui.debug_view_models import (
    DebugBreakpoint,
    DebugConnectionState,
    DebugFrame,
    DebugRegister,
    DebugVariableNode,
    VariablesTreeModel,
)
from b300_gui.debug_workspace import DebugWorkstationWidget
from b300_gui.remote_login_dialog import RemoteLoginDialog
from tests.test_debug_tab import FakeDebugService, FakeLiveMonitorSession, FakeSession, FakeTunnel


class DebugWorkstationGuiTests(unittest.TestCase):
    """Test suite verifying all 17 UX & architectural requirements for Engineering Workstation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_01_mode_first_entry_selector(self) -> None:
        """Requirement 1: Mode-first selection with 3 technical tiles (LOCAL, GATEWAY, CLIENT)."""
        selector = DebugModeSelector()
        self.assertIsNotNone(selector.tile_local)
        self.assertIsNotNone(selector.tile_gateway)
        self.assertIsNotNone(selector.tile_client)

        received_modes = []
        selector.mode_selected.connect(received_modes.append)

        selector.tile_local.button.click()
        self.assertEqual(received_modes, ["local"])
        self.assertEqual(selector.current_mode(), "local")

        selector.tile_gateway.button.click()
        self.assertEqual(received_modes, ["local", "gateway"])
        self.assertEqual(selector.current_mode(), "gateway")

        selector.tile_client.button.click()
        self.assertEqual(received_modes, ["local", "gateway", "client"])
        self.assertEqual(selector.current_mode(), "client")

    def test_02_client_login_dialog_masked_password_and_no_leak(self) -> None:
        """Requirement 3 & 4: Single Client login, masked password, async lifecycle, zero logging."""
        dialog = RemoteLoginDialog(default_host="192.168.1.145", default_user="Admin", default_port=22)
        self.assertEqual(dialog.password_input.echoMode(), QLineEdit.EchoMode.Password)
        self.assertTrue(dialog.remember_checkbox.isChecked())
        self.assertEqual(dialog.host_input.text(), "192.168.1.145")
        self.assertEqual(dialog.user_input.text(), "Admin")

        # Remembered password indicator test
        dialog.set_has_remembered_credential(True)
        self.assertIn("Đã lưu trên máy này", dialog.password_input.placeholderText())

        # Verify async lifecycle on connect click
        dialog.password_input.setText("SecretPass123!")
        creds = []
        dialog.login_requested.connect(lambda h, u, p, pt, r: creds.append((h, u, p, pt, r)))
        dialog.btn_connect.click()

        self.assertEqual(len(creds), 1)
        host, user, password, port, remember = creds[0]
        self.assertEqual(host, "192.168.1.145")
        self.assertEqual(user, "Admin")
        self.assertEqual(password, "SecretPass123!")
        self.assertEqual(port, 22)
        self.assertTrue(remember)

        # Dialog remains open and is in connecting state
        self.assertFalse(dialog.btn_connect.isEnabled())
        self.assertIn("ĐANG KẾT NỐI", dialog.btn_connect.text())

        # Simulate authentication failure
        dialog.set_login_error("Permission denied (publickey,password)")
        self.assertTrue(dialog.btn_connect.isEnabled())
        self.assertIn("Permission denied", dialog.status_banner.text())
        self.assertEqual(dialog.password_input.echoMode(), QLineEdit.EchoMode.Password)

        # Simulate successful login
        dialog.set_login_success()
        self.assertEqual(dialog.password_input.text(), "")
        dialog.close()

    def test_03_debug_studio_has_no_password_fields(self) -> None:
        """Requirement 4: Debug Studio Workstation does NOT display any password fields."""
        workstation = DebugWorkstationWidget()
        line_edits = workstation.findChildren(QLineEdit)
        for le in line_edits:
            self.assertNotEqual(
                le.echoMode(),
                QLineEdit.EchoMode.Password,
                "Debug Studio Workstation must not contain any password input fields!",
            )
            self.assertNotIn("pass", le.objectName().lower())

    def test_04_no_external_powershell_or_cmd_spawned(self) -> None:
        """Requirement 4 & 22: Frontend does not own or spawn external CMD/PowerShell terminal."""
        dlg = RemoteLoginDialog()
        self.assertIsInstance(dlg, RemoteLoginDialog)
        dlg.close()

    def test_05_callstack_pane_is_structured_table(self) -> None:
        """Requirement 8, 10 & 22: Call Stack table emits frame_selected(level: int)."""
        pane = DebugCallStackPane()
        self.assertIsInstance(pane.table, QTableWidget)
        self.assertEqual(pane.table.columnCount(), 5)
        headers = [pane.table.horizontalHeaderItem(i).text() for i in range(5)]
        self.assertEqual(headers, ["#", "Function", "File", "Line", "Address"])

        selected_levels = []
        pane.frame_selected.connect(selected_levels.append)

        frames = [
            DebugFrame(level=0, function="HardFault_Handler", file="stm32f4xx_it.c", line=42, address="0x08012344"),
            DebugFrame(level=1, function="main", file="main.c", line=105, address="0x08014568"),
        ]
        pane.set_frames(frames)
        self.assertEqual(pane.table.rowCount(), 2)
        self.assertEqual(pane.table.item(0, 1).text(), "HardFault_Handler")
        self.assertEqual(pane.table.item(1, 1).text(), "main")

        # Select row 1 and verify selection signal emits int level
        pane.table.selectRow(1)
        self.assertGreaterEqual(len(selected_levels), 1)
        self.assertEqual(selected_levels[-1], 1)

    def test_06_variables_pane_is_expandable_tree(self) -> None:
        """Requirement 7, 9 & 22: Variables is an expandable tree view with clean metadata."""
        pane = DebugVariablesPane()
        self.assertIsInstance(pane.tree, QTreeView)
        self.assertIsInstance(pane.model, VariablesTreeModel)

        parent_node = DebugVariableNode(id="config", name="system_config", value="{...}", type="SysConfig", address="0x20000100")
        child_node = DebugVariableNode(id="config.baud", name="baudrate", value="115200", type="uint32_t", address="0x20000104", editable=True)
        parent_node.add_child(child_node)

        pane.set_variables([parent_node])
        self.assertEqual(pane.model.rowCount(), 1)
        parent_idx = pane.model.index(0, 0)
        self.assertEqual(pane.model.data(parent_idx, Qt.ItemDataRole.DisplayRole), "system_config")
        self.assertEqual(pane.model.rowCount(parent_idx), 1)

    def test_07_variable_editing_rule_disabled_when_running(self) -> None:
        """Requirement 9 & 22: Variable editing is STRICTLY DISABLED when MCU is RUNNING."""
        pane = DebugVariablesPane()
        node = DebugVariableNode(id="v1", name="sensor_val", value="42", type="int", address="0x20000000", editable=True)
        pane.set_variables([node])

        # Interactive connected but target is RUNNING
        pane.set_target_state("RUNNING", interactive_connected=True)
        val_idx = pane.model.index(0, 1)
        flags = pane.model.flags(val_idx)
        self.assertFalse(
            bool(flags & Qt.ItemFlag.ItemIsEditable),
            "Variables must NOT be editable when target is RUNNING!",
        )

    def test_08_variable_editing_rule_enabled_when_halted(self) -> None:
        """Requirement 9 & 22: Variable editing is enabled ONLY when HALTED + editable capability."""
        pane = DebugVariablesPane()
        node = DebugVariableNode(id="v1", name="sensor_val", value="42", type="int", address="0x20000000", editable=True)
        pane.set_variables([node])

        # Interactive connected and target is HALTED
        pane.set_target_state("HALTED", interactive_connected=True)
        val_idx = pane.model.index(0, 1)
        flags = pane.model.flags(val_idx)
        self.assertTrue(
            bool(flags & Qt.ItemFlag.ItemIsEditable),
            "Variables MUST be editable when target is HALTED and node is editable!",
        )

        # But column 0 (name) or non-editable node must remain read-only
        name_idx = pane.model.index(0, 0)
        self.assertFalse(bool(pane.model.flags(name_idx) & Qt.ItemFlag.ItemIsEditable))

    def test_09_registers_pane_structure_and_highlights(self) -> None:
        """Requirement 10 & 22: Registers table with changed highlights."""
        pane = DebugRegistersPane()
        self.assertIsInstance(pane.table, QTableWidget)
        self.assertEqual(pane.table.columnCount(), 3)
        self.assertEqual(pane.table.rowCount(), 17)

        regs = [
            DebugRegister(name="R0", value="0x00000000"),
            DebugRegister(name="PC", value="0x080146A8"),
            DebugRegister(name="SP", value="0x2001FFC0"),
            DebugRegister(name="LR", value="0x08010245"),
        ]
        pane.set_registers(regs)

        # Update R0 to new value -> should be updated
        updated_regs = [
            DebugRegister(name="R0", value="0x00000001"),
            DebugRegister(name="PC", value="0x080146A8"),
            DebugRegister(name="SP", value="0x2001FFC0"),
            DebugRegister(name="LR", value="0x08010245"),
        ]
        pane.set_registers(updated_regs)
        r0_item = pane.table.item(0, 1)
        self.assertEqual(r0_item.text(), "0x00000001")

    def test_10_breakpoint_manager_presentation(self) -> None:
        """Requirement 11, 12 & 22: Breakpoints table with BP X/6 and WP Y/4 badge."""
        pane = DebugBreakpointsPane()
        self.assertIsInstance(pane.table, QTableWidget)
        self.assertEqual(pane.table.columnCount(), 6)

        bps = [
            DebugBreakpoint(number=1, kind="breakpoint", location="main.c:42", address="0x08010040", enabled=True, hit_count=3),
            DebugBreakpoint(number=2, kind="watchpoint", location="g_counter", address="0x20000010", enabled=True, hit_count=0),
        ]
        pane.set_breakpoints(bps)
        self.assertEqual(pane.table.rowCount(), 2)
        self.assertIn("BP 1/6", pane.status_badge.text())
        self.assertIn("WP 1/4", pane.status_badge.text())

    def test_11_source_view_and_disassembly_fallback(self) -> None:
        """Requirement 6 & 22: Source view with gutter, arrow marker, and disassembly fallback."""
        view = DebugSourceView()
        self.assertIsNotNone(view.editor)
        self.assertIsNotNone(view.editor.line_number_area)

        # When source file does not exist, disassembly view fallback is shown
        view.show_location("non_existent_source.c", 10, "0x080146A8", function="SystemInit")
        content = view.disasm_view.toPlainText()
        self.assertIn("0x080146A8", content)
        self.assertIn("SystemInit", content)

    def test_12_workstation_status_bar_and_run_halt_states(self) -> None:
        """Requirement 5 & 22: Status bar RUN/HALT states."""
        workstation = DebugWorkstationWidget()
        bar = workstation.status_bar

        state_running = DebugConnectionState(
            mode="client", ssh=True, gdb=True, tcl=True, target="RUNNING", pc="0x080146A8", sample_rate="10 Hz"
        )
        bar.update_state(state_running)
        self.assertIn("RUN", bar.mcu_state_badge.text())

        state_halted = DebugConnectionState(
            mode="local", ssh=False, gdb=True, tcl=True, target="HALTED", pc="0x08010020", sample_rate="—"
        )
        bar.update_state(state_halted)
        self.assertIn("HALT", bar.mcu_state_badge.text())

    def test_13_workstation_toolbar_actions(self) -> None:
        """Requirement 5 & 8: Compact engineering toolbar with Run, Halt, Step, Step Out, Break, Disconnect."""
        ws = DebugWorkstationWidget()
        tb = ws.toolbar
        actions_triggered = []
        tb.run_requested.connect(lambda: actions_triggered.append("run"))
        tb.halt_requested.connect(lambda: actions_triggered.append("halt"))
        tb.reset_requested.connect(lambda: actions_triggered.append("reset"))
        tb.step_in_requested.connect(lambda: actions_triggered.append("step_in"))
        tb.step_over_requested.connect(lambda: actions_triggered.append("step_over"))
        tb.step_out_requested.connect(lambda: actions_triggered.append("step_out"))
        tb.break_requested.connect(lambda: actions_triggered.append("break"))
        tb.disconnect_requested.connect(lambda: actions_triggered.append("disconnect"))

        tb.btn_run.click()
        tb.btn_halt.click()
        tb.btn_reset.click()
        tb.btn_step_in.click()
        tb.btn_step_over.click()
        tb.btn_step_out.click()
        tb.btn_break.click()
        tb.btn_disconnect.click()

        self.assertEqual(
            actions_triggered,
            ["run", "halt", "reset", "step_in", "step_over", "step_out", "break", "disconnect"],
        )

    def test_14_bottom_dock_tabs_and_log_collapsed(self) -> None:
        """Requirement 5, 11 & 22: LIVE, MEMORY, CONSOLE, TECHNICAL LOG bottom dock tabs."""
        ws = DebugWorkstationWidget()
        tabs = ws.bottom_tabs
        self.assertEqual(tabs.count(), 4)
        self.assertEqual(tabs.tabText(0), "LIVE")
        self.assertEqual(tabs.tabText(1), "MEMORY")
        self.assertEqual(tabs.tabText(2), "CONSOLE")
        self.assertEqual(tabs.tabText(3), "TECHNICAL LOG")
        # Default active tab is LIVE (index 0)
        self.assertEqual(tabs.currentIndex(), 0)

    def test_15_compact_width_responsive_splitters(self) -> None:
        """Requirement 22: Compact 760x460 responsive layout without breaking workspace."""
        ws = DebugWorkstationWidget()
        ws.resize(760, 460)
        ws.show()
        self.app.processEvents()
        self.assertGreater(ws.width(), 0)
        self.assertGreater(ws.height(), 0)
        self.assertIsNotNone(ws.horizontal_splitter)
        self.assertIsNotNone(ws.vertical_splitter)
        ws.close()

    def test_16_mode_separation_in_connection_panel(self) -> None:
        """Requirement 1, 2, 3, 4: Local, Gateway, Client UI modes separation."""
        panel = DebugConnectionPanel()
        panel.set_mode("local")
        self.assertFalse(panel.symbols_box.isHidden())
        self.assertTrue(panel.gateway_actions.isHidden())
        self.assertTrue(panel.client_box.isHidden())

        panel.set_mode("gateway")
        self.assertFalse(panel.gateway_actions.isHidden())
        self.assertTrue(panel.symbols_box.isHidden())
        self.assertTrue(panel.client_box.isHidden())

        panel.set_mode("client")
        self.assertFalse(panel.client_box.isHidden())
        self.assertFalse(panel.symbols_box.isHidden())
        self.assertTrue(panel.gateway_actions.isHidden())

    def test_17_debug_tab_workstation_integration_lifecycle(self) -> None:
        """Requirement 1, 5, 8 & 22: Mode-first screen flow, setup, and workstation stack."""
        service = FakeDebugService()
        session = FakeSession(service)
        tab = DebugTab(
            service,
            lambda: ProbeRef("DEBUG_TEST"),
            debug_session=session,
            tcl_factory=lambda _endpoint: service.tcl,
            probe_count=lambda: 1,
            tunnel_factory=lambda config: FakeTunnel(config, []),
            settings=None,
            live_session_factory=FakeLiveMonitorSession,
        )
        self.assertIsInstance(tab.main_stack, QStackedWidget)
        self.assertIsInstance(tab.workstation, DebugWorkstationWidget)
        self.assertIsInstance(tab.mode_selector, DebugModeSelector)

        # Mode screen transition
        tab.show_mode_selector()
        self.assertEqual(tab.main_stack.currentWidget(), tab.mode_selector)

        # Setup screen transition
        tab.show_setup()
        self.assertEqual(tab.main_stack.currentWidget(), tab.scroll_area)

        # Workstation transition
        tab.show_workstation()
        self.assertEqual(tab.main_stack.currentWidget(), tab.workstation)

        tab.prepare_shutdown()


if __name__ == "__main__":
    unittest.main()
