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

    def test_existing_launch_json_blocks_before_hardware_bridge_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace, symbols = self._workspace(Path(directory))
            vscode = workspace / ".vscode"
            vscode.mkdir()
            (vscode / "launch.json").write_text("{}", encoding="utf-8")
            controller = VsCodeDebugController()
            controller._environment = READY_ENV
            controller.bridge = mock.Mock()
            with self.assertRaises(FileExistsError):
                controller.start_local(
                    probe=ProbeRef("probe"), workspace=workspace, symbols=symbols
                )
            controller.bridge.start_local.assert_not_called()

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
            with mock.patch("b300_gui.vscode_debug_controller.launch_vscode"):
                result = controller.start_client(
                    session=session, workspace=workspace, symbols=symbols,
                    local_gdb_port=0,
                )
            bridge.start_client.assert_called_once_with(session, local_gdb_port=0)
            self.assertEqual(result.state.gdb_target, "127.0.0.1:45123")


if __name__ == "__main__":
    unittest.main()
