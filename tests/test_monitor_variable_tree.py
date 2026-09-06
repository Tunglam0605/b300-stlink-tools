from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_core.live_monitor import LiveSample, LiveValue
from b300_core.offline_symbols import SourceLocation
from b300_core.typed_symbols import VariableNode
from b300_gui.production_live_panel import ProductionLivePanel
from b300_gui.views.monitor_view import MonitorView
from tests.test_typed_symbols import _build_keil_fixture


class _LargeCatalog:
    fingerprint = "a" * 64

    def __init__(self, count=167):
        self.root = VariableNode(
            node_id="large", name="large", type_name="Large_t", kind="structure",
            address=0x20000000, byte_size=count, has_children=True,
            availability="browse_only", reason=None, path="large",
            source_file="fixture.c", value_type=None,
        )
        self.children_nodes = tuple(
            VariableNode(
                node_id="large.f%d" % index, name="f%d" % index,
                type_name="uint8_t", kind="scalar",
                address=0x20000000 + index, byte_size=1, has_children=False,
                availability="watchable", reason=None, path="large.f%d" % index,
                source_file="fixture.c", value_type="u8",
            )
            for index in range(count)
        )
        self.nodes = {node.node_id: node for node in (self.root,) + self.children_nodes}

    def roots(self, query="", offset=0, limit=100):
        rows = (self.root,) if not query or query.casefold() in self.root.name else ()
        return rows[offset:offset + limit]

    def children(self, node_id, offset=0, limit=100):
        rows = self.children_nodes if node_id == self.root.node_id else ()
        return rows[offset:offset + limit]

    def node(self, node_id):
        return self.nodes[node_id]


class _Signal:
    def __init__(self):
        self.receivers = []

    def connect(self, receiver):
        self.receivers.append(receiver)

    def emit(self, value=None):
        for receiver in tuple(self.receivers):
            receiver() if value is None else receiver(value)


class _DeferredCatalogWorker:
    def __init__(self, operation, _parent=None):
        self.operation = operation
        self.completed = _Signal()
        self.failed = _Signal()
        self.finished = _Signal()
        self.started = False
        self.deleted = False

    def start(self):
        self.started = True

    def run(self):
        try:
            result = self.operation(lambda _message: None, lambda _value: None, None)
        except Exception as error:
            self.failed.emit(type("Failure", (), {"message": str(error)})())
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()

    def cancel(self):
        pass

    def isRunning(self):
        return self.started and not self.deleted

    def wait(self, _milliseconds):
        return True

    def deleteLater(self):
        self.deleted = True


class MonitorVariableTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.temp = tempfile.TemporaryDirectory()
        cls.image = _build_keil_fixture(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_selecting_nested_leaf_adds_dwarf_typed_watch_without_manual_input(self):
        panel = ProductionLivePanel()
        view = MonitorView(live_panel=panel)
        view.load_typed_symbols(self.image)
        tree = view.variable_tree_panel
        machine = next(tree.model.index(row, 0) for row in range(tree.model.rowCount())
                       if tree.model.data(tree.model.index(row, 0)) == "g_machine")
        tree.model.fetchMore(machine)
        position = next(tree.model.index(row, 0, machine)
                        for row in range(tree.model.rowCount(machine))
                        if tree.model.data(tree.model.index(row, 0, machine)) == "position")
        tree.model.fetchMore(position)
        x = next(tree.model.index(row, 0, position)
                 for row in range(tree.model.rowCount(position))
                 if tree.model.data(tree.model.index(row, 0, position)) == "x")
        tree.tree.setCurrentIndex(x)
        self.app.processEvents()
        tree.add_button.click()

        watches = panel.compiled_watches()
        self.assertEqual(len(watches), 1)
        self.assertEqual((watches[0].name, watches[0].value_type, watches[0].address),
                         ("g_machine.position.x", "i16", 0x20000000))
        self.assertEqual(panel.watch_specs(), ())
        self.assertEqual(panel.table.item(0, 2).text(), "i16")
        view.close()

    def test_selecting_struct_adds_all_watchable_scalar_descendants(self):
        panel = ProductionLivePanel()
        view = MonitorView(live_panel=panel)
        view.load_typed_symbols(self.image)
        tree = view.variable_tree_panel
        machine = next(tree.model.index(row, 0) for row in range(tree.model.rowCount())
                       if tree.model.data(tree.model.index(row, 0)) == "g_machine")
        tree.tree.setCurrentIndex(machine)
        self.app.processEvents()

        self.assertTrue(tree.add_button.isEnabled())
        tree.add_button.click()

        watches = panel.compiled_watches()
        self.assertEqual(len(watches), 16)
        self.assertEqual(
            tuple(watch.name for watch in watches[:4]),
            ("g_machine.position.x", "g_machine.position.y",
             "g_machine.rpm[0]", "g_machine.rpm[1]"),
        )
        self.assertEqual(watches[-1].name, "g_machine.next")
        self.assertIn("16", tree.status.text())
        view.close()

    def test_selecting_large_struct_adds_all_167_scalar_descendants(self):
        panel = ProductionLivePanel()
        view = MonitorView(live_panel=panel)
        tree = view.variable_tree_panel
        tree.set_catalog(_LargeCatalog(167))
        tree.tree.setCurrentIndex(tree.model.index(0, 0))
        self.app.processEvents()

        tree.add_button.click()

        self.assertEqual(len(panel.compiled_watches()), 167)
        self.assertEqual(panel.compiled_watches()[0].name, "large.f0")
        self.assertEqual(panel.compiled_watches()[-1].name, "large.f166")
        self.assertIn("167", tree.status.text())
        view.close()

    def test_failed_large_struct_preflight_disables_add_until_selection_changes(self):
        panel = ProductionLivePanel()
        view = MonitorView(live_panel=panel)
        tree = view.variable_tree_panel
        tree.set_catalog(_LargeCatalog(513))
        root = tree.model.index(0, 0)
        tree.tree.setCurrentIndex(root)
        self.app.processEvents()

        tree.add_button.click()

        self.assertEqual(panel.compiled_watches(), ())
        self.assertFalse(tree.add_button.isEnabled())
        tree.model.fetchMore(root)
        tree.tree.setCurrentIndex(tree.model.index(0, 0, root))
        self.app.processEvents()
        self.assertTrue(tree.add_button.isEnabled())
        view.close()

    def test_production_watch_chooser_has_no_manual_type_or_json_preset_controls(self):
        panel = ProductionLivePanel()
        try:
            self.assertFalse(hasattr(panel, "expressions"))
            self.assertFalse(hasattr(panel, "type_combo"))
            self.assertFalse(hasattr(panel, "add_watch_btn"))
            self.assertFalse(hasattr(panel, "load_preset_btn"))
            self.assertFalse(hasattr(panel, "save_preset_btn"))
            self.assertIn("axf/elf", panel.watch_source_hint.text().casefold())
        finally:
            panel.close()

    def test_selecting_project_symbols_loads_typed_catalog_automatically(self):
        view = MonitorView(live_panel=ProductionLivePanel())
        try:
            view.set_symbols(self.image)
            self.assertIsNotNone(view.variable_tree_panel.catalog)
            names = {
                view.variable_tree_panel.model.data(
                    view.variable_tree_panel.model.index(row, 0)
                )
                for row in range(view.variable_tree_panel.model.rowCount())
            }
            self.assertIn("g_machine", names)
        finally:
            view.close()

    def test_automatic_catalog_parse_runs_outside_the_gui_call_stack(self):
        workers = []

        def factory(operation, parent=None):
            worker = _DeferredCatalogWorker(operation, parent)
            workers.append(worker)
            return worker

        view = MonitorView(
            live_panel=ProductionLivePanel(), catalog_worker_factory=factory,
        )
        try:
            view.begin_typed_symbol_load(self.image)
            self.assertEqual(len(workers), 1)
            self.assertTrue(workers[0].started)
            self.assertIsNone(view.variable_tree_panel.catalog)
            self.assertIn("Đang đọc", view.variable_tree_panel.status.text())

            workers[0].run()

            self.assertIsNotNone(view.variable_tree_panel.catalog)
            self.assertIn("Đã nạp", view.variable_tree_panel.status.text())
        finally:
            view.close()

    def test_live_sample_updates_selected_node_in_typed_tree(self):
        panel = ProductionLivePanel()
        view = MonitorView(live_panel=panel)
        view.load_typed_symbols(self.image)
        tree = view.variable_tree_panel
        machine = next(tree.model.index(row, 0) for row in range(tree.model.rowCount())
                       if tree.model.data(tree.model.index(row, 0)) == "g_machine")
        tree.model.fetchMore(machine)
        position = next(tree.model.index(row, 0, machine)
                        for row in range(tree.model.rowCount(machine))
                        if tree.model.data(tree.model.index(row, 0, machine)) == "position")
        tree.model.fetchMore(position)
        x = next(tree.model.index(row, 0, position)
                 for row in range(tree.model.rowCount(position))
                 if tree.model.data(tree.model.index(row, 0, position)) == "x")
        node = tree.model.node_for_index(x)

        view._render_sample_summary(LiveSample(
            cycle=0, scheduled_elapsed_seconds=0.0, captured_elapsed_seconds=0.25,
            read_duration_seconds=0.001, overrun=False, pc=0x08000000,
            source=SourceLocation(0x08000000, "main", "main.c", 1),
            values=(LiveValue(
                node.path, "i16", node.address, -7, "F9FF", node_id=node.node_id,
            ),),
        ))

        self.assertEqual(tree.model.data(tree.model.index(x.row(), 2, position)), "-7")
        self.assertEqual(tree.model.data(tree.model.index(x.row(), 4, position)), "Nhất quán")
        view.close()


if __name__ == "__main__":
    unittest.main()
