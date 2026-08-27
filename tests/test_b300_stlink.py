from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"
VALID_HEX = ":020000040801F1\n:0100000000FF\n:00000001FF\n"
BOOTLOADER_HEX = ":020000040800F2\n:0100000000FF\n:00000001FF\n"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class B300StlinkTests(unittest.TestCase):
    def test_flash_erases_only_metadata_and_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "application.hex"
            image.write_text(VALID_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 0)
        command = next(json.loads(line)["command"] for line in output.getvalue().splitlines()
                       if json.loads(line)["event"] == "openocd")
        rendered = " ".join(command)
        self.assertIn("flash erase_sector 0 3 7", rendered)
        self.assertIn("program", rendered)
        self.assertIn("verify", rendered)
        self.assertIn("mww 0x40002860 0x53544C4B", rendered)
        self.assertNotIn("mass_erase", rendered)

    def test_flash_rejects_bootloader_hex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "bad.hex"
            image.write_text(BOOTLOADER_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 1)
        self.assertIn("protected range", output.getvalue())

    def test_debug_has_no_flash_write_command(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["debug", "--dry-run", "--json"])
        self.assertEqual(result, 0)
        rendered = output.getvalue().lower()
        self.assertNotIn("erase_sector", rendered)
        self.assertNotIn("program {", rendered)
        self.assertNotIn("mww ", rendered)

    def test_debug_binds_to_loopback_by_default(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["debug", "--dry-run", "--json"])
        self.assertEqual(result, 0)
        command = json.loads(output.getvalue())["command"]
        self.assertIn("bindto 127.0.0.1", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("tcl port disabled", command)

    def test_debug_can_explicitly_listen_for_remote_gdb(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "--bind-address", "0.0.0.0",
                "--gdb-port", "4333",
                "--probe-serial", "TEST-PROBE", "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        command = json.loads(output.getvalue())["command"]
        self.assertIn("bindto 0.0.0.0", command)
        self.assertIn("gdb port 4333", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("tcl port disabled", command)
        self.assertIn("adapter serial TEST-PROBE", command)

    def test_debug_allows_explicit_telnet_on_loopback(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "--telnet-port", "4444", "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        command = json.loads(output.getvalue())["command"]
        self.assertIn("telnet port 4444", command)

    def test_remote_debug_rejects_telnet_listener(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "--bind-address", "0.0.0.0", "--telnet-port", "4444",
                "--dry-run", "--json",
            ])
        self.assertEqual(result, 1)
        self.assertIn("Telnet", output.getvalue())

    def test_debug_rejects_command_in_bind_address(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool().main([
                "debug", "--bind-address", "127.0.0.1; flash erase_sector 0 0 7",
                "--dry-run",
            ])

    def test_debug_rejects_port_outside_tcp_range(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool().main(["debug", "--gdb-port", "70000", "--dry-run"])

    def test_debug_rejects_command_in_probe_serial(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool().main([
                "debug", "--probe-serial", "SAFE; flash erase_sector 0 0 7",
                "--dry-run",
            ])

    def test_flash_rejects_path_that_can_break_openocd_braces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "bad}name.hex"
            image.write_text(VALID_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 1)
        self.assertIn("unsafe character", output.getvalue())


if __name__ == "__main__":
    unittest.main()
