from __future__ import annotations

import importlib.util
import io
import json
import signal
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.models import BootVerification, FlashPhaseEvent, ProbeInfo, TargetInfo


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"
VALID_HEX = ":020000040801F1\n:080000000000022001010108CB\n:00000001FF\n"
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
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        transactions = [record for record in records if record["event"] == "openocd"]
        self.assertEqual([item["phase"] for item in transactions], [
            "program_verify", "metadata_write_verify", "reset",
        ])
        program = " ".join(transactions[0]["command"])
        metadata = " ".join(transactions[1]["command"])
        reset = " ".join(transactions[2]["command"])
        self.assertIn("flash erase_sector 0 3 7", program)
        self.assertIn("flash write_image", program)
        self.assertIn("verify_image", program)
        self.assertNotIn("flash write_image erase", program)
        self.assertNotIn("mww 0x40002860", program)
        self.assertIn("flash write_image", metadata)
        self.assertIn("verify_image", metadata)
        self.assertIn("dump_image", metadata)
        self.assertIn("0x0800C000", metadata)
        self.assertNotIn("erase_sector", metadata)
        self.assertNotIn("mww", metadata)
        self.assertIn("reset run", reset)
        self.assertEqual(transactions[0]["condition"], "always")
        self.assertEqual(transactions[1]["condition"], "after_application_verified")
        self.assertEqual(transactions[2]["condition"], "after_exact_stlm_verified_readback")
        metadata_plan = next(record for record in records if record["event"] == "metadata_plan")
        self.assertEqual(metadata_plan["address"], "0x0800C000")
        self.assertEqual(metadata_plan["size"], 44)
        self.assertEqual(metadata_plan["magic"], "STLM")
        self.assertEqual(metadata_plan["state"], "VERIFIED")
        self.assertEqual(metadata_plan["condition"], "after_application_verified")
        self.assertIn("gdb port disabled", program)
        self.assertIn("telnet port disabled", program)
        self.assertIn("tcl port disabled", program)
        confirmation = next(record for record in records if record["event"] == "confirmation_plan")
        self.assertEqual(confirmation["required_magic"], "STLM")
        self.assertEqual(confirmation["required_state"], "CONFIRMED")
        self.assertEqual(confirmation["sequence_policy"], "written_sequence_plus_1_mod_2^32")
        self.assertEqual(confirmation["final_gate"], "application_pc_and_bkp1r_zero")
        self.assertNotIn("mass_erase", program + metadata + reset)
        self.assertNotIn("flash protect", program + metadata + reset)
        self.assertNotIn("53544C4B", program + metadata + reset)

    def test_flash_rejects_bootloader_hex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "bad.hex"
            image.write_text(BOOTLOADER_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 1)
        error = json.loads(output.getvalue())
        self.assertIn("protected range", error["message"])
        self.assertEqual(error["phase"], "validating")
        self.assertIn("Application HEX", error["next_action"])

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
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        role = next(record for record in records if record["event"] == "debug_role")
        command = next(record["command"] for record in records if record["event"] == "openocd")
        self.assertEqual(role["role"], "gateway")
        self.assertIn("bindto 127.0.0.1", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("tcl port 6666", command)

    def test_real_debug_uses_debug_service_lifecycle(self) -> None:
        module = tool()
        created = []

        class FakeDebugService:
            def __init__(self, executable=None):
                self.executable = executable
                self.states = [module.DebugState.READY, module.DebugState.FAILED]
                self.stopped = False
                created.append(self)

            @property
            def state(self):
                return self.states.pop(0) if self.states else module.DebugState.FAILED

            def start(self, config, **kwargs):
                self.config = config
                return module.DebugState.READY

            def stop(self):
                self.stopped = True

        output = io.StringIO()
        with mock.patch.object(module, "DebugService", FakeDebugService), \
                mock.patch.object(module, "list_probes", return_value=(ProbeInfo("DEBUG123", "ST-Link", "test"),)), \
                mock.patch.object(module.time, "sleep"), redirect_stdout(output):
            result = module.main(["debug", "server", "--probe-serial", "DEBUG123", "--json"])

        self.assertEqual(result, 1)
        self.assertEqual(created[0].config.probe.serial, "DEBUG123")
        self.assertTrue(created[0].stopped)

    def test_default_gateway_real_lifecycle_arms_guard_without_local_gdb(self) -> None:
        module = tool()
        created = []

        class FakeDebugService:
            def __init__(self, executable=None):
                self.executable = executable
                self._state_reads = 0
                self.stopped = False
                created.append(self)

            @property
            def state(self):
                self._state_reads += 1
                return module.DebugState.READY if self._state_reads == 1 else module.DebugState.STOPPED

            def start(self, config, **kwargs):
                self.config = config
                return module.DebugState.READY

            def stop(self):
                self.stopped = True

        class FakeTcl:
            def __init__(self, _endpoint):
                self.state = "running"

            def wait_target_state(self, *args, **kwargs):
                return self.state

            def resume_target(self):
                self.state = "running"
                return self.state

        output = io.StringIO()
        probe = ProbeInfo("AUTO123", "ST-Link", "test")
        with mock.patch.object(module, "DebugService", FakeDebugService), \
                mock.patch.object(module, "SafeTclClient", FakeTcl), \
                mock.patch.object(module, "list_probes", return_value=(probe,)), \
                mock.patch.object(module.time, "sleep"), redirect_stdout(output):
            result = module.main(["debug", "--json"])

        self.assertEqual(result, 0)
        self.assertTrue(created[0].stopped)
        self.assertEqual(created[0].config.tcl_port, 6666)
        self.assertEqual(created[0].config.probe.serial, "AUTO123")
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        command = next(record["command"] for record in records if record["event"] == "openocd")
        self.assertIn("adapter serial AUTO123", command)
        role = next(record for record in records if record["event"] == "debug_role")
        self.assertFalse(role["requires_local_gdb"])
        guard_events = [record.get("guard_event") for record in records if record["event"] == "remote_guard"]
        self.assertIn("armed", guard_events)
        self.assertIn("shutdown_restore", guard_events)

    def test_gateway_sigterm_runs_guarded_cleanup_before_openocd_stops(self) -> None:
        """A service manager SIGTERM must exercise the same cleanup as Ctrl-C."""
        module = tool()
        created = []
        installed_handlers = {}
        sentinel_previous_handler = object()

        def fake_signal(signum, handler):
            previous = installed_handlers.get(signum, sentinel_previous_handler)
            installed_handlers[signum] = handler
            return previous

        class FakeDebugService:
            def __init__(self, executable=None):
                self.executable = executable
                self._state_reads = 0
                self.stopped = False
                created.append(self)

            @property
            def state(self):
                self._state_reads += 1
                handler = installed_handlers.get(signal.SIGTERM)
                if self._state_reads == 1 and handler is not None:
                    handler(signal.SIGTERM, None)
                return module.DebugState.FAILED

            def start(self, config, **kwargs):
                self.config = config
                return module.DebugState.READY

            def stop(self):
                self.stopped = True

        class FakeTcl:
            def __init__(self, _endpoint):
                self.state = "running"

            def wait_target_state(self, *args, **kwargs):
                return self.state

            def resume_target(self):
                self.state = "running"
                return self.state

        output = io.StringIO()
        probe = ProbeInfo("TERM123", "ST-Link", "test")
        with mock.patch.object(module, "DebugService", FakeDebugService), \
                mock.patch.object(module, "SafeTclClient", FakeTcl), \
                mock.patch.object(signal, "signal", side_effect=fake_signal), \
                mock.patch.object(module, "list_probes", return_value=(probe,)), \
                mock.patch.object(module.time, "sleep"), redirect_stdout(output):
            result = module.main(["debug", "gateway", "--json"])

        self.assertEqual(result, 0)
        self.assertTrue(created[0].stopped)
        self.assertIs(installed_handlers[signal.SIGTERM], sentinel_previous_handler)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        guard_events = [record.get("guard_event") for record in records if record["event"] == "remote_guard"]
        self.assertIn("shutdown_restore", guard_events)

    def test_real_debug_gateway_fails_closed_when_multiple_probes_are_connected(self) -> None:
        module = tool()
        probes = (
            ProbeInfo("A", "ST-Link A", "test"),
            ProbeInfo("B", "ST-Link B", "test"),
        )
        output = io.StringIO()
        with mock.patch.object(module, "list_probes", return_value=probes), \
                mock.patch.object(module, "DebugService") as service, redirect_stdout(output):
            result = module.main(["debug", "gateway", "--json"])
        self.assertEqual(result, 1)
        self.assertFalse(service.called)
        record = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(record["reason_code"], "MULTIPLE_PROBES")

    def test_real_integrated_debug_fails_closed_when_multiple_probes_are_connected(self) -> None:
        module = tool()
        probes = (
            ProbeInfo("A", "ST-Link A", "test"),
            ProbeInfo("B", "ST-Link B", "test"),
        )
        output = io.StringIO()
        with mock.patch.object(module, "list_probes", return_value=probes), \
                mock.patch.object(module, "DebugSession") as session, redirect_stdout(output):
            result = module.main(["debug", "poll", "--json"])
        self.assertEqual(result, 1)
        self.assertFalse(session.called)
        record = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(record["reason_code"], "MULTIPLE_PROBES")

    def test_real_debug_rejects_unknown_requested_probe_before_openocd(self) -> None:
        module = tool()
        probes = (ProbeInfo("SAFE", "ST-Link", "test"),)
        output = io.StringIO()
        with mock.patch.object(module, "list_probes", return_value=probes), \
                mock.patch.object(module, "DebugService") as service, redirect_stdout(output):
            result = module.main(["debug", "gateway", "--probe-serial", "MISSING", "--json"])
        self.assertEqual(result, 1)
        self.assertFalse(service.called)
        record = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(record["reason_code"], "PROBE_NOT_FOUND")

    def test_legacy_server_alias_cannot_listen_for_remote_gdb(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "server", "--bind-address", "0.0.0.0",
                "--gdb-port", "4333",
                "--probe-serial", "TEST-PROBE", "--dry-run", "--json",
            ])
        self.assertEqual(result, 1)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(records[0]["reason_code"], "DEPRECATED_ALIAS")
        self.assertIn("loopback-only", records[-1]["message"])
        self.assertFalse(any(record["event"] == "openocd" for record in records))

    def test_legacy_server_alias_keeps_telnet_disabled_on_loopback(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "server", "--telnet-port", "4444", "--dry-run", "--json",
            ])
        self.assertEqual(result, 1)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIn("Telnet", records[-1]["message"])

    def test_remote_debug_rejects_telnet_listener(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "server", "--bind-address", "0.0.0.0", "--telnet-port", "4444",
                "--dry-run", "--json",
            ])
        self.assertEqual(result, 1)
        self.assertIn("loopback-only", output.getvalue())

    def test_debug_allows_explicit_tcl_on_loopback(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "server", "--gdb-port", "3333", "--tcl-port", "6666",
                "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        command = next(record["command"] for record in records if record["event"] == "openocd")
        self.assertIn("gdb port 3333", command)
        self.assertIn("tcl port 6666", command)

    def test_remote_debug_rejects_tcl_listener(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "server", "--bind-address", "0.0.0.0", "--tcl-port", "6666",
                "--dry-run", "--json",
            ])
        self.assertEqual(result, 1)
        self.assertIn("loopback-only", output.getvalue())

    def test_debug_rejects_duplicate_openocd_ports(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "--gdb-port", "3333", "--tcl-port", "3333",
                "--dry-run", "--json",
            ])
        self.assertEqual(result, 1)
        self.assertIn("must be distinct", output.getvalue())

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

    def test_target_health_reports_bootability_crc_and_vector_without_write_surface(self) -> None:
        module = tool()
        metadata = SimpleNamespace(
            classification="VALID", valid=True, magic=0x53544C4D, format_version=1,
            state=3, state_name="CONFIRMED", image_size=126580, image_crc32=0xC99ED31F,
            board_token="B300_F407ZE", sequence=4, meta_crc32=0x11111111,
            calculated_meta_crc32=0x11111111,
        )
        vector = SimpleNamespace(
            initial_msp=0x200185C8, reset_vector=0x08010361, valid=True,
            reason="Application vector is valid.",
        )
        health = SimpleNamespace(
            lifecycle="BOOTABLE", bootable=True, reason="evidence matches",
            next_action="No action is required.", bytes_checked=126580,
            image_crc_valid=True, actual_image_crc32=0xC99ED31F,
            metadata=metadata, application_vector=vector,
        )
        created = []

        class FakeService:
            def __init__(self, executable=None):
                self.executable = executable
                created.append(self)
            def inspect_application_health(self, probe):
                self.probe = probe
                return health

        output = io.StringIO()
        with mock.patch.object(module, "B300Service", FakeService), \
                mock.patch.object(module, "list_probes", return_value=(
                    ProbeInfo("TEST123", "ST-Link", "test"),
                )), redirect_stdout(output):
            result = module.main(["target", "health", "--json"])

        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["command"], "target health")
        self.assertEqual(record["health"]["lifecycle"], "BOOTABLE")
        self.assertTrue(record["health"]["bootable"])
        self.assertTrue(record["health"]["image_crc_valid"])
        self.assertEqual(record["health"]["expected_image_crc32"], "0xC99ED31F")
        self.assertEqual(record["health"]["actual_image_crc32"], "0xC99ED31F")
        self.assertTrue(record["health"]["application_vector"]["valid"])
        self.assertEqual(created[0].probe.serial, "TEST123")
        rendered = output.getvalue().lower()
        for forbidden in ("erase_sector", "mass_erase", "flash protect", "mww ", "program {"):
            self.assertNotIn(forbidden, rendered)

    def test_support_bundle_cli_emits_privacy_bounded_result(self) -> None:
        module = tool()
        snapshot = {
            "diagnostics": {"conclusion": "READY_FOR_APPLICATION_FLASH"},
            "application_health": {"lifecycle": "BOOTABLE"},
        }
        created_service = object()
        result_object = SimpleNamespace(
            path=Path("support.zip").resolve(),
            sha256="A" * 64,
            size_bytes=1234,
        )
        output = io.StringIO()
        with mock.patch.object(module, "B300Service", return_value=created_service), \
                mock.patch.object(module, "collect_support_snapshot", return_value=snapshot) as collect, \
                mock.patch.object(module, "write_support_bundle", return_value=result_object) as write, \
                mock.patch.object(module, "list_probes", return_value=()), \
                redirect_stdout(output):
            rc = module.main(["support", "bundle", "support.zip", "--json"])
        self.assertEqual(rc, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["command"], "support bundle")
        self.assertEqual(record["status"], "ok")
        self.assertTrue(record["privacy_bounded"])
        self.assertEqual(record["diagnostics_conclusion"], "READY_FOR_APPLICATION_FLASH")
        self.assertEqual(record["application_lifecycle"], "BOOTABLE")
        self.assertEqual(record["sha256"], "A" * 64)
        collect.assert_called_once()
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], Path("support.zip"))
        self.assertFalse(write.call_args.kwargs["force"])

    def test_support_without_bundle_subcommand_fails_without_hardware_access(self) -> None:
        module = tool()
        output = io.StringIO()
        with mock.patch.object(module, "B300Service") as service, redirect_stdout(output):
            rc = module.main(["support", "--json"])
        self.assertEqual(rc, 1)
        service.assert_not_called()
        record = json.loads(output.getvalue())
        self.assertEqual(record["reason_code"], "SUPPORT_SUBCOMMAND_REQUIRED")

    def test_target_health_returns_nonzero_for_nonbootable_evidence(self) -> None:
        module = tool()
        metadata = SimpleNamespace(
            classification="VALID", valid=True, magic=0x53544C4D, format_version=1,
            state=2, state_name="VERIFIED", image_size=64, image_crc32=0x12345678,
            board_token="B300_F407ZE", sequence=9, meta_crc32=1, calculated_meta_crc32=1,
        )
        health = SimpleNamespace(
            lifecycle="STLINK_VERIFIED_PENDING", bootable=False,
            reason="pending one-shot Bootloader consumption",
            next_action="Reset once", bytes_checked=64, image_crc_valid=True,
            actual_image_crc32=0x12345678, metadata=metadata, application_vector=None,
        )
        class FakeService:
            def __init__(self, executable=None): pass
            def inspect_application_health(self, probe): return health

        output = io.StringIO()
        with mock.patch.object(module, "B300Service", FakeService), \
                mock.patch.object(module, "list_probes", return_value=(
                    ProbeInfo("TEST123", "ST-Link", "test"),
                )), redirect_stdout(output):
            result = module.main(["target", "health", "--json"])
        self.assertEqual(result, 1)
        record = json.loads(output.getvalue())
        self.assertEqual(record["status"], "warning")
        self.assertEqual(record["health"]["lifecycle"], "STLINK_VERIFIED_PENDING")

    def test_debug_gateway_dry_run_uses_fixed_safe_headless_profile(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["debug", "gateway", "--dry-run", "--json"])
        self.assertEqual(result, 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        role = next(record for record in records if record["event"] == "debug_role")
        command = " ".join(next(record["command"] for record in records if record["event"] == "openocd")).lower()
        self.assertEqual(role["role"], "gateway")
        self.assertFalse(role["requires_local_gdb"])
        self.assertEqual(role["gdb_endpoint"], "127.0.0.1:3333")
        self.assertEqual(role["tcl_endpoint"], "127.0.0.1:6666")
        self.assertIn("bindto 127.0.0.1", command)
        self.assertIn("gdb port 3333", command)
        self.assertIn("tcl port 6666", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("gdb flash_program disable", command)
        self.assertIn("gdb breakpoint_override hard", command)
        for forbidden in ("flash erase_sector", "mass_erase", "program {", "flash protect", "mww "):
            self.assertNotIn(forbidden, command)

    def test_debug_gateway_rejects_nonloopback_and_duplicate_ports_even_in_dry_run(self) -> None:
        for argv, expected in (
            (["debug", "gateway", "--bind-address", "0.0.0.0", "--dry-run", "--json"], "loopback-only"),
            (["debug", "gateway", "--gdb-port", "6666", "--dry-run", "--json"], "must be distinct"),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(argv)
            self.assertEqual(result, 1)
            self.assertIn(expected, output.getvalue())

    def test_debug_client_dry_run_uses_password_ssh_loopback_forwarding(self) -> None:
        module = tool()
        output = io.StringIO()
        with redirect_stdout(output):
            result = module.main([
                "debug", "client", "--ssh-host", "192.168.1.109",
                "--ssh-user", "automation", "--client-action", "inspect",
                "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["event"], "debug_client_plan")
        self.assertEqual(record["role"], "client")
        self.assertEqual(record["action"], "inspect")
        self.assertEqual(record["remote_transport"], "ssh-local-forwarding")
        rendered = " ".join(record["ssh_command"])
        self.assertIn("PasswordAuthentication=yes", rendered)
        self.assertIn("KbdInteractiveAuthentication=yes", rendered)
        self.assertIn("PubkeyAuthentication=no", rendered)
        self.assertNotIn("-i", record["ssh_command"])
        self.assertNotIn("KnownHostsFile", rendered)
        self.assertIn("127.0.0.1:3333", rendered)
        self.assertIn("127.0.0.1:6666", rendered)
        for forbidden in ("flash erase_sector", "mass_erase", "flash protect", "mww "):
            self.assertNotIn(forbidden, rendered)

    def test_debug_client_requires_explicit_ssh_identity(self) -> None:
        module = tool()
        output = io.StringIO()
        with mock.patch.object(module.gateway_workflows, "apply_saved_remote_profile", return_value=None):
            with redirect_stdout(output):
                result = module.main(["debug", "client", "--dry-run", "--json"])
        self.assertEqual(result, 1)
        record = json.loads(output.getvalue())
        self.assertIn("--ssh-host", record["message"])

    def test_integrated_debug_inspect_dry_run_uses_safe_local_ports(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "inspect", "--dry-run", "--json",
            ])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["event"], "debug_plan")
        self.assertEqual(record["gdb_endpoint"], "127.0.0.1:3333")
        self.assertEqual(record["tcl_endpoint"], "127.0.0.1:6666")
        self.assertTrue(record["preserve_target_state"])
        command = " ".join(record["command"])
        self.assertIn("gdb port 3333", command)
        self.assertIn("tcl port 6666", command)
        self.assertIn("telnet port disabled", command)
        self.assertIn("gdb flash_program disable", command.lower())
        self.assertIn("gdb breakpoint_override hard", command.lower())
        for forbidden in ("flash erase_sector", "mass_erase", "program {", "flash protect", "mww "):
            self.assertNotIn(forbidden, command.lower())

    def test_integrated_debug_rejects_remote_bind_and_missing_variable_expression(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "debug", "inspect", "--bind-address", "0.0.0.0", "--dry-run", "--json",
            ])
        self.assertEqual(result, 1)
        self.assertIn("loopback-only", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["debug", "variable", "--dry-run", "--json"])
        self.assertEqual(result, 1)
        self.assertIn("requires --expression", output.getvalue())

    def test_integrated_debug_where_reports_symbol_location_and_always_stops(self) -> None:
        module = tool()
        created = []

        class FakeFrame:
            level = 0
            address = 0x08012345
            function = "Motor_Update"
            file = "motor.c"
            fullname = "C:/fw/motor.c"
            line = 417

        class FakeInfo:
            state = "CONNECTED"
            gdb_endpoint = "127.0.0.1:3333"
            tcl_endpoint = "127.0.0.1:6666"
            symbols = "C:/fw/firmware.elf"
            tcl_version = "OpenOCD test"
            initial_target_state = "target running"

        class FakeSession:
            def __init__(self, *args, **kwargs):
                self.stopped = False
                created.append(self)

            def start(self, config):
                self.config = config
                return FakeInfo()

            def capture_where(self):
                return FakeFrame()

            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.elf"
            symbols.write_bytes(b"ELF")
            output = io.StringIO()
            with mock.patch.object(module, "DebugSession", FakeSession), \
                    mock.patch.object(module, "list_probes", return_value=(ProbeInfo("TEST123", "ST-Link", "test"),)), \
                    redirect_stdout(output):
                result = module.main([
                    "debug", "where", "--symbols", str(symbols), "--json",
                ])

        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["command"], "debug where")
        self.assertEqual(record["frame"]["address"], "0x08012345")
        self.assertEqual(record["frame"]["function"], "Motor_Update")
        self.assertEqual(record["frame"]["line"], 417)
        self.assertTrue(created[0].stopped)

    def test_integrated_sample_dry_run_is_bounded_multi_variable_and_non_mutating(self) -> None:
        module = tool()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            symbols = root / "firmware.axf"
            symbols.write_bytes(b"AXF")
            sample_output = root / "samples.csv"
            output = io.StringIO()
            with redirect_stdout(output):
                result = module.main([
                    "debug", "sample", "--symbols", str(symbols),
                    "--expression", "xTickCount",
                    "--sample-expression", "motorSpeed",
                    "--samples", "5", "--sample-interval", "0.2",
                    "--sample-output", str(sample_output),
                    "--dry-run", "--json",
                ])
            self.assertFalse(sample_output.exists())
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["mode"], "sample")
        self.assertEqual(record["sample_expressions"], ["xTickCount", "motorSpeed"])
        self.assertEqual(record["sample_cycles"], 5)
        self.assertEqual(record["sample_interval"], 0.2)
        self.assertTrue(record["preserve_target_state"])
        rendered = " ".join(record["command"]).lower()
        for forbidden in ("flash erase_sector", "mass_erase", "program {", "flash protect", "mww "):
            self.assertNotIn(forbidden, rendered)

    def test_integrated_sample_validates_bounds_symbols_and_output_before_hardware(self) -> None:
        module = tool()
        cases = [
            (["debug", "sample", "--expression", "xTickCount", "--dry-run", "--json"], "requires --symbols"),
            (["debug", "sample", "--symbols", "missing.axf", "--expression", "xTickCount", "--samples", "0", "--dry-run", "--json"], "Sample cycles"),
            (["debug", "sample", "--symbols", "missing.axf", "--expression", "xTickCount", "--sample-interval", "0.01", "--dry-run", "--json"], "Sample interval"),
            (["debug", "sample", "--symbols", "missing.axf", "--expression", "xTickCount", "--sample-output", "samples.txt", "--dry-run", "--json"], "csv or .jsonl"),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                output = io.StringIO()
                with redirect_stdout(output):
                    result = module.main(argv)
                self.assertEqual(result, 1)
                self.assertIn(expected, output.getvalue())

    def test_integrated_sample_writes_csv_and_stops_session(self) -> None:
        module = tool()
        created = []

        class Info:
            gdb_endpoint = "127.0.0.1:3333"
            tcl_endpoint = "127.0.0.1:6666"
            tcl_version = "OpenOCD test"
            initial_target_state = "running"
            symbols = "C:/fw/firmware.axf"

        class FakeSession:
            def __init__(self, *args, **kwargs):
                self.stopped = False
                self.cycles = 0
                created.append(self)
            def start(self, config):
                return Info()
            def capture_variables(self, expressions):
                self.cycles += 1
                return tuple(
                    SimpleNamespace(expression=expression, value=str(self.cycles * index))
                    for index, expression in enumerate(expressions, start=1)
                )
            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            symbols = root / "firmware.axf"
            symbols.write_bytes(b"AXF")
            sample_output = root / "samples.csv"
            output = io.StringIO()
            with mock.patch.object(module, "DebugSession", FakeSession), \
                    mock.patch.object(module, "list_probes", return_value=(ProbeInfo("TEST123", "ST-Link", "test"),)), \
                    redirect_stdout(output):
                result = module.main([
                    "debug", "sample", "--symbols", str(symbols),
                    "--expression", "speed", "--sample-expression", "current",
                    "--samples", "2", "--sample-interval", "0.1",
                    "--sample-output", str(sample_output), "--json",
                ])
            csv_text = sample_output.read_text(encoding="utf-8")
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["sampling"]["sample_cycles"], 2)
        self.assertEqual(len(record["sampling"]["samples"]), 4)
        self.assertEqual(record["sampling"]["samples"][0]["expression"], "speed")
        self.assertIn("speed", csv_text)
        self.assertIn("current", csv_text)
        self.assertTrue(created[0].stopped)

    def test_debug_client_sample_dry_run_uses_same_bounded_sampling_contract(self) -> None:
        module = tool()
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"AXF")
            output = io.StringIO()
            with redirect_stdout(output):
                result = module.main([
                    "debug", "client", "--ssh-host", "gateway.example",
                    "--ssh-user", "automation", "--client-action", "sample",
                    "--symbols", str(symbols), "--expression", "xTickCount",
                    "--sample-expression", "motorSpeed", "--samples", "3",
                    "--sample-interval", "0.25", "--dry-run", "--json",
                ])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["event"], "debug_client_plan")
        self.assertEqual(record["action"], "sample")
        self.assertEqual(record["sample_expressions"], ["xTickCount", "motorSpeed"])
        self.assertEqual(record["sample_cycles"], 3)
        self.assertEqual(record["sample_interval"], 0.25)
        self.assertEqual(record["remote_transport"], "ssh-local-forwarding")

    def test_integrated_break_dry_run_requires_symbols_and_is_hardware_only(self) -> None:
        module = tool()
        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"AXF")
            output = io.StringIO()
            with redirect_stdout(output):
                result = module.main([
                    "debug", "break", "--location", "main",
                    "--symbols", str(symbols), "--timeout", "2.5",
                    "--dry-run", "--json",
                ])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["mode"], "break")
        self.assertEqual(record["location"], "main")
        self.assertEqual(record["timeout"], 2.5)
        rendered = " ".join(record["command"]).lower()
        self.assertIn("gdb flash_program disable", rendered)
        self.assertIn("gdb breakpoint_override hard", rendered)
        for forbidden in ("flash erase_sector", "mass_erase", "program {", "flash protect", "mww "):
            self.assertNotIn(forbidden, rendered)

    def test_integrated_break_watch_validate_required_inputs_before_hardware(self) -> None:
        module = tool()
        cases = [
            (["debug", "break", "--location", "main", "--dry-run", "--json"], "requires --symbols"),
            (["debug", "break", "--dry-run", "--json"], "requires --location"),
            (["debug", "watch", "--dry-run", "--json"], "requires --expression"),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                output = io.StringIO()
                with redirect_stdout(output):
                    result = module.main(argv)
                self.assertEqual(result, 1)
                self.assertIn(expected, output.getvalue())

    def test_integrated_break_reports_verified_hit_and_stops_session(self) -> None:
        module = tool()
        created = []

        class Frame:
            level = 0
            address = 0x08025FDE
            function = "vApplicationIdleHook"
            file = "User\\main.c"
            fullname = "C:/fw/User/main.c"
            line = 87

        class Info:
            gdb_endpoint = "127.0.0.1:3333"
            tcl_endpoint = "127.0.0.1:6666"
            tcl_version = "OpenOCD test"
            initial_target_state = "running"
            symbols = "C:/fw/firmware.axf"

        class FakeSession:
            def __init__(self, *args, **kwargs):
                self.stopped = False
                created.append(self)
            def start(self, config):
                return Info()
            def break_once(self, location, timeout):
                return SimpleNamespace(
                    kind="hardware-breakpoint", number=1, location=location,
                    reason="breakpoint-hit", frame=Frame(),
                )
            def stop(self):
                self.stopped = True

        with tempfile.TemporaryDirectory() as directory:
            symbols = Path(directory) / "firmware.axf"
            symbols.write_bytes(b"AXF")
            output = io.StringIO()
            with mock.patch.object(module, "DebugSession", FakeSession), \
                    mock.patch.object(module, "list_probes", return_value=(ProbeInfo("TEST123", "ST-Link", "test"),)), \
                    redirect_stdout(output):
                result = module.main([
                    "debug", "break", "--location", "vApplicationIdleHook",
                    "--symbols", str(symbols), "--json",
                ])
        self.assertEqual(result, 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["hit"]["kind"], "hardware-breakpoint")
        self.assertEqual(record["hit"]["reason"], "breakpoint-hit")
        self.assertEqual(record["hit"]["frame"]["line"], 87)
        self.assertTrue(created[0].stopped)

    def test_flash_rejects_path_that_can_break_openocd_braces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "bad}name.hex"
            image.write_text(VALID_HEX, encoding="ascii")
            output = io.StringIO()
            with redirect_stdout(output):
                result = tool().main(["flash", str(image), "--dry-run", "--json"])
        self.assertEqual(result, 1)
        self.assertIn("unsafe character", output.getvalue())

    def test_cli_json_reports_structured_flash_phases(self) -> None:
        module = tool()

        class FakeService:
            def __init__(self, executable=None):
                pass

            def inspect_image(self, path):
                return module.inspect_image(path)

            def inspect_target(self, probe, event_sink=None):
                return TargetInfo(0x101F6413, 512, 3.09, "S0-S2 protected", (0, 1, 2), True)

            def plan(self, image, probe, target):
                return module.build_flash_plan(image, probe, target)

            def flash_command(self, plan):
                return ["openocd", "program"]

            def reset_command(self, probe):
                return ["openocd", "reset"]

            def flash(self, plan, event_sink=None, phase_sink=None):
                phase_sink(FlashPhaseEvent("erasing", 20, "Erasing Sector 3 through 7"))
                phase_sink(FlashPhaseEvent("succeeded", 100, "Application is running"))
                return SimpleNamespace(
                    status="succeeded",
                    succeeded=True,
                    boot_verification=BootVerification(
                        0x08010001, 0, True, "Application is running."
                    ),
                    failure_phase=None,
                    reason="",
                    next_action="",
                )

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "application.hex"
            image.write_text(VALID_HEX, encoding="ascii")
            output = io.StringIO()
            with mock.patch.object(module, "B300Service", FakeService), \
                    mock.patch.object(
                        module, "list_probes",
                        return_value=(ProbeInfo("FLASH123", "ST-Link", "test"),),
                    ), redirect_stdout(output):
                result = module.main(["flash", str(image), "--json"])

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        phases = [record["phase"] for record in records
                  if record["event"] == "flash_phase"]
        self.assertEqual(result, 0)
        self.assertEqual(phases, ["erasing", "succeeded"])


    def test_factory_dry_run_uses_trusted_bundle_and_safe_s0_s2_sequence(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main([
                "provision-bootloader", "--dry-run", "--json",
                "--probe-serial", "FACTORY123",
            ])
        self.assertEqual(result, 0)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        artifact = next(item for item in records if item["event"] == "factory_artifact")
        self.assertEqual(
            artifact["sha256"],
            "085E44E8339D21EE2D136D11F86C2103295812CB2438807774B232647D3F75A1",
        )
        transactions = [item for item in records if item["event"] == "openocd"]
        self.assertEqual([item["phase"] for item in transactions], [
            "unprotect", "program_verify", "reprotect", "reset",
        ])
        rendered = " ".join(" ".join(item["command"]) for item in transactions)
        self.assertIn("flash protect 0 0 2 off", rendered)
        self.assertIn("flash erase_sector 0 0 2", rendered)
        self.assertIn("flash protect 0 0 2 on", rendered)
        self.assertGreaterEqual(rendered.count("reset halt"), 2)
        self.assertIn("reset run", rendered)
        self.assertNotIn("mass_erase", rendered)
        self.assertNotIn("stm32f2x lock", rendered)
        self.assertNotIn("stm32f2x unlock", rendered)

    def test_factory_rejects_user_supplied_bootloader_path(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool().main([
                "provision-bootloader", "C:\\temp\\custom-bootloader.hex",
                "--dry-run",
            ])

    def test_factory_real_run_requires_explicit_confirmation_before_hardware(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = tool().main(["provision-bootloader", "--json"])
        self.assertEqual(result, 1)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        error = next(item for item in records if item["event"] == "error")
        self.assertEqual(error["phase"], "authorization")
        self.assertIn("--confirm-factory-provision", error["reason"])

if __name__ == "__main__":
    unittest.main()
