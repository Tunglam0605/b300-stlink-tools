from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from b300_core.debug_service import DebugState
from b300_core.models import ProbeRef
from b300_core.remote_session import RemoteForward, RemoteForwardError
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
        self.event_sink = None

    def start(self, config, readiness_timeout_seconds=3.0, event_sink=None):
        self.last_config = config
        self.starts += 1
        self.event_sink = event_sink
        self.current = DebugState.READY
        return self.current

    def emit(self, line: str) -> None:
        if self.event_sink is not None:
            self.event_sink(line)

    def poll(self):
        return self.current

    def stop(self):
        self.stops += 1
        self.current = DebugState.STOPPED
        return self.current


class FakeTclClient:
    def __init__(self, state: str = "running") -> None:
        self.state = state
        self.resume_count = 0

    def wait_target_state(self):
        return self.state

    def resume_target(self):
        self.resume_count += 1
        self.state = "running"
        return self.state


class FakeRemoteSession:
    def __init__(self, *, connected=True, local_port=43333, listener_ready=True) -> None:
        self.connected = connected
        self.local_port = local_port
        self.opened = []
        self.closed = []
        self.disconnected = False
        self.listener_ready = listener_ready
        self.checked_ports = []

    def require_remote_listener(self, *, remote_port, timeout_seconds=3.0):
        self.checked_ports.append(remote_port)
        if not self.listener_ready:
            raise RemoteForwardError("Gateway listener unavailable")

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
    def make_server_bridge(self, debug=None, *, initial_state="running"):
        selected_debug = debug or FakeDebugService()
        tcl = FakeTclClient(initial_state)
        bridge = VsCodeDebugBridge(
            debug_service=selected_debug,
            tcl_factory=lambda _endpoint: tcl,
        )
        return bridge, selected_debug, tcl

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

    def test_local_mode_starts_openocd_loopback_with_private_guard_tcl(self) -> None:
        bridge, debug, _tcl = self.make_server_bridge()
        state = bridge.start_local(ProbeRef("STLINK123"), gdb_port=3333)
        self.assertEqual(state.role, DebugRole.LOCAL)
        self.assertEqual(state.state, BridgeState.READY)
        self.assertEqual(state.gdb_target, "127.0.0.1:3333")
        self.assertEqual(state.initial_target_state, "running")
        self.assertEqual(debug.last_config.bind_address, "127.0.0.1")
        self.assertEqual(debug.last_config.gdb_port, 3333)
        self.assertIsNone(debug.last_config.telnet_port)
        self.assertEqual(debug.last_config.tcl_port, 6666)
        bridge.stop()
        self.assertEqual(debug.stops, 1)

    def test_gateway_mode_never_requests_public_openocd(self) -> None:
        bridge, debug, _tcl = self.make_server_bridge()
        state = bridge.start_gateway(ProbeRef("STLINK123"), gdb_port=3333)
        self.assertEqual(state.role, DebugRole.GATEWAY)
        self.assertEqual(debug.last_config.bind_address, "127.0.0.1")
        self.assertEqual(debug.last_config.tcl_port, 6666)
        self.assertIn("private", state.detail.lower())

    def test_gdb_disconnect_restores_running_target_without_forwarding_tcl(self) -> None:
        bridge, debug, tcl = self.make_server_bridge(initial_state="running")
        bridge.start_gateway(ProbeRef("STLINK123"))
        debug.emit("Info : accepting 'gdb' connection on tcp/3333")
        tcl.state = "halted"
        debug.emit("Info : dropped 'gdb' connection")
        self.assertEqual(tcl.state, "running")
        self.assertEqual(tcl.resume_count, 1)
        self.assertIn("restored", bridge.state.detail.lower())

    def test_bridge_stop_restores_target_if_debugger_left_it_halted(self) -> None:
        bridge, _debug, tcl = self.make_server_bridge(initial_state="running")
        bridge.start_local(ProbeRef("STLINK123"))
        tcl.state = "halted"
        stopped = bridge.stop()
        self.assertEqual(stopped.state, BridgeState.STOPPED)
        self.assertEqual(tcl.state, "running")
        self.assertEqual(tcl.resume_count, 1)
        self.assertIn("restored", stopped.detail.lower())

    def test_client_mode_forwards_gateway_loopback_gdb_only_to_dynamic_local_port(self) -> None:
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
        self.assertEqual(session.checked_ports, [3333])

    def test_client_missing_gateway_listener_never_opens_forward_or_becomes_ready(self):
        session = FakeRemoteSession(listener_ready=False)
        bridge = VsCodeDebugBridge(debug_service=FakeDebugService())
        with self.assertRaisesRegex(RemoteForwardError, "Start.*Gateway"):
            bridge.start_client(session)
        self.assertEqual(session.opened, [])
        self.assertEqual(bridge.state.state, BridgeState.STOPPED)
        self.assertIsNone(bridge.state.gdb_target)
        self.assertFalse(session.disconnected)

    def test_client_requires_authenticated_remote_session(self) -> None:
        bridge = VsCodeDebugBridge(debug_service=FakeDebugService())
        with self.assertRaises(RuntimeError):
            bridge.start_client(FakeRemoteSession(connected=False))

    def test_profile_uses_bridge_endpoint_and_workspace_relative_symbols(self) -> None:
        bridge, _debug, _tcl = self.make_server_bridge()
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

    def test_launch_writer_preserves_jsonc_workspace_and_replaces_only_named_profile(self):
        original = '''\ufeff{
          // Team launch profiles
          "version": "0.2.0",
          "inputs": [{"id": "path", "default": "https://host/a/*b*/,]",}],
          "compounds": [{"name": "All", "configurations": ["Python", "B300 local"],}],
          "configurations": [
            {"name": "Python", "type": "debugpy", "args": ["a", "b",],},
            /* Managed attach */ {"name": "B300 local", "gdbTarget": "old"},
            {"name": "Other board", "type": "cortex-debug"},
          ],
        }'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".vscode" / "launch.json"
            output.parent.mkdir()
            output.write_text(original, encoding="utf-8")
            profile = VsCodeExternalProfile("B300 local", "app.elf", "127.0.0.1:3333")
            profile.write_launch_json(root, force=True)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["inputs"], [{"id": "path", "default": "https://host/a/*b*/,]"}])
            self.assertEqual(result["compounds"], [{"name": "All", "configurations": ["Python", "B300 local"]}])
            self.assertEqual(result["configurations"][0], {"name": "Python", "type": "debugpy", "args": ["a", "b"]})
            self.assertEqual(result["configurations"][2], {"name": "Other board", "type": "cortex-debug"})
            self.assertEqual(len(result["configurations"]), 3)
            self.assertEqual(result["configurations"][1]["gdbTarget"], "127.0.0.1:3333")
            self.assertEqual(result["configurations"][1]["request"], "attach")

    def test_launch_writer_appends_managed_profile_to_existing_document(self):
        for original in ({"inputs": []}, {"configurations": [{"name": "Other"}]}):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / ".vscode" / "launch.json"
                output.parent.mkdir()
                output.write_text(json.dumps(original), encoding="utf-8")
                VsCodeExternalProfile("B300 local", "app.elf", "127.0.0.1:3333").write_launch_json(root, force=True)
                result = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(result["configurations"][:-1], original.get("configurations", []))
                self.assertEqual(result["configurations"][-1]["name"], "B300 local")
                if "inputs" in original:
                    self.assertEqual(result["inputs"], [])

    def test_launch_writer_refuses_malformed_or_ambiguous_document_without_changes(self):
        documents = [
            '{"configurations": [', '[]', '{"configurations": {}}',
            '{"configurations": [null]}', '{"configurations": [,]}',
            '{"configurations": []} /* unfinished',
            '{"configurations": [], "inputs": NaN}',
            '{"configurations": [], "configurations": []}',
            '{"configurations": [{"name":"B300 local"},{"name":"B300 local"}]}',
        ]
        for original in documents:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / ".vscode" / "launch.json"
                output.parent.mkdir()
                output.write_text(original, encoding="utf-8")
                before = output.read_bytes()
                with self.assertRaises(ValueError):
                    VsCodeExternalProfile("B300 local", "app.elf", "127.0.0.1:3333").write_launch_json(root, force=True)
                self.assertEqual(output.read_bytes(), before)

    def test_launch_writer_failed_atomic_replace_preserves_original_and_cleans_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".vscode" / "launch.json"
            output.parent.mkdir()
            original = b'{"configurations": [{"name": "Other"}]}'
            output.write_bytes(original)
            with patch("b300_core.vscode_bridge.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    VsCodeExternalProfile("B300 local", "app.elf", "127.0.0.1:3333").write_launch_json(root, force=True)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(list(output.parent.iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
