from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from b300_core.gateway_agent_setup import (
    GatewayAgentSetupReport,
    build_gateway_agent_setup_plan,
    inspect_gateway_agent_setup,
)


CLI = Path("C:/Tools/b300-stlink.exe")


class GatewayAgentSetupTests(unittest.TestCase):
    def test_isolated_linux_setup_never_enables_user_service(self):
        from b300_core import gateway_agent_setup
        from b300_core.gateway_system_mode import IsolatedGatewayConfig
        config = IsolatedGatewayConfig(Path("/run/b300-stlink/agent.sock"),
                                       Path("/var/lib/b300-stlink/gateway"),
                                       Path("/var/spool/b300-stlink/ingress"), 1000)
        with mock.patch.object(gateway_agent_setup, "load_isolated_gateway_config",
                               return_value=config, create=True):
            report = inspect_gateway_agent_setup(
                cli_path=CLI, system_name="Linux",
                runner=lambda command: self.fail("per-user systemctl must not run"))
            plan = build_gateway_agent_setup_plan(report, cli_path=CLI,
                                                  system_name="Linux")
        self.assertEqual(report.reason_code, "ISOLATED_SYSTEM_AGENT")
        self.assertEqual(plan.commands, ())

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

    def test_plan_preserves_cli_path_spelling_without_resolving_it(self):
        report = GatewayAgentSetupReport(
            platform="windows", supported=True, installed=False, running=False,
            version=None, autostart_enabled=False, reason_code="AGENT_MISSING",
        )
        cli = Path("C:/Tools/B300-STLink.exe")
        with mock.patch.object(Path, "resolve", side_effect=AssertionError("must preserve path")):
            plan = build_gateway_agent_setup_plan(report, cli_path=cli, system_name="Windows")

        self.assertIn(str(cli), plan.commands[0][-2])

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
