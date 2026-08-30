from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from b300_core.debug_service import DebugState
from b300_core.models import ProbeRef
from b300_core.offline_symbols import SymbolCatalogEntry
from b300_gui.debug_live_panel import DebugLivePanel
from b300_gui.debug_tab import DebugTab
from b300_gui.symbol_browser_dialog import SymbolBrowserDialog
from tests.test_debug_tab import FakeDebugService, FakeLiveMonitorSession, FakeSession, FakeTunnel


class FakeSymbolTable:
    def __init__(self, entries):
        self.entries = tuple(entries)
        self.image = Path("firmware.axf")
        self.closed = False

    def search_catalog(self, query="", *, category=None, watchable=None, limit=256):
        needle = str(query).strip().casefold()
        selected = []
        for item in self.entries:
            if needle and needle not in item.name.casefold():
                continue
            if category is not None and item.category != category:
                continue
            if watchable is not None and item.watchable is not watchable:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
        return tuple(selected)

    def close(self):
        self.closed = True


def entry(name, address, size, kind, category, watchable, code=None, reason=None):
    return SymbolCatalogEntry(
        name=name, address=address, size=size, kind=kind, category=category,
        watchable=watchable, watch_block_code=code, watch_block_reason=reason,
        name_unique=True, ambiguous_name=False, name_occurrences=1,
        distinct_address_count=1,
    )


ENTRIES = (
    entry("bRUN", 0x20000849, 1, "D", "data", True),
    entry("xTickCount", 0x20000030, 4, "d", "data", True),
    entry("worker", 0x08012000, 16, "T", "function", False,
          "not_data_symbol", "Symbol category function is not RAM data."),
)


class SymbolBrowserDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_default_view_only_lists_watchable_ram_symbols(self) -> None:
        dialog = SymbolBrowserDialog(FakeSymbolTable(ENTRIES))
        self.assertTrue(dialog.safe_only.isChecked())
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(0, 0).text(), "bRUN")
        self.assertEqual(dialog.table.item(1, 0).text(), "xTickCount")
        self.assertFalse(dialog.use_button.isEnabled())
        dialog.table.selectRow(1)
        self.app.processEvents()
        self.assertTrue(dialog.use_button.isEnabled())
        self.assertEqual(dialog.selected_symbol_name(), "xTickCount")
        self.assertIn("Choose the data type explicitly", dialog.status.text())
        dialog.close()

    def test_blocked_symbol_is_visible_for_diagnostics_but_cannot_be_used(self) -> None:
        dialog = SymbolBrowserDialog(FakeSymbolTable(ENTRIES))
        dialog.safe_only.setChecked(False)
        dialog.category.setCurrentIndex(dialog.category.findData("function"))
        self.app.processEvents()
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), "worker")
        dialog.table.selectRow(0)
        self.app.processEvents()
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertIsNone(dialog.selected_symbol_name())
        self.assertIn("function", dialog.status.text())
        dialog.close()

    def test_search_is_case_insensitive_and_does_not_infer_a_type(self) -> None:
        dialog = SymbolBrowserDialog(FakeSymbolTable(ENTRIES))
        dialog.search.setText("TICK")
        self.app.processEvents()
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), "xTickCount")
        self.assertFalse(hasattr(dialog._visible_entries[0], "type"))
        dialog.close()

    def test_live_panel_browser_signal_and_selection_preserve_explicit_type(self) -> None:
        panel = DebugLivePanel()
        panel.type_combo.setCurrentText("f32")
        signals = []
        panel.symbol_browser_requested.connect(lambda: signals.append(True))
        panel.browse_symbols_btn.click()
        self.assertEqual(signals, [True])
        panel.select_symbol("xTickCount")
        self.assertEqual(panel.expressions.text(), "xTickCount")
        self.assertEqual(panel.type_combo.currentText(), "f32")
        panel.close()

    def test_debug_tab_browser_uses_offline_table_and_only_populates_symbol_name(self) -> None:
        service = FakeDebugService(DebugState.STOPPED)
        session = FakeSession(service, initial="running", attach_state="halted")
        tab = DebugTab(
            service, lambda: ProbeRef("TEST_PROBE"), debug_session=session,
            tcl_factory=lambda _endpoint: service.tcl, probe_count=lambda: 1,
            tunnel_factory=lambda config: FakeTunnel(config, []),
            live_session_factory=FakeLiveMonitorSession,
        )
        tab.live_panel.type_combo.setCurrentText("i32")
        fake_table = FakeSymbolTable(ENTRIES)

        class AcceptedDialog:
            def __init__(self, symbols, parent=None):
                self.symbols = symbols
            def exec(self):
                return QDialog.DialogCode.Accepted
            def selected_symbol_name(self):
                return "xTickCount"

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "firmware.axf"
            image.write_bytes(b"fake")
            tab.symbol_path.setText(str(image))
            with mock.patch("b300_gui.debug_tab.OfflineSymbolTable", return_value=fake_table),                     mock.patch("b300_gui.debug_tab.SymbolBrowserDialog", AcceptedDialog):
                tab.browse_live_symbols()

        self.assertTrue(fake_table.closed)
        self.assertEqual(tab.live_panel.expressions.text(), "xTickCount")
        self.assertEqual(tab.live_panel.type_combo.currentText(), "i32")
        tab.close()


if __name__ == "__main__":
    unittest.main()
