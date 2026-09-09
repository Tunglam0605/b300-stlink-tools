from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from b300_core.debug_service import DebugState
from b300_core.models import ProbeRef
from b300_core.remote_session import RemoteForward, RemoteForwardError
from b300_core.remote_debug_guard import RemoteDebugGuard
from b300_core.gateway_status import GatewaySnapshot
from b300_core.vscode_bridge import (
    BridgeState,
    DebugRole,
    VsCodeDebugBridge,
    VsCodeExternalProfile,
    launch_vscode,
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
        self.generation = 1

    @property
    def state(self):
        forwards = ("vscode_gdb",) if self.opened and not self.closed else ()
        return type("State", (), {"forwards": forwards, "generation": self.generation})()

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
    @staticmethod
    def gateway_snapshot(*, sequence, count=None, activity=None, ever=None,
                         instance="gateway", generation=1):
        record = {
            "schema_version": 1, "instance_id": instance, "generation": generation,
            "sequence": sequence, "state": "READY", "reason_code": "TARGET_VERIFIED",
            "selected_probe": {"serial": "STLINK123"},
            "gdb_endpoint": "127.0.0.1:3333", "tcl_endpoint": "127.0.0.1:6666",
            "cpu_state": "running", "evidence_age_ms": 0,
        }
        if count is not None:
            record.update(gdb_connection_count=count, gdb_activity_generation=activity,
                          gdb_ever_attached=ever)
        return GatewaySnapshot.from_record(record)
    def make_server_bridge(self, debug=None, *, initial_state="running", guard_factory=RemoteDebugGuard):
        selected_debug = debug or FakeDebugService()
        tcl = FakeTclClient(initial_state)
        bridge = VsCodeDebugBridge(
            debug_service=selected_debug,
            tcl_factory=lambda _endpoint: tcl,
            guard_factory=guard_factory,
        )
        return bridge, selected_debug, tcl

    def test_launch_vscode_opens_a_new_window_with_separate_shell_free_argv(self) -> None:
        """Catches a regression that reuses an existing user VS Code window."""
        captured = {}

        def process_factory(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

        with tempfile.TemporaryDirectory(prefix="B300 Workspace ") as directory:
            workspace = Path(directory) / "Firmware Workspace"
            workspace.mkdir()
            executable = Path(directory) / "VS Code" / "Code.exe"
            executable.parent.mkdir()
            executable.touch()
            launch_vscode(
                workspace,
                executable=str(executable),
                process_factory=process_factory,
                platform_name="windows",
            )

        self.assertEqual(
            captured["argv"],
            (
                str(executable.resolve()),
                "--new-window",
                str(workspace.resolve()),
            ),
        )
        self.assertNotIn("--reuse-window", captured["argv"])
        self.assertFalse(captured["kwargs"]["shell"])

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
        self.assertEqual(config["liveWatch"], {"enabled": True, "samplesPerSecond": 4})
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
        self.assertEqual(debug.last_config.gdb_max_connections, 2)
        bridge.stop()
        self.assertEqual(debug.stops, 1)

    def test_gateway_mode_never_requests_public_openocd(self) -> None:
        bridge, debug, _tcl = self.make_server_bridge()
        state = bridge.start_gateway(ProbeRef("STLINK123"), gdb_port=3333)
        self.assertEqual(state.role, DebugRole.GATEWAY)
        self.assertEqual(debug.last_config.bind_address, "127.0.0.1")
        self.assertEqual(debug.last_config.tcl_port, 6666)
        self.assertEqual(debug.last_config.gdb_max_connections, 2)
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

    def test_last_gdb_disconnect_can_stop_the_b300_owned_bridge(self) -> None:
        scheduled = []
        def guard_factory(tcl, **kwargs):
            return RemoteDebugGuard(tcl, reclaim_delay_seconds=0.0,
                                    reclaim_scheduler=lambda _delay, callback: scheduled.append(callback),
                                    **kwargs)
        bridge, debug, _tcl = self.make_server_bridge(guard_factory=guard_factory)
        bridge.set_last_client_detached_handler(bridge.stop_if_generation)
        bridge.start_gateway(ProbeRef("STLINK123"))

        debug.emit("Info : accepting 'gdb' connection on tcp/3333")
        debug.emit("Info : dropped 'gdb' connection")
        self.assertEqual(debug.stops, 0)
        scheduled.pop()()

        self.assertEqual(debug.stops, 1)
        self.assertEqual(bridge.state.state, BridgeState.STOPPED)

    def test_stale_reclaim_cannot_stop_a_restarted_bridge_generation(self) -> None:
        scheduled = []
        def guard_factory(tcl, **kwargs):
            return RemoteDebugGuard(tcl, reclaim_delay_seconds=0.0,
                                    reclaim_scheduler=lambda _delay, callback: scheduled.append(callback),
                                    **kwargs)
        bridge, debug, _tcl = self.make_server_bridge(guard_factory=guard_factory)
        bridge.set_last_client_detached_handler(bridge.stop_if_generation)
        bridge.start_gateway(ProbeRef("STLINK123"))
        debug.emit("Info : accepting 'gdb' connection on tcp/3333")
        debug.emit("Info : dropped 'gdb' connection")
        bridge.stop()
        bridge.start_gateway(ProbeRef("STLINK123"))

        scheduled.pop(0)()

        self.assertEqual(debug.stops, 1)
        self.assertEqual(bridge.state.state, BridgeState.READY)

    def test_client_reclaims_only_its_forward_after_observed_two_to_zero(self) -> None:
        scheduled = []
        session = FakeRemoteSession()
        bridge = VsCodeDebugBridge(
            debug_service=FakeDebugService(), client_reclaim_delay_seconds=0.0,
            client_reclaim_scheduler=lambda _delay, callback: scheduled.append(callback),
        )
        bridge.set_last_client_detached_handler(bridge.stop_if_generation)
        first = self.gateway_snapshot(sequence=1, count=2, activity=1, ever=True)
        bridge.start_client(session, snapshot=first, profile_id="lab")

        bridge.observe_gateway_snapshot(self.gateway_snapshot(sequence=2, count=1, activity=2, ever=True))
        self.assertEqual(scheduled, [])
        bridge.observe_gateway_snapshot(self.gateway_snapshot(sequence=3, count=0, activity=3, ever=True))
        self.assertEqual(session.closed, [])
        scheduled.pop()()

        self.assertEqual(session.closed, ["vscode_gdb"])
        self.assertEqual(bridge.state.state, BridgeState.STOPPED)

    def test_newer_zero_activity_rebases_client_reclaim_candidate(self) -> None:
        scheduled = []
        session = FakeRemoteSession()
        bridge = VsCodeDebugBridge(
            debug_service=FakeDebugService(), client_reclaim_delay_seconds=0.0,
            client_reclaim_scheduler=lambda _delay, callback: scheduled.append(callback),
        )
        bridge.set_last_client_detached_handler(bridge.stop_if_generation)
        bridge.start_client(
            session,
            snapshot=self.gateway_snapshot(sequence=1, count=1, activity=1, ever=True),
            profile_id="lab",
        )

        bridge.observe_gateway_snapshot(self.gateway_snapshot(sequence=2, count=0, activity=2, ever=True))
        bridge.observe_gateway_snapshot(self.gateway_snapshot(sequence=3, count=0, activity=4, ever=True))
        self.assertEqual(len(scheduled), 2)

        scheduled.pop(0)()
        self.assertEqual(session.closed, [])
        scheduled.pop(0)()
        self.assertEqual(session.closed, ["vscode_gdb"])

    def test_client_reattach_and_old_gateway_zero_do_not_reclaim_forward(self) -> None:
        scheduled = []
        session = FakeRemoteSession()
        bridge = VsCodeDebugBridge(
            debug_service=FakeDebugService(), client_reclaim_delay_seconds=0.0,
            client_reclaim_scheduler=lambda _delay, callback: scheduled.append(callback),
        )
        bridge.set_last_client_detached_handler(bridge.stop_if_generation)
        bridge.start_client(session, snapshot=self.gateway_snapshot(sequence=1, count=1, activity=1, ever=True), profile_id="lab")
        bridge.observe_gateway_snapshot(self.gateway_snapshot(sequence=2, count=0, activity=2, ever=True))
        bridge.observe_gateway_snapshot(self.gateway_snapshot(sequence=3, count=1, activity=3, ever=True))
        scheduled.pop()()
        self.assertEqual(session.closed, [])

        self.assertFalse(bridge.observe_gateway_snapshot(
            self.gateway_snapshot(sequence=4, count=0, activity=4, ever=True, generation=2)
        ))
        self.assertEqual(session.closed, [])

    def test_client_missing_activity_capability_never_reclaims_forward(self) -> None:
        session = FakeRemoteSession()
        bridge = VsCodeDebugBridge(debug_service=FakeDebugService())
        bridge.start_client(session, snapshot=self.gateway_snapshot(sequence=1), profile_id="lab")
        self.assertFalse(bridge.observe_gateway_snapshot(self.gateway_snapshot(sequence=2)))
        self.assertEqual(session.closed, [])

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

    def test_launch_writer_updates_its_existing_managed_configuration(self) -> None:
        profile = VsCodeExternalProfile(
            name="B300 local",
            executable="${workspaceFolder}/build/application.elf",
            gdb_target="127.0.0.1:3333",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = profile.write_launch_json(root)
            self.assertTrue(output.is_file())
            profile.write_launch_json(root)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(result["configurations"]), 1)
            self.assertEqual(
                result["configurations"][0]["b300"]["id"],
                "b300.stm32f407.attach",
            )

    def test_launch_writer_preserves_jsonc_workspace_and_replaces_only_named_profile(self):
        original = '''\ufeff{
          // Team launch profiles
          "version": "0.2.0",
          "inputs": [{"id": "path", "default": "https://host/a/*b*/,]",}],
          "compounds": [{"name": "All", "configurations": ["Python", "B300 local"],}],
          "configurations": [
            {"name": "Python", "type": "debugpy", "args": ["a", "b",],},
            /* Managed attach */ {"name": "B300 local", "gdbTarget": "old",
              "b300": {"owner": "b300-stlink-tools", "id": "b300.stm32f407.attach"}},
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
            self.assertEqual(result["configurations"][1], {"name": "Python", "type": "debugpy", "args": ["a", "b"]})
            self.assertEqual(result["configurations"][2], {"name": "Other board", "type": "cortex-debug"})
            self.assertEqual(len(result["configurations"]), 3)
            self.assertEqual(result["configurations"][0]["gdbTarget"], "127.0.0.1:3333")
            self.assertEqual(result["configurations"][0]["request"], "attach")

    def test_launch_writer_does_not_claim_same_name_without_b300_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".vscode" / "launch.json"
            output.parent.mkdir()
            original = '{"configurations":[{"name":"B300 local","type":"custom"}]}'
            output.write_text(original, encoding="utf-8")
            profile = VsCodeExternalProfile("B300 local", "app.elf", "127.0.0.1:3333")
            with self.assertRaises(FileExistsError):
                profile.write_launch_json(root)
            self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_launch_writer_rejects_revision_conflict_without_changing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".vscode" / "launch.json"
            output.parent.mkdir()
            output.write_text('{"configurations":[]}', encoding="utf-8")
            profile = VsCodeExternalProfile("B300 local", "app.elf", "127.0.0.1:3333")
            revision = profile.launch_revision(root)
            changed = '{"configurations":[{"name":"user edit"}]}'
            output.write_text(changed, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                profile.write_launch_json(root, expected_revision=revision)
            self.assertEqual(output.read_text(encoding="utf-8"), changed)

    def test_profile_rejects_temporary_gdb_path(self):
        temporary_gdb = str(Path(tempfile.gettempdir()) / "pytest-42" / "arm-none-eabi-gdb")
        with self.assertRaisesRegex(ValueError, "temporary"):
            VsCodeExternalProfile(
                "B300 local", "app.elf", "127.0.0.1:3333", gdb_path=temporary_gdb
            ).configuration()

    def test_profile_rejects_active_b300_packaged_gdb_under_temporary_install(self):
        with tempfile.TemporaryDirectory() as directory:
            app_root = Path(directory) / "installed"
            executable = app_root / "b300-stlink-gui.exe"
            gdb_name = "arm-none-eabi-gdb.exe" if os.name == "nt" else "arm-none-eabi-gdb"
            gdb = app_root / "vendor" / "gdb" / "bin" / gdb_name
            gdb.parent.mkdir(parents=True)
            executable.write_bytes(b"gui")
            gdb.write_bytes(b"managed gdb")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "temporary"):
                    VsCodeExternalProfile(
                        "B300 local", "app.elf", "127.0.0.1:3333", gdb_path=str(gdb)
                    ).configuration()

    def test_launch_writer_migrates_exact_legacy_b300_profile_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".vscode" / "launch.json"
            output.parent.mkdir()
            output.write_text(json.dumps({"configurations": [{
                "name": "B300 STM32F407 · Remote via Gateway",
                "type": "cortex-debug",
                "request": "attach",
                "servertype": "external",
                "gdbTarget": "127.0.0.1:59235",
                "gdbPath": r"C:\\Users\\Admin\\AppData\\Local\\Temp\\old\\arm-none-eabi-gdb.exe",
            }]}), encoding="utf-8")

            VsCodeExternalProfile(
                "B300 STM32F407 · Remote via Gateway",
                "${workspaceFolder}/Objects/F407/Main_V2_F407.axf",
                "127.0.0.1:41234",
                gdb_path=r"C:\\Users\\Admin\\AppData\\Local\\B300-STLink\\vendor\\gdb\\bin\\arm-none-eabi-gdb.exe",
            ).write_launch_json(root)

            config = json.loads(output.read_text(encoding="utf-8"))["configurations"][0]
            self.assertEqual(config["gdbTarget"], "127.0.0.1:41234")
            self.assertIn("B300-STLink", config["gdbPath"])
            self.assertEqual(config["b300"]["owner"], "b300-stlink-tools")

    def test_launch_writer_removes_stale_legacy_b300_profiles_when_owned_profile_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".vscode" / "launch.json"
            output.parent.mkdir()
            legacy = {
                "name": "B300 STM32F407 · Remote via Gateway",
                "type": "cortex-debug", "request": "attach", "servertype": "external",
                "gdbTarget": "127.0.0.1:59235", "gdbPath": "expired-gdb",
            }
            owned = VsCodeExternalProfile(
                "B300 STM32F407 · Local ST-Link", "app.axf", "127.0.0.1:3333"
            ).configuration()
            output.write_text(json.dumps({
                "configurations": [legacy, {"name": "Python", "type": "debugpy"}, owned]
            }), encoding="utf-8")

            VsCodeExternalProfile(
                "B300 STM32F407 · Remote via Gateway", "app.axf", "127.0.0.1:41234"
            ).write_launch_json(root)

            configs = json.loads(output.read_text(encoding="utf-8"))["configurations"]
            b300 = [item for item in configs if item.get("b300", {}).get("owner") == "b300-stlink-tools"]
            self.assertEqual(len(b300), 1)
            self.assertEqual(b300[0]["gdbTarget"], "127.0.0.1:41234")
            self.assertEqual([item["name"] for item in configs if item.get("type") == "cortex-debug"],
                             ["B300 STM32F407 · Remote via Gateway"])
            self.assertIn({"name": "Python", "type": "debugpy"}, configs)

    def test_profile_rejects_temporary_gdb_from_untrusted_app_root_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            app_root = Path(directory) / "attacker-controlled"
            gdb_name = "arm-none-eabi-gdb.exe" if os.name == "nt" else "arm-none-eabi-gdb"
            gdb = app_root / "vendor" / "gdb" / "bin" / gdb_name
            gdb.parent.mkdir(parents=True)
            gdb.write_bytes(b"untrusted gdb")
            with patch.dict(os.environ, {"B300_APP_ROOT": str(app_root)}, clear=False):
                with self.assertRaisesRegex(ValueError, "temporary"):
                    VsCodeExternalProfile(
                        "B300 local", "app.elf", "127.0.0.1:3333", gdb_path=str(gdb)
                    ).configuration()

    def test_launch_writer_appends_managed_profile_to_existing_document(self):
        for original in ({"inputs": []}, {"configurations": [{"name": "Other"}]}):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / ".vscode" / "launch.json"
                output.parent.mkdir()
                output.write_text(json.dumps(original), encoding="utf-8")
                VsCodeExternalProfile("B300 local", "app.elf", "127.0.0.1:3333").write_launch_json(root, force=True)
                result = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(result["configurations"][1:], original.get("configurations", []))
                self.assertEqual(result["configurations"][0]["name"], "B300 local")
                if "inputs" in original:
                    self.assertEqual(result["inputs"], [])

    def test_launch_writer_places_managed_profile_first_for_new_workstations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".vscode" / "launch.json"
            output.parent.mkdir()
            output.write_text(json.dumps({"configurations": [{
                "name": "STM32F407 Main Debug via Raspberry Pi",
                "type": "cortex-debug",
                "request": "launch",
                "inputs": "raspberryPiHost",
            }]}), encoding="utf-8")

            VsCodeExternalProfile(
                "B300 STM32F407 · Remote via Gateway",
                "${workspaceFolder}/Objects/F407/Main_V2_F407.axf",
                "127.0.0.1:41234",
                gdb_path=r"C:\Toolchain\bin\arm-none-eabi-gdb.exe",
            ).write_launch_json(root)

            configs = json.loads(output.read_text(encoding="utf-8"))["configurations"]
            self.assertEqual(configs[0]["name"], "B300 STM32F407 · Remote via Gateway")
            self.assertEqual(configs[1]["name"], "STM32F407 Main Debug via Raspberry Pi")

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
