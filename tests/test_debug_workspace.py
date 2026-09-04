from __future__ import annotations

import unittest
from types import SimpleNamespace

from b300_core.debug_workspace import DebugWorkspaceBackend


class Result:
    def __init__(self, payload: str):
        self.payload = payload


class FakeGdb:
    def __init__(self):
        self.calls = []
        self._next_var = 1
        self._async_records = []
        self._register_round = 0

    @staticmethod
    def _quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @property
    def async_records(self):
        return tuple(self._async_records)

    def evaluate_variable(self, expression: str):
        self.calls.append(("evaluate", expression))
        if "(" in expression:
            raise ValueError("unsupported")
        return object()

    def current_frame(self):
        return SimpleNamespace(
            function="Motor_Update", file="motor.c", fullname="C:/fw/motor.c",
            line=127, address=0x080146A8,
        )

    def stack_frames(self, max_frames):
        return (
            SimpleNamespace(level=0, function="Motor_Update", file="motor.c", line=127, address=0x080146A8),
            SimpleNamespace(level=1, function="Control_Task", file="control.c", line=301, address=0x08015310),
        )[:max_frames]

    def register_values(self):
        self._register_round += 1
        pc = "0x080146A8" if self._register_round == 1 else "0x080146AC"
        return (
            SimpleNamespace(name="r0", value="0x1"),
            SimpleNamespace(name="pc", value=pc),
        )

    def insert_hardware_breakpoint(self, location):
        self.calls.append(("insert-hw", location))
        return SimpleNamespace(number=4)

    def insert_watchpoint(self, expression):
        self.calls.append(("insert-watch", expression))
        return SimpleNamespace(number=5)

    def delete_breakpoint(self, number):
        self.calls.append(("delete-break", number))

    def wait_for_stopped(self, **kwargs):
        self.calls.append(("wait-stopped", kwargs))
        return object()

    def _request(self, command, accepted):
        self.calls.append((command, accepted))
        if command.startswith("-stack-list-variables"):
            return Result(
                'variables=[variable={name="motor",value="{...}",type="Motor_t"},'
                'variable={name="tick",value="42",type="uint32_t"}]'
            )
        if command.startswith("-var-create"):
            expression = command.rsplit('"', 2)[1]
            if expression == "motor":
                payload = 'name="var_motor",numchild="2",value="{...}",type="Motor_t"'
            elif expression == "tick":
                payload = 'name="var_tick",numchild="0",value="42",type="uint32_t"'
            else:
                payload = 'name="var%d",numchild="3",value="{...}",type="Motor_t"' % self._next_var
                self._next_var += 1
            return Result(payload)
        if command.startswith("-var-list-children"):
            return Result(
                'numchild="2",children=['
                'child={name="var_motor.speed",exp="speed",numchild="0",value="1200",type="int"},'
                'child={name="var_motor.pid",exp="pid",numchild="3",value="{...}",type="PID_t"}]'
            )
        if command.startswith("-var-evaluate-expression"):
            if "var_tick" in command:
                return Result('value="43"')
            return Result('value="1210"')
        if command.startswith("-var-update"):
            return Result(
                'changelist=[{name="var_motor.speed",value="1220",in_scope="true"},'
                '{name="var_motor.pid",value="{...}",new_num_children="3",in_scope="true"}]'
            )
        if command.startswith("-var-assign"):
            return Result('value="1800"')
        if command.startswith("-break-list"):
            return Result(
                'BreakpointTable={body=['
                'bkpt={number="1",type="hw breakpoint",enabled="y",addr="0x08012345",'
                'func="Motor_Update",times="3",original-location="Motor_Update"},'
                'bkpt={number="2",type="watchpoint",enabled="n",what="motor.rpm",times="1"}]}'
            )
        if command.startswith("-exec-finish"):
            return Result("")
        return Result("")


class DebugWorkspaceBackendTests(unittest.TestCase):
    def setUp(self):
        self.state = "halted"
        self.gdb = FakeGdb()
        self.backend = DebugWorkspaceBackend(self.gdb, target_state_provider=lambda: self.state)

    def test_locals_are_structured_variable_objects_and_expandable(self):
        rows = self.backend.list_locals()
        self.assertEqual([item.name for item in rows], ["motor", "tick"])
        self.assertTrue(rows[0].has_children)
        self.assertTrue(rows[0].editable)
        children = self.backend.list_children(rows[0].id)
        self.assertEqual([item.name for item in children], ["speed", "pid"])
        self.assertFalse(children[0].has_children)
        self.assertTrue(children[1].has_children)

    def test_create_watch_and_refresh_changed_children(self):
        node = self.backend.create_watch("motor")
        self.assertTrue(node.has_children)
        children = self.backend.list_children("var_motor")
        self.assertEqual(len(children), 2)
        changed = self.backend.refresh_changes()
        self.assertEqual([item.id for item in changed], ["var_motor.speed", "var_motor.pid"])
        self.assertTrue(all(item.changed for item in changed))

    def test_assign_variable_requires_halted_and_rejects_expression(self):
        node = self.backend.create_watch("motor")
        self.assertEqual(self.backend.assign_variable(node.id, "1800"), "1800")
        with self.assertRaisesRegex(ValueError, "simple scalar"):
            self.backend.assign_variable(node.id, "danger()")
        self.state = "running"
        with self.assertRaisesRegex(RuntimeError, "HALTED"):
            self.backend.assign_variable(node.id, "1700")

    def test_select_frame_is_bounded_and_resets_local_objects(self):
        rows = self.backend.list_locals()
        self.assertTrue(rows)
        self.assertEqual(self.backend.select_frame(3), 3)
        commands = [call[0] for call in self.gdb.calls if isinstance(call, tuple) and call and isinstance(call[0], str)]
        self.assertIn("-stack-select-frame 3", commands)
        self.assertTrue(any(command.startswith("-var-delete") for command in commands))
        with self.assertRaisesRegex(ValueError, "0..63"):
            self.backend.select_frame(64)

    def test_source_call_stack_and_register_change_tracking(self):
        location = self.backend.current_location()
        self.assertEqual(location.function, "Motor_Update")
        self.assertEqual(location.line, 127)
        self.assertEqual(len(self.backend.call_stack(8)), 2)
        first = self.backend.registers()
        second = self.backend.registers()
        self.assertFalse(first[1].changed)
        self.assertTrue(second[1].changed)
        self.assertFalse(second[0].changed)

    def test_breakpoint_manager_rows_usage_and_mutations(self):
        rows = self.backend.list_breakpoints()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].number, 1)
        self.assertTrue(rows[0].enabled)
        self.assertEqual(rows[0].hit_count, 3)
        self.assertEqual(rows[1].location, "motor.rpm")
        usage = self.backend.breakpoint_usage()
        self.assertEqual((usage.breakpoints, usage.breakpoint_limit), (1, 6))
        self.assertEqual((usage.watchpoints, usage.watchpoint_limit), (1, 4))
        self.assertEqual(self.backend.create_hardware_breakpoint("Motor_Update"), 4)
        self.assertEqual(self.backend.create_watchpoint("motor.rpm"), 5)
        self.backend.set_breakpoint_enabled(2, True)
        self.backend.delete_breakpoint(2)
        self.assertIn(("delete-break", 2), self.gdb.calls)

    def test_step_out_requires_halted_and_waits_for_stop(self):
        location = self.backend.step_out(timeout_seconds=2.0)
        self.assertEqual(location.address, 0x080146A8)
        self.assertTrue(any(call[0] == "-exec-finish" for call in self.gdb.calls if isinstance(call, tuple)))
        self.assertTrue(any(call[0] == "wait-stopped" for call in self.gdb.calls if isinstance(call, tuple)))
        self.state = "running"
        with self.assertRaisesRegex(RuntimeError, "HALTED"):
            self.backend.step_out()

    def test_var_object_identifiers_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.backend.list_children("x; monitor erase")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.backend.delete_watch("x\n-gdb-exit")


if __name__ == "__main__":
    unittest.main()
