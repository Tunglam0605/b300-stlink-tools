from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink_live_tests", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_axf(directory: str) -> Path:
    path = Path(directory) / "firmware.axf"
    path.write_bytes(b"fake-symbols")
    return path


class CliLiveMonitorTests(unittest.TestCase):
    def test_local_dry_run_is_tcl_only_and_contains_no_intrusive_commands(self):
        module = tool()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("b300_core.live_service.resolve_openocd", return_value="openocd"), \
                redirect_stdout(output):
            result = module.main([
                "debug", "live", "--symbols", str(fake_axf(directory)),
                "--live-interval", "0.1", "--live-samples", "5",
                "--live-watch", "xTickCount:u32", "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue().strip())
        self.assertTrue(record["zero_halt"])
        self.assertFalse(record["gdb_connected"])
        rendered = " ".join(record["command"]).lower()
        self.assertIn("gdb port disabled", rendered)
        self.assertIn("telnet port disabled", rendered)
        self.assertIn("tcl port 6666", rendered)
        for token in (" halt", "resume", "reset", "flash erase", "program {", "mww "):
            self.assertNotIn(token, rendered)

    def test_client_live_dry_run_forwards_only_tcl(self):
        module = tool()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(module, "find_available_loopback_port", return_value=16666), \
                redirect_stdout(output):
            result = module.main([
                "debug", "client", "--ssh-host", "gateway.local",
                "--ssh-user", "automation", "--client-action", "live",
                "--symbols", str(fake_axf(directory)), "--live-interval", "0.1",
                "--live-samples", "5", "--live-watch", "xTickCount:u32",
                "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue().strip())
        self.assertTrue(record["zero_halt"])
        self.assertFalse(record["gdb_connected"])
        self.assertIsNone(record["gdb_endpoint"])
        self.assertEqual(record["gateway_ports"], {"tcl": 6666})
        command = record["ssh_command"]
        rendered = " ".join(command)
        self.assertEqual(command.count("-L"), 1)
        self.assertIn("127.0.0.1:16666:127.0.0.1:6666", rendered)
        self.assertNotIn(":3333", rendered)

    def test_invalid_live_interval_fails_before_probe_discovery(self):
        module = tool()
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch("b300_core.live_service.resolve_openocd", return_value="openocd"), \
                mock.patch.object(module, "list_probes", side_effect=AssertionError("must not touch hardware")) as discovery, \
                redirect_stdout(output):
            result = module.main([
                "debug", "live", "--symbols", str(fake_axf(directory)),
                "--live-interval", "0.05", "--live-samples", "5", "--json",
            ])
        self.assertEqual(result, 1)
        discovery.assert_not_called()
        self.assertIn("0.1..60.0", output.getvalue())

    def test_csv_output_reserves_coherence_and_raw_columns_per_watch(self):
        module = tool()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trace.csv"
            handle, writer = module._open_live_output(output, ("xTickCount:u32", "speed:f64"), False)
            try:
                self.assertIsNotNone(writer)
                self.assertIn("xTickCount__coherent", writer.fieldnames)
                self.assertIn("xTickCount__raw_hex", writer.fieldnames)
                self.assertIn("speed__coherent", writer.fieldnames)
                self.assertIn("speed__raw_hex", writer.fieldnames)
            finally:
                handle.close()

    def test_live_output_does_not_overwrite_without_force(self):
        module = tool()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trace.jsonl"
            output.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "--force"):
                module._open_live_output(output, (), False)
            handle, writer = module._open_live_output(output, (), True)
            self.assertIsNone(writer)
            handle.close()


if __name__ == "__main__":
    unittest.main()
