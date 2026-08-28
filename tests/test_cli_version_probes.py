from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from b300_core.models import ProbeInfo


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(argv, probes=()):
    module = tool()
    output = io.StringIO()
    with mock.patch.object(module, "list_probes", create=True, return_value=probes), \
            redirect_stdout(output):
        code = module.main(argv)
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    return code, records


class CliVersionAndProbesTests(unittest.TestCase):
    def test_version_json_is_one_stable_object(self) -> None:
        code, records = run_cli(["--version", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["schema_version"], 1)
        self.assertEqual(records[0]["version"], "0.5.3")
        self.assertEqual(records[0]["openocd_version"], "0.12.0-7")

    def test_version_text_reports_cli_core_openocd_and_platform(self) -> None:
        module = tool()
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("CLI/Core: 0.5.3", output.getvalue())
        self.assertIn("OpenOCD: 0.12.0-7", output.getvalue())
        self.assertIn("Platform:", output.getvalue())

    def test_probes_zero_is_nonzero_with_reason_code(self) -> None:
        code, records = run_cli(["probes", "--json"], probes=())
        self.assertNotEqual(code, 0)
        self.assertEqual(records[-1]["reason_code"], "NO_PROBE")

    def test_probes_list_global_json_reports_serialless_probe_fields(self) -> None:
        probe = ProbeInfo(None, "Clone", "test", "usb:1")
        code, records = run_cli(["--json", "probes", "list"], probes=(probe,))
        self.assertEqual(code, 0)
        self.assertEqual(len(records), 1)
        listed = records[0]["probes"][0]
        self.assertEqual(listed["index"], 1)
        self.assertEqual(listed["name"], "Clone")
        self.assertIsNone(listed["serial"])
        self.assertFalse(listed["serial_available"])
        self.assertEqual(listed["usb_identity"], "usb:1")
        self.assertEqual(listed["source"], "test")
        self.assertEqual(listed["status"], "available")

    def test_probes_lists_multiple_physical_probes_without_selecting_one(self) -> None:
        probes = (
            ProbeInfo("FIRST", "ST-Link A", "test", "usb:1"),
            ProbeInfo("SECOND", "ST-Link B", "test", "usb:2"),
        )
        code, records = run_cli(["probes", "--json"], probes=probes)
        self.assertEqual(code, 0)
        self.assertEqual([item["serial"] for item in records[0]["probes"]], [
            "FIRST", "SECOND",
        ])

    def test_probes_text_includes_stable_operator_fields(self) -> None:
        module = tool()
        output = io.StringIO()
        probe = ProbeInfo("SERIAL", "ST-Link", "test", "usb:1", "available")
        with mock.patch.object(module, "list_probes", create=True, return_value=(probe,)), \
                redirect_stdout(output):
            code = module.main(["probes"])
        self.assertEqual(code, 0)
        self.assertIn("index=1", output.getvalue())
        self.assertIn("type/name=ST-Link", output.getvalue())
        self.assertIn("serial=SERIAL", output.getvalue())
        self.assertIn("serial_available=True", output.getvalue())
        self.assertIn("usb_identity=usb:1", output.getvalue())
        self.assertIn("source=test", output.getvalue())
        self.assertIn("status=available", output.getvalue())


if __name__ == "__main__":
    unittest.main()
