from __future__ import annotations

import unittest

from b300_cli.parser import parse_args
from b300_core.gateway_readiness import inspect_gateway_readiness
from b300_core.models import ProbeInfo


PROBE = ProbeInfo("SAFE123", "ST-Link", "test", "USB\\VID_0483&PID_3748\\SAFE123")


class GatewayReadinessTests(unittest.TestCase):
    def test_ready_gateway_requires_only_gateway_dependencies(self) -> None:
        report = inspect_gateway_readiness(
            openocd_resolver=lambda _value: "/opt/b300/openocd",
            probe_discovery=lambda: (PROBE,),
            ssh_probe=lambda host, port: (host, port) == ("127.0.0.1", 22),
            port_probe=lambda host, port: host == "127.0.0.1" and port in (3333, 6666),
            ipv4_discovery=lambda: ("192.168.1.205",),
        )
        self.assertTrue(report.ready)
        self.assertEqual(report.conclusion, "READY")
        self.assertEqual(report.probe.serial, "SAFE123")
        self.assertEqual(report.ipv4_addresses, ("192.168.1.205",))
        self.assertNotIn("gdb", [check.name for check in report.checks])
        self.assertTrue(all(check.status == "PASS" for check in report.checks))

    def test_missing_ssh_server_blocks_gateway(self) -> None:
        report = inspect_gateway_readiness(
            openocd_resolver=lambda _value: "openocd",
            probe_discovery=lambda: (PROBE,),
            ssh_probe=lambda _host, _port: False,
            port_probe=lambda _host, _port: True,
            ipv4_discovery=lambda: ("192.168.1.205",),
        )
        self.assertFalse(report.ready)
        self.assertEqual(report.conclusion, "BLOCKED")
        ssh = next(check for check in report.checks if check.name == "ssh")
        self.assertEqual(ssh.code, "SSH_SERVER_UNAVAILABLE")

    def test_multiple_probes_fail_closed_without_serial(self) -> None:
        second = ProbeInfo("SECOND", "ST-Link", "test", "usb:2")
        report = inspect_gateway_readiness(
            openocd_resolver=lambda _value: "openocd",
            probe_discovery=lambda: (PROBE, second),
            ssh_probe=lambda _host, _port: True,
            port_probe=lambda _host, _port: True,
            ipv4_discovery=lambda: ("10.0.0.2",),
        )
        self.assertFalse(report.ready)
        probe = next(check for check in report.checks if check.name == "probe")
        self.assertEqual(probe.code, "MULTIPLE_PROBES")

    def test_no_ipv4_is_warning_not_false_hardware_failure(self) -> None:
        report = inspect_gateway_readiness(
            openocd_resolver=lambda _value: "openocd",
            probe_discovery=lambda: (PROBE,),
            ssh_probe=lambda _host, _port: True,
            port_probe=lambda _host, _port: True,
            ipv4_discovery=lambda: (),
        )
        self.assertTrue(report.ready)
        self.assertEqual(report.conclusion, "READY_WITH_WARNINGS")

    def test_duplicate_gateway_ports_are_rejected_before_any_probe(self) -> None:
        calls = []
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            inspect_gateway_readiness(
                ssh_port=22, gdb_port=3333, tcl_port=3333,
                openocd_resolver=lambda value: calls.append(value) or "openocd",
            )
        self.assertEqual(calls, [])

    def test_parser_exposes_one_command_gateway_doctor(self) -> None:
        args = parse_args(["gateway", "doctor", "--ssh-port", "2222", "--json"])
        self.assertEqual(args.command, "gateway")
        self.assertEqual(args.gateway_action, "doctor")
        self.assertEqual(args.ssh_port, 2222)
        self.assertEqual(args.gdb_port, 3333)
        self.assertEqual(args.tcl_port, 6666)


if __name__ == "__main__":
    unittest.main()
