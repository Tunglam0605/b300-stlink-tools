from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core.models import ProbeRef
from b300_core.vscode_bridge import BridgeState, DebugRole, VsCodeBridgeState, VsCodeExternalProfile
from b300_core.vscode_environment import VsCodeEnvironmentStatus
from b300_gui.vscode_debug_controller import VsCodeDebugController


READY_ENV = VsCodeEnvironmentStatus(
    vscode_ready=True,
    cortex_debug_ready=True,
    gdb_ready=True,
    vscode_path="/opt/vscode/code",
    gdb_path="/opt/b300/vendor/gdb/bin/arm-none-eabi-gdb",
)


class V018VsCodeControllerTests(unittest.TestCase):
    def _workspace(self, root: Path):
        workspace = root / "project"
        symbols = workspace / "build" / "application.elf"
        symbols.parent.mkdir(parents=True)
        symbols.write_bytes(b"ELF")
        return workspace, symbols

    def test_existing_unrelated_launch_json_is_preserved_when_profile_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            vscode = workspace / ".vscode"
            vscode.mkdir()
            launch_json = vscode / "launch.json"
            launch_json.write_text(
                '{"configurations":[{"name":"Python","type":"debugpy"}]}',
                encoding="utf-8",
            )
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            bridge = mock.Mock()
            bridge.start_local.return_value = VsCodeBridgeState(
                role=DebugRole.LOCAL, state=BridgeState.READY,
                gdb_target="127.0.0.1:3333",
            )
            bridge.profile.return_value = VsCodeExternalProfile(
                "B300 local", "${workspaceFolder}/build/application.elf",
                "127.0.0.1:3333", gdb_path=READY_ENV.gdb_path or "arm-none-eabi-gdb",
            )
            controller.bridge = bridge
            with mock.patch("b300_gui.vscode_debug_controller.launch_vscode"):
                controller.start_local(
                    probe=ProbeRef("probe"), workspace=workspace, symbols=symbols
                )
            payload = __import__("json").loads(launch_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["configurations"][0]["b300"]["owner"], "b300-stlink-tools")
            self.assertEqual(payload["configurations"][1], {"name": "Python", "type": "debugpy"})

    def test_local_launch_uses_backend_profile_and_shell_free_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            bridge = mock.Mock()
            ready = VsCodeBridgeState(
                role=DebugRole.LOCAL, state=BridgeState.READY,
                gdb_target="127.0.0.1:3333",
            )
            bridge.start_local.return_value = ready
            bridge.profile.return_value = VsCodeExternalProfile(
                name="B300 test",
                executable="${workspaceFolder}/build/application.elf",
                gdb_target="127.0.0.1:3333",
                gdb_path=READY_ENV.gdb_path or "arm-none-eabi-gdb",
            )
            controller.bridge = bridge
            with mock.patch("b300_gui.vscode_debug_controller.launch_vscode") as launch:
                result = controller.start_local(
                    probe=ProbeRef("probe"), workspace=workspace, symbols=symbols
                )
            bridge.start_local.assert_called_once()
            self.assertTrue(result.launch_json.is_file())
            launch.assert_called_once_with(workspace.resolve(), executable=READY_ENV.vscode_path)
            payload = result.launch_json.read_text(encoding="utf-8")
            self.assertIn('"request": "attach"', payload)
            self.assertIn('"gdbTarget": "127.0.0.1:3333"', payload)
            self.assertIn('"require": true', payload)

    def test_profile_failure_stops_bridge_and_releases_debug_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            bridge = mock.Mock()
            bridge.start_local.return_value = VsCodeBridgeState(
                role=DebugRole.LOCAL, state=BridgeState.READY,
                gdb_target="127.0.0.1:3333",
            )
            bridge.profile.side_effect = RuntimeError("profile failed")
            controller.bridge = bridge
            with self.assertRaisesRegex(RuntimeError, "profile failed"):
                controller.start_local(
                    probe=ProbeRef("probe"), workspace=workspace, symbols=symbols
                )
            bridge.stop.assert_called_once()

    def test_symbols_must_stay_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "project"
            workspace.mkdir()
            symbols = root / "outside.elf"
            symbols.write_bytes(b"ELF")
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            controller.bridge = mock.Mock()
            with self.assertRaisesRegex(ValueError, "inside"):
                controller.start_local(
                    probe=ProbeRef("probe"), workspace=workspace, symbols=symbols
                )
            controller.bridge.start_local.assert_not_called()

    def test_client_uses_only_bridge_client_forward_and_dynamic_port_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            bridge = mock.Mock()
            bridge.start_client.return_value = VsCodeBridgeState(
                role=DebugRole.CLIENT, state=BridgeState.READY,
                gdb_target="127.0.0.1:45123", tunnel_name="vscode_gdb",
            )
            bridge.profile.return_value = VsCodeExternalProfile(
                name="B300 remote",
                executable="${workspaceFolder}/build/application.elf",
                gdb_target="127.0.0.1:45123",
                gdb_path=READY_ENV.gdb_path or "arm-none-eabi-gdb",
            )
            controller.bridge = bridge
            session = mock.Mock()
            snapshot = type("Snapshot", (), {
                "state": "READY", "instance_id": "gw", "generation": 1,
                "gdb_endpoint": "127.0.0.1:3333",
            })()
            with mock.patch("b300_gui.vscode_debug_controller.launch_vscode"):
                result = controller.start_client(
                    session=session, workspace=workspace, symbols=symbols,
                    local_gdb_port=0, gateway_snapshot=snapshot, profile_id="lab",
                )
            bridge.start_client.assert_called_once_with(
                session, local_gdb_port=0, snapshot=snapshot, profile_id="lab"
            )
            self.assertEqual(result.state.gdb_target, "127.0.0.1:45123")

    def test_client_gateway_not_ready_never_writes_launch_or_opens_vscode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            bridge = mock.Mock()
            bridge.start_client.side_effect = RuntimeError("Gateway snapshot must be READY")
            controller.bridge = bridge
            snapshot = type("Snapshot", (), {
                "state": "STARTING", "instance_id": "gw", "generation": 1,
                "gdb_endpoint": None,
            })()
            with mock.patch("b300_gui.vscode_debug_controller.launch_vscode") as launch:
                with self.assertRaisesRegex(RuntimeError, "READY"):
                    controller.start_client(
                        session=mock.Mock(), workspace=workspace, symbols=symbols,
                        gateway_snapshot=snapshot, profile_id="lab",
                    )
            self.assertFalse((workspace / ".vscode" / "launch.json").exists())
            launch.assert_not_called()
            bridge.profile.assert_not_called()

    def test_client_ensures_gateway_ready_before_starting_tunnel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            controller.bridge = mock.Mock()
            session = mock.Mock()
            session.ensure_gateway_ready.side_effect = RuntimeError("Gateway is not ready: WAITING_PROBE")
            with mock.patch("b300_gui.vscode_debug_controller.launch_vscode") as launch:
                with self.assertRaisesRegex(RuntimeError, "WAITING_PROBE"):
                    controller.start_client(
                        session=session, workspace=workspace, symbols=symbols,
                        profile_id="lab",
                    )
            session.ensure_gateway_ready.assert_called_once()
            controller.bridge.start_client.assert_not_called()
            self.assertFalse((workspace / ".vscode" / "launch.json").exists())
            launch.assert_not_called()

    def test_client_requires_explicit_gateway_profile_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            controller.bridge = mock.Mock()
            session = mock.Mock()
            session.endpoint = "operator@gateway.example:22"
            session.ensure_gateway_ready.return_value = type("Snapshot", (), {
                "state": "READY", "instance_id": "gw", "generation": 1,
                "gdb_endpoint": "127.0.0.1:3333",
            })()

            with self.assertRaisesRegex(ValueError, "profile identity"):
                controller.start_client(
                    session=session, workspace=workspace, symbols=symbols,
                )

            controller.bridge.start_client.assert_not_called()

    def test_controller_passes_snapshot_binding_and_revision_to_atomic_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            launch_json = workspace / ".vscode" / "launch.json"
            launch_json.parent.mkdir()
            launch_json.write_text('{"configurations":[]}', encoding="utf-8")
            revision = VsCodeExternalProfile.launch_revision(workspace)
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            bridge = mock.Mock()
            state = VsCodeBridgeState(
                role=DebugRole.CLIENT, state=BridgeState.READY,
                gdb_target="127.0.0.1:45123", tunnel_name="vscode_gdb",
            )
            bridge.start_client.return_value = state
            profile = mock.Mock()
            profile.write_launch_json.return_value = workspace / ".vscode" / "launch.json"
            bridge.profile.return_value = profile
            controller.bridge = bridge
            snapshot = type("Snapshot", (), {
                "state": "READY", "instance_id": "gw", "generation": 4,
                "gdb_endpoint": "127.0.0.1:4333",
            })()
            with mock.patch("b300_gui.vscode_debug_controller.launch_vscode"):
                controller.start_client(
                    session=mock.Mock(), workspace=workspace, symbols=symbols,
                    gateway_snapshot=snapshot, profile_id="lab",
                )
            bridge.start_client.assert_called_once_with(
                mock.ANY, local_gdb_port=0, snapshot=snapshot, profile_id="lab"
            )
            profile.write_launch_json.assert_called_once_with(
                workspace.resolve(), force=False, expected_revision=revision
            )

    def test_recovery_sync_updates_managed_profile_without_opening_vscode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            bridge = mock.Mock()
            state = VsCodeBridgeState(
                role=DebugRole.CLIENT, state=BridgeState.READY,
                gdb_target="127.0.0.1:45123", tunnel_name="vscode_gdb",
            )
            bridge.sync_client.return_value = state
            profile = mock.Mock()
            profile.write_launch_json.return_value = workspace / ".vscode" / "launch.json"
            bridge.profile.return_value = profile
            controller.bridge = bridge
            session = mock.Mock()
            snapshot = type("Snapshot", (), {
                "state": "READY", "instance_id": "gw-new", "generation": 2,
                "gdb_endpoint": "127.0.0.1:4333",
            })()
            with mock.patch("b300_gui.vscode_debug_controller.launch_vscode") as launch:
                result = controller.synchronize_client(
                    session=session, workspace=workspace, symbols=symbols,
                    gateway_snapshot=snapshot, profile_id="lab",
                )
            bridge.sync_client.assert_called_once_with(
                session, snapshot=snapshot, profile_id="lab", local_gdb_port=0
            )
            profile.write_launch_json.assert_called_once_with(
                workspace.resolve(), force=False, expected_revision=None
            )
            launch.assert_not_called()
            self.assertEqual(result.state.gdb_target, "127.0.0.1:45123")


if __name__ == "__main__":
    unittest.main()
