from __future__ import annotations

import json
import os
import shlex
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from b300_cli.parser import parse_args
from b300_core.remote_vscode import RemoteVsCodeProfile, _render_command, workspace_executable
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
        self.assertIn("PreferredAuthentications=password,keyboard-interactive", tunnel)
        self.assertIn("PasswordAuthentication=yes", tunnel)
        self.assertIn("ExitOnForwardFailure=yes", tunnel)
        self.assertIn("ConnectTimeout=8", tunnel)
        self.assertIn("ServerAliveInterval=30", tunnel)

    def test_vscode_tunnel_has_no_managed_identity_or_known_hosts_override(self) -> None:
        tunnel = self.make_profile().tunnel_argv()
        rendered = " ".join(tunnel)
        self.assertNotIn("-i", tunnel)
        self.assertNotIn("IdentityFile", rendered)
        self.assertNotIn("KnownHostsFile", rendered)
        self.assertNotIn("BatchMode=yes", rendered)

    def test_vscode_kit_writes_without_managed_ssh_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "remote-kit"
            self.make_profile().write_kit(destination)
            self.assertTrue(destination.exists())

    def test_vscode_tunnel_command_quotes_paths_for_posix_shell(self) -> None:
        profile = self.make_profile()
        self.assertEqual(shlex.split(profile.tunnel_command(system_name="linux")), list(profile.tunnel_argv()))

    def test_vscode_tunnel_command_is_interactive_on_windows(self) -> None:
        command = self.make_profile().tunnel_command(system_name="windows")
        self.assertIn("PasswordAuthentication=yes", command)
        self.assertNotIn("'-i'", command)
        self.assertNotIn(" -i ", command)
        self.assertNotIn("KnownHostsFile", command)

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

    def test_cli_vscode_kit_uses_password_interactive_ssh_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "kit"
            stream = StringIO()
            with redirect_stdout(stream):
                code = main([
                    "debug", "vscode", "--ssh-host", "192.168.1.50",
                    "--ssh-user", "automation", "--program-relative",
                    "Objects/F407/Main_V2_F407.axf", "--output-dir", str(output), "--json",
                ])
            self.assertEqual(code, 0)
            tunnel = (output / "b300-ssh-tunnel.txt").read_text(encoding="utf-8")
            self.assertIn("PasswordAuthentication=yes", tunnel)
            self.assertNotIn("'-i'", tunnel)
            self.assertNotIn(" -i ", tunnel)
            self.assertNotIn("KnownHostsFile", tunnel)

    def test_cli_vscode_writes_kit_without_managed_identity_or_trust(self) -> None:
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
            self.assertFalse({"password", "secret", "identity_file", "known_hosts_file"} & set(record))
            self.assertFalse({"password", "secret"} & set(record.get("profile", {})))
            self.assertNotIn("s3cr3t", " ".join(record["ssh_tunnel_command"]))
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
