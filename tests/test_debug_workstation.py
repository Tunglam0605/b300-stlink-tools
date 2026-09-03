from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from b300_core.debug_session import DebugSessionInfo
from b300_core.debug_workstation import DebugWorkstationController
from b300_core.remote_session import RemoteForward, RemoteSessionState


class FakeCredentialStore:
    def __init__(self, secret=None):
        self.secret = secret

    def load(self, profile):
        return self.secret


class FakeRemoteSession:
    def __init__(self, *, connected=True, remembered=None):
        self.connected = connected
        self.endpoint = "Admin@192.168.1.145:22"
        self.profile = SimpleNamespace()
        self.credential_store = FakeCredentialStore(remembered)
        self.login_calls = []
        self.disconnect_calls = []
        self.forward_calls = 0

    def ensure_connected(self, password=None, *, remember=False, timeout_seconds=30.0):
        self.login_calls.append((password, remember, timeout_seconds))
        self.connected = True
        return self.check_health()

    def check_health(self):
        return RemoteSessionState(
            state="connected" if self.connected else "disconnected",
            endpoint=self.endpoint,
            authenticated=self.connected,
            forwards=("gdb", "tcl") if self.forward_calls else (),
        )

    def open_debug_forwards(self):
        self.forward_calls += 1
        return (
            RemoteForward("gdb", "127.0.0.1", 13333, "127.0.0.1", 3333),
            RemoteForward("tcl", "127.0.0.1", 16666, "127.0.0.1", 6666),
        )

    def disconnect(self, *, forget_password=False):
        self.disconnect_calls.append(forget_password)
        self.connected = False


class FakeGdb:
    def current_frame(self):
        return SimpleNamespace(
            function="Motor_Update", file="motor.c", fullname="C:/fw/motor.c",
            line=127, address=0x080146A8,
        )


class FakeDebugSession:
    def __init__(self):
        self.active = False
        self.gdb = FakeGdb()
        self.tcl = None
        self.target_state = "running"
        self.start_calls = []
        self.external_calls = []
        self.stop_calls = 0

    def start(self, config):
        self.start_calls.append(config)
        self.active = True
        self.tcl = object()
        return DebugSessionInfo(
            state="CONNECTED", gdb_endpoint="127.0.0.1:3333",
            tcl_endpoint="127.0.0.1:6666", symbols="local.axf",
            tcl_version="OpenOCD", initial_target_state="running",
        )

    def start_external(self, **kwargs):
        self.external_calls.append(kwargs)
        self.active = True
        self.tcl = object()
        symbols = kwargs.get("symbol_file")
        return DebugSessionInfo(
            state="CONNECTED", gdb_endpoint="127.0.0.1:%d" % kwargs["gdb_port"],
            tcl_endpoint="127.0.0.1:%d" % kwargs["tcl_port"],
            symbols=str(symbols) if symbols is not None else None,
            tcl_version="OpenOCD", initial_target_state=self.target_state,
        )

    def target_poll(self):
        return self.target_state

    def stop(self):
        self.stop_calls += 1
        self.active = False
        self.tcl = None


class DebugWorkstationControllerTests(unittest.TestCase):
    def test_remote_login_is_one_controller_action_and_can_use_remembered_password(self):
        remote = FakeRemoteSession(connected=False, remembered="saved")
        controller = DebugWorkstationController(debug_session=FakeDebugSession(), remote_session=remote)
        self.assertTrue(controller.has_remembered_remote_password())
        state = controller.remote_login(None, remember=True, timeout_seconds=45)
        self.assertTrue(state.authenticated)
        self.assertEqual(remote.login_calls, [(None, True, 45)])
        self.assertEqual(controller.mode, "client")

    def test_client_debug_uses_forwarded_endpoints_without_relogin(self):
        remote = FakeRemoteSession(connected=True)
        debug = FakeDebugSession()
        controller = DebugWorkstationController(debug_session=debug, remote_session=remote)
        info = controller.start_client(Path("firmware.axf"))
        self.assertEqual(info.gdb_endpoint, "127.0.0.1:13333")
        self.assertEqual(info.tcl_endpoint, "127.0.0.1:16666")
        self.assertEqual(remote.login_calls, [])
        self.assertEqual(remote.forward_calls, 1)
        self.assertEqual(debug.external_calls[0]["gdb_port"], 13333)
        self.assertEqual(debug.external_calls[0]["tcl_port"], 16666)
        self.assertIsNotNone(controller.workspace)

    def test_stopping_debug_keeps_remote_session_authenticated(self):
        remote = FakeRemoteSession(connected=True)
        debug = FakeDebugSession()
        controller = DebugWorkstationController(debug_session=debug, remote_session=remote)
        controller.start_client(Path("firmware.axf"))
        controller.stop_interactive()
        self.assertFalse(debug.active)
        self.assertTrue(remote.connected)
        self.assertEqual(remote.disconnect_calls, [])

    def test_client_requires_authenticated_remote_session(self):
        controller = DebugWorkstationController(
            debug_session=FakeDebugSession(), remote_session=FakeRemoteSession(connected=False)
        )
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            controller.start_client(Path("firmware.axf"))

    def test_connection_state_is_compact_and_reports_pc_only_when_halted(self):
        remote = FakeRemoteSession(connected=True)
        debug = FakeDebugSession()
        controller = DebugWorkstationController(debug_session=debug, remote_session=remote)
        controller.start_client(Path("firmware.axf"))
        running = controller.connection_state()
        self.assertEqual((running.ssh, running.gdb, running.tcl, running.target),
                         ("connected", "connected", "connected", "running"))
        self.assertIsNone(running.pc)
        debug.target_state = "halted"
        halted = controller.connection_state()
        self.assertEqual(halted.pc, 0x080146A8)

    def test_disconnect_remote_stops_interactive_first_and_can_forget_password(self):
        remote = FakeRemoteSession(connected=True)
        debug = FakeDebugSession()
        controller = DebugWorkstationController(debug_session=debug, remote_session=remote)
        controller.start_client(Path("firmware.axf"))
        controller.disconnect_remote(forget_password=True)
        self.assertEqual(debug.stop_calls, 1)
        self.assertEqual(remote.disconnect_calls, [True])

    def test_remote_session_cannot_be_replaced_while_interactive_debug_is_active(self):
        remote = FakeRemoteSession(connected=True)
        controller = DebugWorkstationController(debug_session=FakeDebugSession(), remote_session=remote)
        controller.start_client(Path("firmware.axf"))
        with self.assertRaisesRegex(RuntimeError, "Cannot replace"):
            controller.set_remote_session(FakeRemoteSession(connected=True))


if __name__ == "__main__":
    unittest.main()
