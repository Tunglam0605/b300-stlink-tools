from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from b300_core.typed_symbols import VariableNode
from b300_gui.variable_tree_model import VariableTreeModel
from b300_gui.variable_tree_panel import VariableTreePanel


def node(name, *, node_id=None, kind="scalar", address=0x20000000,
         children=False, availability="watchable"):
    return VariableNode(
        node_id=node_id or name, name=name, type_name="uint32_t", kind=kind,
        address=address, byte_size=4, has_children=children,
        availability=availability, reason=None, path=name, source_file="fixture.c",
        value_type="u32" if not children else None,
    )


class _Catalog:
    fingerprint = "f" * 64

    def __init__(self):
        self.root_nodes = tuple(node("item%03d" % index, address=0x20000000 + index * 4)
                                for index in range(250))
        self.parent = node("machine", kind="structure", children=True,
                           availability="browse_only")
        self.child_nodes = (node("speed", node_id="machine.speed", address=0x20001000),)

    def roots(self, query="", offset=0, limit=100):
        rows = (self.parent,) + self.root_nodes
        if query:
            rows = tuple(item for item in rows if query.casefold() in item.name.casefold())
        return rows[offset:offset + limit]

    def children(self, node_id, offset=0, limit=100):
        return self.child_nodes[offset:offset + limit] if node_id == "machine" else ()


class VariableTreeModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_roots_and_children_load_in_bounded_pages_without_model_reset_on_expand(self):
        model = VariableTreeModel()
        model.set_catalog(_Catalog())
        self.assertEqual(model.rowCount(), 100)
        self.assertTrue(model.canFetchMore(QModelIndex()))
        model.fetchMore(QModelIndex())
        self.assertEqual(model.rowCount(), 200)

        machine = next(model.index(row, 0) for row in range(model.rowCount())
                       if model.data(model.index(row, 0)) == "machine")
        resets = []
        model.modelReset.connect(lambda: resets.append(True))
        model.fetchMore(machine)
        self.assertEqual(model.rowCount(machine), 1)
        self.assertEqual(model.data(model.index(0, 0, machine)), "speed")
        self.assertEqual(resets, [])

    def test_search_reloads_matching_roots_and_columns_expose_typed_status(self):
        model = VariableTreeModel()
        model.set_catalog(_Catalog())
        model.search("item249")
        self.assertEqual(model.rowCount(), 1)
        index = model.index(0, 0)
        self.assertEqual(model.data(index), "item249")
        self.assertEqual(model.data(model.index(0, 1)), "uint32_t")
        self.assertEqual(model.data(model.index(0, 3)), "0x200003E4")
        self.assertEqual(model.data(model.index(0, 4)), "Sẵn sàng")

    def test_panel_only_enables_add_watch_for_watchable_leaf(self):
        panel = VariableTreePanel()
        panel.set_catalog(_Catalog())
        selected = []
        panel.add_watch_requested.connect(selected.append)

        machine = panel.model.index(0, 0)
        panel.tree.setCurrentIndex(machine)
        self.app.processEvents()
        self.assertFalse(panel.add_button.isEnabled())

        panel.model.fetchMore(machine)
        speed = panel.model.index(0, 0, machine)
        panel.tree.setCurrentIndex(speed)
        self.app.processEvents()
        self.assertTrue(panel.add_button.isEnabled())
        panel.add_button.click()
        self.assertEqual(selected, ["machine.speed"])
        panel.close()


if __name__ == "__main__":
    unittest.main()
