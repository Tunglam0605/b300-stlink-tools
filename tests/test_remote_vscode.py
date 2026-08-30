from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from b300_cli.parser import parse_args
from b300_core.remote_vscode import RemoteVsCodeProfile, workspace_executable
from b300_stlink import main


class RemoteVsCodeTests(unittest.TestCase):
    def make_profile(self) -> RemoteVsCodeProfile:
        return RemoteVsCodeProfile(
            ssh_host="gateway.example",
            ssh_user="automation",
            executable=workspace_executable("Objects/F407/Main_V2_F407.axf"),
            probe_serial="STLINK123",
        )

    def test_workspace_executable_rejects_escape_and_non_symbol_file(self) -> None:
        for value in ("../secret.axf", "/tmp/app.axf", "app.hex"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                workspace_executable(value)
        self.assertEqual(
            workspace_executable(r"Objects\F407\Main_V2_F407.axf"),
            "${workspaceFolder}/Objects/F407/Main_V2_F407.axf",
        )

    def test_gateway_and_ssh_tunnel_keep_openocd_private(self) -> None:
        profile = self.make_profile()
        gateway = profile.gateway_argv()
        tunnel = profile.tunnel_argv()
        self.assertIn("127.0.0.1", gateway)
        self.assertIn("--tcl-port", gateway)
        self.assertIn("6666", gateway)
        self.assertIn("--probe-serial", gateway)
        self.assertIn("127.0.0.1:3333:127.0.0.1:3333", tunnel)
        self.assertNotIn("6666", tunnel)
        self.assertIn("BatchMode=yes", tunnel)
        self.assertIn("StrictHostKeyChecking=yes", tunnel)
        self.assertIn("ExitOnForwardFailure=yes", tunnel)
        self.assertIn("ConnectTimeout=8", tunnel)
        self.assertIn("ServerAliveInterval=30", tunnel)

    def test_launch_json_is_external_attach_and_hardware_only(self) -> None:
        config = self.make_profile().cortex_debug_configuration()
        self.assertEqual(config["type"], "cortex-debug")
        self.assertEqual(config["request"], "attach")
        self.assertEqual(config["servertype"], "external")
        self.assertEqual(config["gdbTarget"], "127.0.0.1:3333")
        self.assertEqual(config["gdbPath"], "arm-none-eabi-gdb")
        self.assertEqual(config["device"], "STM32F407ZE")
        self.assertEqual(config["rtos"], "FreeRTOS")
        self.assertTrue(config["hardwareBreakpoints"]["require"])
        self.assertEqual(config["hardwareBreakpoints"]["limit"], 6)
        self.assertTrue(config["hardwareWatchpoints"]["require"])
        self.assertEqual(config["hardwareWatchpoints"]["limit"], 4)
        self.assertNotIn("load", json.dumps(config).lower())

    def test_parser_accepts_remote_vscode_options(self) -> None:
        args = parse_args([
            "debug", "vscode",
            "--ssh-host", "192.168.1.50",
            "--ssh-user", "automation",
            "--program-relative", "Objects/F407/Main_V2_F407.axf",
            "--output-dir", "remote-kit",
        ])
        self.assertEqual(args.debug_mode, "vscode")
        self.assertEqual(args.ssh_host, "192.168.1.50")
        self.assertEqual(args.ssh_user, "automation")
        self.assertEqual(args.local_gdb_port, 3333)
        self.assertEqual(args.gdb_port, 3333)
        self.assertIsNone(args.vscode_gdb_path)

    def test_cli_vscode_auto_resolves_client_gdb_when_not_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "kit"
            stream = StringIO()
            with patch("b300_stlink.resolve_gdb", return_value=r"C:\Toolchain\bin\arm-none-eabi-gdb.exe"):
                with redirect_stdout(stream):
                    code = main([
                        "debug", "vscode",
                        "--ssh-host", "192.168.1.50",
                        "--ssh-user", "automation",
                        "--program-relative", "Objects/F407/Main_V2_F407.axf",
                        "--output-dir", str(output),
                        "--json",
                    ])
            self.assertEqual(code, 0)
            launch = json.loads((output / ".vscode" / "launch.json").read_text(encoding="utf-8"))
            self.assertEqual(
                launch["configurations"][0]["gdbPath"],
                r"C:\Toolchain\bin\arm-none-eabi-gdb.exe",
            )

    def test_cli_vscode_falls_back_to_portable_gdb_name_when_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "kit"
            stream = StringIO()
            with patch("b300_stlink.resolve_gdb", side_effect=FileNotFoundError("missing")):
                with redirect_stdout(stream):
                    code = main([
                        "debug", "vscode",
                        "--ssh-host", "192.168.1.50",
                        "--ssh-user", "automation",
                        "--program-relative", "Objects/F407/Main_V2_F407.axf",
                        "--output-dir", str(output),
                        "--json",
                    ])
            self.assertEqual(code, 0)
            launch = json.loads((output / ".vscode" / "launch.json").read_text(encoding="utf-8"))
            self.assertEqual(launch["configurations"][0]["gdbPath"], "arm-none-eabi-gdb")

    def test_cli_generates_portable_kit_without_hardware_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "kit"
            stream = StringIO()
            with redirect_stdout(stream):
                code = main([
                    "debug", "vscode",
                    "--ssh-host", "192.168.1.50",
                    "--ssh-user", "automation",
                    "--program-relative", "Objects/F407/Main_V2_F407.axf",
                    "--output-dir", str(output),
                    "--json",
                ])
            self.assertEqual(code, 0)
            record = json.loads(stream.getvalue().strip())
            self.assertEqual(record["command"], "debug vscode")
            self.assertFalse(record["security"]["gdb_exposed_publicly"])
            self.assertFalse(record["security"]["tcl_forwarded"])
            self.assertTrue((output / ".vscode" / "launch.json").is_file())
            self.assertTrue((output / ".vscode" / "extensions.json").is_file())
            self.assertTrue((output / "b300-ssh-tunnel.txt").is_file())
            self.assertTrue((output / "b300-gateway-command.txt").is_file())
            self.assertTrue((output / "B300-REMOTE-DEBUG.md").is_file())
            launch = json.loads((output / ".vscode" / "launch.json").read_text(encoding="utf-8"))
            self.assertEqual(launch["configurations"][0]["gdbTarget"], "127.0.0.1:3333")


if __name__ == "__main__":
    unittest.main()
