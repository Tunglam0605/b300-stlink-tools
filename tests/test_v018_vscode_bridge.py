from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from b300_core.debug_service import DebugState
from b300_core.models import ProbeRef
from b300_core.remote_session import RemoteForward
from b300_core.vscode_bridge import (
    BridgeState,
    DebugRole,
    VsCodeDebugBridge,
    VsCodeExternalProfile,
)


class FakeDebugService:
    def __init__(self) -> None:
        self.current = DebugState.STOPPED
        self.last_config = None
        self.starts = 0
        self.stops = 0

    def start(self, config, readiness_timeout_seconds=3.0, event_sink=None):
        self.last_config = config
        self.starts += 1
        self.current = DebugState.READY
        return self.current

    def poll(self):
        return self.current

    def stop(self):
        self.stops += 1
        self.current = DebugState.STOPPED
        return self.current


class FakeRemoteSession:
    def __init__(self, *, connected=True, local_port=43333) -> None:
        self.connected = connected
        self.local_port = local_port
        self.opened = []
        self.closed = []
        self.disconnected = False

    def open_forward(self, name, *, remote_port, local_port=0,
                     remote_host="127.0.0.1", local_host="127.0.0.1"):
        self.opened.append((name, remote_port, local_port, remote_host, local_host))
        return RemoteForward(
            name=name,
            local_host=local_host,
            local_port=self.local_port,
            remote_host=remote_host,
            remote_port=remote_port,
        )

    def close_forward(self, name):
        self.closed.append(name)
        return True

    def disconnect(self):
        self.disconnected = True


class V018VsCodeBridgeTests(unittest.TestCase):
    def test_external_profile_is_attach_only_and_loopback_only(self) -> None:
        profile = VsCodeExternalProfile(
            name="B300 local",
            executable="${workspaceFolder}/build/application.elf",
            gdb_target="127.0.0.1:3333",
        )
        config = profile.configuration()
        self.assertEqual(config["type"], "cortex-debug")
        self.assertEqual(config["request"], "attach")
        self.assertEqual(config["servertype"], "external")
        self.assertEqual(config["gdbTarget"], "127.0.0.1:3333")
        self.assertTrue(config["hardwareBreakpoints"]["require"])
        self.assertTrue(config["hardwareWatchpoints"]["require"])
        self.assertNotIn("load", json.dumps(config).lower())

        with self.assertRaises(ValueError):
            VsCodeExternalProfile(
                name="unsafe",
                executable="${workspaceFolder}/build/application.elf",
                gdb_target="192.168.1.10:3333",
            ).validate()

    def test_local_mode_starts_openocd_loopback_with_gdb_only(self) -> None:
        debug = FakeDebugService()
        bridge = VsCodeDebugBridge(debug_service=debug)
        state = bridge.start_local(ProbeRef("STLINK123"), gdb_port=3333)
        self.assertEqual(state.role, DebugRole.LOCAL)
        self.assertEqual(state.state, BridgeState.READY)
        self.assertEqual(state.gdb_target, "127.0.0.1:3333")
        self.assertEqual(debug.last_config.bind_address, "127.0.0.1")
        self.assertEqual(debug.last_config.gdb_port, 3333)
        self.assertIsNone(debug.last_config.telnet_port)
        self.assertIsNone(debug.last_config.tcl_port)
        bridge.stop()
        self.assertEqual(debug.stops, 1)

    def test_gateway_mode_never_requests_public_openocd(self) -> None:
        debug = FakeDebugService()
        bridge = VsCodeDebugBridge(debug_service=debug)
        state = bridge.start_gateway(ProbeRef("STLINK123"), gdb_port=3333)
        self.assertEqual(state.role, DebugRole.GATEWAY)
        self.assertEqual(debug.last_config.bind_address, "127.0.0.1")
        self.assertIn("private", state.detail.lower())

    def test_client_mode_forwards_gateway_loopback_to_dynamic_local_port(self) -> None:
        debug = FakeDebugService()
        session = FakeRemoteSession(local_port=43333)
        bridge = VsCodeDebugBridge(debug_service=debug)
        state = bridge.start_client(session, remote_gdb_port=3333, local_gdb_port=0)
        self.assertEqual(state.role, DebugRole.CLIENT)
        self.assertEqual(state.state, BridgeState.READY)
        self.assertEqual(state.gdb_target, "127.0.0.1:43333")
        self.assertEqual(
            session.opened,
            [("vscode_gdb", 3333, 0, "127.0.0.1", "127.0.0.1")],
        )
        bridge.stop()
        self.assertEqual(session.closed, ["vscode_gdb"])
        self.assertFalse(session.disconnected)
        self.assertEqual(debug.starts, 0)

    def test_client_requires_authenticated_remote_session(self) -> None:
        bridge = VsCodeDebugBridge(debug_service=FakeDebugService())
        with self.assertRaises(RuntimeError):
            bridge.start_client(FakeRemoteSession(connected=False))

    def test_profile_uses_bridge_endpoint_and_workspace_relative_symbols(self) -> None:
        bridge = VsCodeDebugBridge(debug_service=FakeDebugService())
        bridge.start_local(ProbeRef("STLINK123"))
        profile = bridge.profile(
            program_relative="build/application.elf",
            gdb_path="arm-none-eabi-gdb",
        )
        self.assertEqual(profile.gdb_target, "127.0.0.1:3333")
        self.assertEqual(profile.executable, "${workspaceFolder}/build/application.elf")

    def test_launch_writer_is_fail_closed_for_existing_configuration(self) -> None:
        profile = VsCodeExternalProfile(
            name="B300 local",
            executable="${workspaceFolder}/build/application.elf",
            gdb_target="127.0.0.1:3333",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = profile.write_launch_json(root)
            self.assertTrue(output.is_file())
            with self.assertRaises(FileExistsError):
                profile.write_launch_json(root)


if __name__ == "__main__":
    unittest.main()
