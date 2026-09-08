from __future__ import annotations

import unittest
from pathlib import Path

from b300_core.gateway_agent_setup import (
    GatewayAgentSetupReport,
    build_gateway_agent_setup_plan,
    inspect_gateway_agent_setup,
)


CLI = Path("C:/Tools/b300-stlink.exe")


class GatewayAgentSetupTests(unittest.TestCase):
    def test_windows_plan_uses_hidden_logon_task_and_exact_cli_path(self):
        report = GatewayAgentSetupReport(
            platform="windows", supported=True, installed=False, running=False,
            version=None, autostart_enabled=False, reason_code="AGENT_MISSING",
        )
        plan = build_gateway_agent_setup_plan(report, cli_path=CLI, system_name="Windows")
        rendered = " ".join(" ".join(command) for command in plan.commands)
        self.assertIn("B300-STLink-GatewayAgent", rendered)
        self.assertIn(str(CLI), rendered)
        self.assertIn("ONLOGON", rendered)
        self.assertNotIn("/RP", rendered.upper())
        self.assertNotIn("password", rendered.lower())

    def test_linux_plan_is_user_scoped_and_never_uses_sudo_or_linger(self):
        report = GatewayAgentSetupReport(
            platform="linux", supported=True, installed=False, running=False,
            version=None, autostart_enabled=False, reason_code="AGENT_MISSING",
        )
        plan = build_gateway_agent_setup_plan(report, cli_path=CLI, system_name="Linux")
        rendered = " ".join(" ".join(command) for command in plan.commands)
        self.assertIn("systemctl --user", rendered)
        self.assertIn("b300-stlink-gateway-agent.service", rendered)
        self.assertNotIn("sudo", rendered.lower())
        self.assertNotIn("enable-linger", rendered)

    def test_inspection_is_read_only_and_reports_running_version(self):
        calls = []

        def runner(command):
            calls.append(tuple(command))
            if command[:2] == ("schtasks", "/Query"):
                return {"returncode": 0, "stdout": "B300-STLink-GatewayAgent\n", "version": "0.23.0"}
            return {"returncode": 0, "stdout": "", "version": None}

        report = inspect_gateway_agent_setup(cli_path=CLI, system_name="Windows", runner=runner)
        self.assertTrue(report.installed)
        self.assertTrue(report.autostart_enabled)
        self.assertEqual(report.version, "0.23.0")
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
