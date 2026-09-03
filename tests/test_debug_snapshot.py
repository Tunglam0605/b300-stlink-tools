from __future__ import annotations

import unittest
from types import SimpleNamespace

from b300_core.debug_snapshot import DebugSnapshotBackend
from b300_core.debug_workspace import (
    DebugBreakpoint,
    DebugRegister,
    DebugSourceLocation,
    DebugVariableNode,
)


class FakeWorkspace:
    def __init__(self, state="halted"):
        self.target_state = state
        self.selected_frames = []

    def current_location(self):
        return DebugSourceLocation("Motor_Update", "motor.c", "C:/fw/motor.c", 127, 0x080146A8)

    def call_stack(self, max_frames=16):
        return (
            SimpleNamespace(level=0, function="Motor_Update", file="motor.c", line=127, address=0x080146A8),
            SimpleNamespace(level=1, function="Control_Task", file="control.c", line=301, address=0x08015310),
        )[:max_frames]

    def list_locals(self):
        return (
            DebugVariableNode("var1", "motor", "{...}", "Motor_t", None, True, True),
        )

    def registers(self):
        return (DebugRegister("pc", "0x080146a8", False),)

    def list_breakpoints(self):
        return (
            DebugBreakpoint(1, True, "hw breakpoint", "Motor_Update", "0x08014620", 3),
            DebugBreakpoint(2, True, "watchpoint", "motor.rpm", None, 1),
        )

    def select_frame(self, level):
        self.selected_frames.append(level)
        return level


class DebugSnapshotTests(unittest.TestCase):
    def test_capture_returns_coherent_engineering_panes(self):
        backend = DebugSnapshotBackend(FakeWorkspace())
        snapshot = backend.capture()
        self.assertEqual(snapshot.location.function, "Motor_Update")
        self.assertEqual(len(snapshot.frames), 2)
        self.assertEqual(snapshot.locals[0].name, "motor")
        self.assertEqual(snapshot.registers[0].name, "pc")
        self.assertEqual(snapshot.breakpoint_usage.breakpoints, 1)
        self.assertEqual(snapshot.breakpoint_usage.watchpoints, 1)
        self.assertEqual(snapshot.breakpoint_usage.breakpoint_limit, 6)
        self.assertEqual(snapshot.breakpoint_usage.watchpoint_limit, 4)

    def test_select_frame_then_recaptures_same_structured_state(self):
        workspace = FakeWorkspace()
        backend = DebugSnapshotBackend(workspace)
        snapshot = backend.select_frame_and_capture(1)
        self.assertEqual(workspace.selected_frames, [1])
        self.assertEqual(snapshot.frames[1].function, "Control_Task")

    def test_snapshot_refuses_running_target(self):
        backend = DebugSnapshotBackend(FakeWorkspace("running"))
        with self.assertRaisesRegex(RuntimeError, "HALTED"):
            backend.capture()


if __name__ == "__main__":
    unittest.main()
