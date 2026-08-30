from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from b300_cli.parser import parse_args
from b300_core.gateway_setup import GatewayHostReport, GatewayPrepareResult, build_gateway_prepare_plan

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"

def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink_gateway_setup", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def host_report(ready=False):
    return GatewayHostReport(
        platform="windows", checks=(), ssh_installed=ready,
        ssh_service_running=ready, ssh_startup_enabled=ready,
        ssh_firewall_ready=ready, ssh_port_listening=ready,
        debug_ports_private=True, ready=ready,
        conclusion="READY" if ready else "BLOCKED", ssh_port=22,
        username="automation", hostname="gateway",
        ipv4_addresses=("192.168.1.109",),
    )

class CliGatewaySetupTests(unittest.TestCase):
    def test_parser_exposes_gateway_plan_and_prepare_confirmation(self):
        plan = parse_args(["gateway", "plan", "--json"])
        prepare = parse_args(["gateway", "prepare", "--confirm-system-change", "--json"])
        self.assertEqual(plan.gateway_action, "plan")
        self.assertEqual(prepare.gateway_action, "prepare")
        self.assertTrue(prepare.confirm_system_change)

    def test_gateway_plan_is_read_only_and_reports_actions(self):
        module = tool()
        before = host_report(False)
        output = io.StringIO()
        with mock.patch.object(module, "inspect_gateway_host", return_value=before), \
             redirect_stdout(output):
            result = module.main(["gateway", "plan", "--json"])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(record["command"], "gateway plan")
        self.assertTrue(record["plan"]["changes_required"])
        self.assertIn("install_openssh_server", record["plan"]["actions"])
        self.assertEqual(record["security"]["openocd_public_ports"], [])

    def test_gateway_prepare_requires_explicit_confirmation_before_changes(self):
        module = tool()
        before = host_report(False)
        output = io.StringIO()
        with mock.patch.object(module, "inspect_gateway_host", return_value=before), \
             mock.patch.object(module, "prepare_gateway_host") as prepare, \
             redirect_stdout(output):
            result = module.main(["gateway", "prepare", "--json"])
        self.assertEqual(result, 1)
        prepare.assert_not_called()
        record = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(record["status"], "confirmation_required")
        self.assertEqual(record["reason_code"], "SYSTEM_CHANGE_CONFIRMATION_REQUIRED")

    def test_gateway_prepare_confirmed_returns_verified_after_state(self):
        module = tool()
        before = host_report(False)
        after = host_report(True)
        plan = build_gateway_prepare_plan(before)
        prepared = GatewayPrepareResult(plan, before, after, True, True)
        output = io.StringIO()
        with mock.patch.object(module, "inspect_gateway_host", return_value=before), \
             mock.patch.object(module, "prepare_gateway_host", return_value=prepared) as prepare, \
             redirect_stdout(output):
            result = module.main(["gateway", "prepare", "--confirm-system-change", "--json"])
        self.assertEqual(result, 0)
        prepare.assert_called_once_with(ssh_port=22)
        record = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertTrue(record["succeeded"])
        self.assertTrue(record["changed"])
        self.assertEqual(record["security"]["lan_ingress"], [22])

if __name__ == "__main__":
    unittest.main()
