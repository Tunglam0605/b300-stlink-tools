import subprocess
import unittest
import json
from unittest import mock

from b300_core.gateway_setup import (
    DEBUG_PORTS, GatewayHostReport, build_gateway_prepare_plan,
    client_connection_text, prepare_gateway_host, _run_windows_elevated, _windows_prepare_script,
)


def report(*, platform="windows", installed=True, running=True, startup=True,
           firewall=True, listening=True, private=True, network_profile=True,
           network_profile_state=None, port=22, ready=None):
    if ready is None:
        ready = all((installed, running, startup, firewall, listening, private, network_profile))
    return GatewayHostReport(
        platform=platform, checks=(), ssh_installed=installed,
        ssh_service_running=running, ssh_startup_enabled=startup,
        ssh_firewall_ready=firewall, ssh_port_listening=listening,
        debug_ports_private=private, ready=ready,
        conclusion="READY" if ready else "BLOCKED", ssh_port=port,
        username="automation", hostname="b300-gateway",
        ipv4_addresses=("192.168.1.109",),
        ssh_network_profile_ready=network_profile,
        ssh_network_profile_state=network_profile_state or ("READY" if network_profile else "PUBLIC"),
    )


class GatewaySetupTests(unittest.TestCase):
    def test_ready_host_is_idempotent_and_requires_no_changes(self):
        plan = build_gateway_prepare_plan(report())
        self.assertFalse(plan.changes_required)
        self.assertFalse(plan.requires_elevation)
        self.assertEqual(plan.actions, ())

    def test_missing_windows_ssh_builds_minimal_prepare_actions(self):
        plan = build_gateway_prepare_plan(report(
            installed=False, running=False, startup=False, firewall=False, listening=False
        ))
        self.assertEqual(plan.actions, (
            "install_openssh_server", "enable_ssh_startup",
            "start_ssh_service", "allow_ssh_firewall",
        ))
        self.assertTrue(plan.requires_elevation)

    def test_windows_prepare_script_never_opens_debug_ports_or_edits_sshd_config(self):
        plan = build_gateway_prepare_plan(report(
            installed=False, running=False, startup=False, firewall=False, listening=False
        ))
        script = _windows_prepare_script(plan)
        self.assertIn("OpenSSH.Server", script)
        self.assertIn("LocalPort 22", script)
        self.assertNotIn("sshd_config", script)
        for port in DEBUG_PORTS:
            self.assertNotIn("LocalPort %d" % port, script)

    def test_windows_prepare_replaces_only_its_own_lan_scoped_ssh_rule(self):
        plan = build_gateway_prepare_plan(report(firewall=False, ready=False))
        script = _windows_prepare_script(plan)

        self.assertIn("Remove-NetFirewallRule -Name 'B300-OpenSSH-Server-In-TCP'", script)
        self.assertIn("-Profile Domain,Private", script)
        self.assertIn("-RemoteAddress LocalSubnet", script)
        self.assertIn("-LocalPort 22", script)

    def test_public_network_profile_is_a_separate_prepare_action(self):
        plan = build_gateway_prepare_plan(report(network_profile=False, firewall=False))

        self.assertEqual(plan.actions, (
            "set_active_network_private", "allow_ssh_firewall",
        ))

    def test_windows_prepare_changes_exactly_one_active_public_profile(self):
        plan = build_gateway_prepare_plan(report(network_profile=False, firewall=False))
        script = _windows_prepare_script(plan)

        self.assertIn("Get-NetConnectionProfile", script)
        self.assertIn("$active.Count -ne 1", script)
        self.assertIn("$active[0].NetworkCategory -ne 'Public'", script)
        self.assertIn("Set-NetConnectionProfile -InterfaceIndex $active[0].InterfaceIndex -NetworkCategory Private", script)

    def test_windows_public_profile_guard_runs_before_every_system_mutation(self):
        plan = build_gateway_prepare_plan(report(
            installed=False, running=False, startup=False, network_profile=False,
            firewall=False, listening=False,
        ))
        script = _windows_prepare_script(plan)

        guard = script.index("$active.Count -ne 1")
        for mutation in (
            "Add-WindowsCapability", "Set-Service", "Start-Service",
            "Set-NetConnectionProfile", "Remove-NetFirewallRule", "New-NetFirewallRule",
        ):
            self.assertLess(guard, script.index(mutation), mutation)

    def test_private_network_profile_is_never_changed_by_prepare(self):
        plan = build_gateway_prepare_plan(report(network_profile=True, firewall=False))
        script = _windows_prepare_script(plan)

        self.assertNotIn("Set-NetConnectionProfile", script)

    def test_ambiguous_network_profile_blocks_prepare_before_firewall_or_uac(self):
        blocked = report(
            network_profile=False, network_profile_state="AMBIGUOUS", firewall=False,
        )
        plan = build_gateway_prepare_plan(blocked)

        self.assertEqual(plan.actions, ("manual_fix_network_profile",))
        runner = mock.Mock(side_effect=AssertionError("must not request UAC"))
        result = prepare_gateway_host(runner=runner, inspector=mock.Mock(return_value=blocked))

        self.assertFalse(result.succeeded)
        self.assertFalse(result.changed)
        runner.assert_not_called()

    def test_windows_firewall_readiness_validates_lan_scope_not_only_rule_name(self):
        from b300_core.gateway_setup import _windows_ssh_firewall_ready

        def runner(argv, timeout):
            rule = {
                "Enabled": True, "Direction": "Inbound", "Action": "Allow",
                "Profile": 3, "Protocol": "TCP", "LocalPort": "22",
                "RemoteAddress": ["LocalSubnet"],
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(rule), "")

        self.assertTrue(_windows_ssh_firewall_ready(22, runner))

    def test_windows_firewall_readiness_rejects_public_or_broad_address_scope(self):
        from b300_core.gateway_setup import _windows_ssh_firewall_ready

        unsafe_rules = (
            {"Enabled": True, "Direction": "Inbound", "Action": "Allow", "Profile": 7,
             "Protocol": "TCP", "LocalPort": "22", "RemoteAddress": ["LocalSubnet"]},
            {"Enabled": True, "Direction": "Inbound", "Action": "Allow", "Profile": 3,
             "Protocol": "TCP", "LocalPort": "22", "RemoteAddress": ["LocalSubnet", "Any"]},
        )
        for rule in unsafe_rules:
            runner = lambda argv, timeout, value=rule: subprocess.CompletedProcess(
                argv, 0, json.dumps(value), ""
            )
            self.assertFalse(_windows_ssh_firewall_ready(22, runner))

    def test_windows_firewall_readiness_rejects_disabled_rule_without_boolean_string_cast(self):
        from b300_core.gateway_setup import _windows_ssh_firewall_ready

        disabled_rule = {
            "Enabled": False, "Direction": "Inbound", "Action": "Allow", "Profile": 3,
            "Protocol": "TCP", "LocalPort": "22", "RemoteAddress": ["LocalSubnet"],
        }
        seen = []

        def runner(argv, timeout):
            seen.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, json.dumps(disabled_rule), "")

        self.assertFalse(_windows_ssh_firewall_ready(22, runner))
        self.assertIn("[string]$rule.Enabled -eq 'True'", seen[0])
        self.assertNotIn("[bool]$rule.Enabled", seen[0])

    def test_linux_report_does_not_add_a_windows_network_profile_check(self):
        from b300_core.gateway_setup import _build_report

        result = _build_report("linux", True, True, True, True, True, True, 22)

        self.assertNotIn("network_profile", [check.name for check in result.checks])

    def test_windows_network_profile_inspection_classifies_public_profile(self):
        from b300_core.gateway_setup import _windows_network_profile_state

        scripts = []

        def runner(argv, timeout):
            scripts.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, "PUBLIC", "")

        self.assertEqual(_windows_network_profile_state(runner), "PUBLIC")
        self.assertIn("Get-NetConnectionProfile", scripts[0])
        self.assertIn("AMBIGUOUS", scripts[0])

    def test_windows_elevated_prepare_keeps_uac_but_hides_powershell_console(self):
        plan = build_gateway_prepare_plan(report(
            installed=False, running=False, startup=False, firewall=False, listening=False
        ))
        commands = []
        def runner(argv, timeout):
            commands.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch("b300_core.gateway_setup._powershell", return_value="powershell.exe"):
            _run_windows_elevated(plan, runner)
        rendered = " ".join(commands[0])
        self.assertIn("-Verb RunAs", rendered)
        self.assertIn("-WindowStyle Hidden", rendered)
        self.assertIn("'-NonInteractive'", rendered)

    def test_existing_install_only_repairs_service_and_firewall(self):
        plan = build_gateway_prepare_plan(report(
            installed=True, running=False, startup=False, firewall=False, listening=False
        ))
        self.assertNotIn("install_openssh_server", plan.actions)
        self.assertIn("start_ssh_service", plan.actions)
        self.assertIn("enable_ssh_startup", plan.actions)
        self.assertIn("allow_ssh_firewall", plan.actions)

    def test_debug_port_exposure_is_manual_safety_blocker(self):
        blocked = report(private=False, ready=False)
        plan = build_gateway_prepare_plan(blocked)
        self.assertIn("manual_fix_debug_exposure", plan.actions)
        inspector = mock.Mock(return_value=blocked)
        result = prepare_gateway_host(inspector=inspector)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.changed)
        inspector.assert_called_once()

    def test_custom_port_is_not_managed_when_not_ready(self):
        with self.assertRaisesRegex(ValueError, "never rewrites sshd_config"):
            build_gateway_prepare_plan(report(port=2222, listening=False, ready=False))

    def test_ready_custom_port_is_left_unchanged(self):
        plan = build_gateway_prepare_plan(report(port=2222, ready=True))
        self.assertEqual(plan.actions, ())

    def test_client_connection_text_uses_gateway_identity(self):
        text = client_connection_text(report())
        self.assertIn("192.168.1.109", text)
        self.assertIn("automation", text)
        self.assertIn("SSH port: 22", text)
        self.assertIn("3333/4444/6666 stay loopback-only", text)

    def test_client_connection_text_lists_all_adapter_candidates_without_guessing(self):
        multi = report()
        multi = type(multi)(
            multi.platform, multi.checks, multi.ssh_installed, multi.ssh_service_running,
            multi.ssh_startup_enabled, multi.ssh_firewall_ready, multi.ssh_port_listening,
            multi.debug_ports_private, multi.ready, multi.conclusion, multi.ssh_port,
            multi.username, multi.hostname, ("10.6.0.101", "192.168.1.95"),
        )
        text = client_connection_text(multi)
        self.assertIn("10.6.0.101", text)
        self.assertIn("192.168.1.95", text)
        self.assertIn("choose the address reachable from the Client", text)
        self.assertNotIn("GUI Client: host=10.6.0.101", text)

    def test_prepare_ready_host_does_not_execute_privileged_command(self):
        ready = report()
        inspector = mock.Mock(return_value=ready)
        runner = mock.Mock(side_effect=AssertionError("runner must not execute"))
        result = prepare_gateway_host(runner=runner, inspector=inspector)
        self.assertTrue(result.succeeded)
        self.assertFalse(result.changed)
        self.assertEqual(inspector.call_count, 2)


if __name__ == "__main__":
    unittest.main()
