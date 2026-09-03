from __future__ import annotations

import unittest

from b300_core.debug_workspace import DebugWorkspaceBackend


class Result:
    def __init__(self, payload: str):
        self.payload = payload


class FakeGdb:
    def __init__(self):
        self.calls = []

    @staticmethod
    def _quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def evaluate_variable(self, expression: str):
        self.calls.append(("evaluate", expression))
        if "(" in expression:
            raise ValueError("unsupported")
        return object()

    def _request(self, command, accepted):
        self.calls.append((command, accepted))
        if command.startswith("-stack-list-variables"):
            return Result('variables=[variable={name="motor",value="{...}",type="Motor_t"},variable={name="tick",value="42",type="uint32_t"}]')
        if command.startswith("-var-create"):
            return Result('name="var1",numchild="3",value="{...}",type="Motor_t"')
        if command.startswith("-var-list-children"):
            return Result('numchild="2",children=[child={name="var1.speed",exp="speed",numchild="0",value="1200",type="int"},child={name="var1.pid",exp="pid",numchild="3",value="{...}",type="PID_t"}]')
        if command.startswith("-var-evaluate-expression"):
            return Result('value="1210"')
        if command.startswith("-var-assign"):
            return Result('value="1800"')
        if command.startswith("-break-list"):
            return Result('BreakpointTable={body=[bkpt={number="1",type="hw breakpoint",enabled="y",addr="0x08012345",func="Motor_Update",times="3",original-location="Motor_Update"},bkpt={number="2",type="watchpoint",enabled="n",what="motor.rpm",times="1"}]}')
        return Result("")


class DebugWorkspaceBackendTests(unittest.TestCase):
    def setUp(self):
        self.gdb = FakeGdb()
        self.backend = DebugWorkspaceBackend(self.gdb)

    def test_create_watch_and_expand_children(self):
        node = self.backend.create_watch("motor")
        self.assertEqual(node.id, "var1")
        self.assertEqual(node.name, "motor")
        self.assertTrue(node.has_children)
        children = self.backend.list_children(node.id)
        self.assertEqual([item.name for item in children], ["speed", "pid"])
        self.assertFalse(children[0].has_children)
        self.assertTrue(children[1].has_children)

    def test_assign_variable_accepts_simple_value_and_rejects_expression(self):
        self.assertEqual(self.backend.assign_variable("var1.speed", "1800"), "1800")
        with self.assertRaisesRegex(ValueError, "simple scalar"):
            self.backend.assign_variable("var1.speed", "danger()")

    def test_select_frame_is_bounded(self):
        self.assertEqual(self.backend.select_frame(3), 3)
        self.assertEqual(self.gdb.calls[-1][0], "-stack-select-frame 3")
        with self.assertRaisesRegex(ValueError, "0..63"):
            self.backend.select_frame(64)

    def test_breakpoint_manager_rows_and_enable_toggle(self):
        rows = self.backend.list_breakpoints()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].number, 1)
        self.assertTrue(rows[0].enabled)
        self.assertEqual(rows[0].hit_count, 3)
        self.assertEqual(rows[1].location, "motor.rpm")
        self.backend.set_breakpoint_enabled(2, True)
        self.assertEqual(self.gdb.calls[-1][0], "-break-enable 2")

    def test_var_object_identifiers_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.backend.list_children("x; monitor erase")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.backend.delete_watch("x\n-gdb-exit")


if __name__ == "__main__":
    unittest.main()
