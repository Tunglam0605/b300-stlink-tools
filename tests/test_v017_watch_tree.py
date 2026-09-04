import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from b300_gui.debug_variables_pane import DebugVariablesPane
from b300_gui.debug_view_models import DebugVariableNode


class V017WatchTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_struct_expands_recursively_and_preserves_open_state(self):
        pane = DebugVariablesPane(title="WATCH 1")
        requests = []
        pane.request_children.connect(requests.append)

        root = DebugVariableNode(
            id="var1",
            name="xAgvInfor",
            value="{...}",
            type="struct <anonymous>",
            has_children=True,
        )
        pane.set_variables([root])
        root_index = pane.model.index(0, 0)
        pane.tree.setExpanded(root_index, True)
        self.app.processEvents()
        self.assertEqual(requests, ["var1"])

        nested = DebugVariableNode(
            id="var1.R2DTag",
            name="R2DTag",
            value="0x2000D1AB {...}",
            type="uchar[5]",
            has_children=True,
        )
        pane.insert_children(
            "var1",
            [
                DebugVariableNode(
                    id="var1.status",
                    name="status",
                    value="1 STATUS_STOP",
                    type="enum (uchar)",
                ),
                nested,
                DebugVariableNode(
                    id="var1.runSpeed",
                    name="runSpeed",
                    value="65",
                    type="ushort",
                ),
            ],
        )
        root_index = pane.model.index_for_id("var1")
        self.assertTrue(pane.tree.isExpanded(root_index))
        self.assertEqual(pane.model.rowCount(root_index), 3)

        nested_index = pane.model.index_for_id("var1.R2DTag")
        pane.tree.setExpanded(nested_index, True)
        self.app.processEvents()
        self.assertIn("var1.R2DTag", requests)

        pane.insert_children(
            "var1.R2DTag",
            [
                DebugVariableNode(
                    id="var1.R2DTag.0", name="[0]", value="0x41", type="uchar"
                ),
                DebugVariableNode(
                    id="var1.R2DTag.1", name="[1]", value="0x42", type="uchar"
                ),
            ],
        )
        nested_index = pane.model.index_for_id("var1.R2DTag")
        self.assertEqual(pane.model.rowCount(nested_index), 2)

        refreshed = DebugVariableNode(
            id="var1",
            name="xAgvInfor",
            value="{...}",
            type="struct <anonymous>",
            has_children=True,
        )
        pane.set_variables([refreshed])
        refreshed_root = pane.model.index_for_id("var1")
        refreshed_nested = pane.model.index_for_id("var1.R2DTag")
        self.assertTrue(pane.tree.isExpanded(refreshed_root))
        self.assertTrue(pane.tree.isExpanded(refreshed_nested))
        self.assertEqual(pane.model.rowCount(refreshed_root), 3)
        self.assertEqual(pane.model.rowCount(refreshed_nested), 2)

    def test_lazy_child_insert_does_not_reset_whole_model(self):
        pane = DebugVariablesPane(title="WATCH 1")
        pane.set_variables([
            DebugVariableNode(
                id="root", name="state", value="{...}", type="struct State", has_children=True
            )
        ])
        resets = []
        pane.model.modelReset.connect(lambda: resets.append(True))
        pane.insert_children(
            "root",
            [DebugVariableNode(id="root.mode", name="mode", value="2", type="uint8_t")],
        )
        self.assertEqual(resets, [])
        self.assertEqual(pane.model.rowCount(pane.model.index_for_id("root")), 1)

    def test_keil_primary_columns_hide_address_by_default(self):
        pane = DebugVariablesPane(title="WATCH 1")
        self.assertEqual(pane.model.COLUMNS[:3], ("Name", "Value", "Type"))
        self.assertTrue(pane.tree.isColumnHidden(3))
        pane.show_address_column(True)
        self.assertFalse(pane.tree.isColumnHidden(3))


if __name__ == "__main__":
    unittest.main()
