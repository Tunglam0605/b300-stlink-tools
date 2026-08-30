from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from b300_cli.parser import parse_args
from b300_core.gateway_setup import GatewayHostReport, GatewayPrepareResult, build_gateway_prepare_plan
from b300_core.ssh_identity import (
    AuthorizedKeyResult, SshClientPrepareResult, SshClientPrerequisiteReport, SshIdentityReport,
)
from tests.test_ssh_identity import key_line

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

    def test_client_key_reports_public_material_but_never_private_contents(self):
        module = tool()
        identity = SshIdentityReport(
            private_key=Path("C:/Users/test/.ssh/b300_gateway_ed25519"),
            public_key=Path("C:/Users/test/.ssh/b300_gateway_ed25519.pub"),
            keygen_available=True, pair_exists=True, public_key_text=key_line(21),
            fingerprint="SHA256:PUBLICONLY", ready=True,
        )
        output = io.StringIO()
        prereq = SshClientPrerequisiteReport(
            "windows", Path("ssh.exe"), Path("ssh-keygen.exe"), True, True, (), False
        )
        with mock.patch.object(module, "inspect_ssh_client_prerequisites", return_value=prereq), \
             mock.patch.object(module, "ensure_ssh_identity", return_value=identity), redirect_stdout(output):
            result = module.main(["gateway", "client-key", "--json"])
        self.assertEqual(result, 0)
        raw = output.getvalue()
        self.assertNotIn("PRIVATE-CONTENT", raw)
        record = json.loads(raw.strip().splitlines()[-1])
        self.assertEqual(record["public_key"], identity.public_key_text)
        self.assertFalse(record["private_key_exported"])
        self.assertEqual(record["private_key_path"], str(identity.private_key))

    def test_client_key_requires_confirmation_when_openssh_client_is_missing(self):
        module = tool()
        missing = SshClientPrerequisiteReport(
            "windows", None, None, False, False, ("install_openssh_client",), True
        )
        output = io.StringIO()
        with mock.patch.object(module, "inspect_ssh_client_prerequisites", return_value=missing), \
             mock.patch.object(module, "prepare_ssh_client_prerequisites") as prepare, \
             mock.patch.object(module, "ensure_ssh_identity") as ensure, redirect_stdout(output):
            result = module.main(["gateway", "client-key", "--json"])
        self.assertEqual(result, 1)
        prepare.assert_not_called()
        ensure.assert_not_called()
        record = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(record["reason_code"], "SYSTEM_CHANGE_CONFIRMATION_REQUIRED")
        self.assertFalse(record["private_key_exported"])

    def test_client_key_confirmed_prepares_openssh_client_then_generates_key(self):
        module = tool()
        missing = SshClientPrerequisiteReport(
            "windows", None, None, False, False, ("install_openssh_client",), True
        )
        ready = SshClientPrerequisiteReport(
            "windows", Path("ssh.exe"), Path("ssh-keygen.exe"), True, True, (), False
        )
        prepared = SshClientPrepareResult(missing, ready, True, True)
        identity = SshIdentityReport(
            Path("C:/id"), Path("C:/id.pub"), True, True, key_line(71), "SHA256:CLIENT", True
        )
        output = io.StringIO()
        with mock.patch.object(module, "inspect_ssh_client_prerequisites", return_value=missing), \
             mock.patch.object(module, "prepare_ssh_client_prerequisites", return_value=prepared) as prepare, \
             mock.patch.object(module, "ensure_ssh_identity", return_value=identity) as ensure, redirect_stdout(output):
            result = module.main(["gateway", "client-key", "--confirm-system-change", "--json"])
        self.assertEqual(result, 0)
        prepare.assert_called_once_with()
        ensure.assert_called_once_with(None)

    def test_authorize_key_requires_confirmation_after_validation(self):
        module = tool()
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory) / "client.pub"
            public.write_text(key_line(31), encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(module, "install_gateway_public_key") as install, redirect_stdout(output):
                result = module.main(["gateway", "authorize-key", "--public-key-file", str(public), "--json"])
        self.assertEqual(result, 1)
        install.assert_not_called()
        record = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(record["reason_code"], "SYSTEM_CHANGE_CONFIRMATION_REQUIRED")
        self.assertFalse(record["private_key_received"])

    def test_authorize_key_confirmed_installs_only_public_key(self):
        module = tool()
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory) / "client.pub"
            public_text = key_line(41)
            public.write_text(public_text, encoding="utf-8")
            target = Path(directory) / "authorized_keys"
            installed = AuthorizedKeyResult(target, "SHA256:KEY", True, False)
            output = io.StringIO()
            with mock.patch.object(module, "install_gateway_public_key", return_value=installed) as install, redirect_stdout(output):
                result = module.main([
                    "gateway", "authorize-key", "--public-key-file", str(public),
                    "--confirm-system-change", "--json",
                ])
        self.assertEqual(result, 0)
        install.assert_called_once_with(public_text)
        record = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertTrue(record["changed"])
        self.assertFalse(record["private_key_received"])

if __name__ == "__main__":
    unittest.main()
