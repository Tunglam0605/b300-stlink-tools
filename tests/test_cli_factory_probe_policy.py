from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.factory_policy import build_factory_plan, build_factory_preview
from b300_core.factory_resource import load_trusted_bootloader
from b300_core.models import ProbeInfo, TargetInfo


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "b300_stlink.py"


def tool():
    spec = importlib.util.spec_from_file_location("b300_stlink_factory_policy", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe(serial: str | None, identity: str) -> ProbeInfo:
    return ProbeInfo(serial, "ST-Link", "test", usb_identity=identity)


class FakeFactoryService:
    created = []
    target = TargetInfo(
        0x101F6413, 512, 3.09, "Sector 0-2 protected", (0, 1, 2), True, False,
    )
    inspect_error = None

    def __init__(self, executable=None) -> None:
        self.calls = []
        type(self).created.append(self)

    def trusted_bootloader(self):
        return load_trusted_bootloader()

    def factory_preview(self, image, selected_probe):
        return build_factory_preview(image, selected_probe)

    def factory_protect_command(self, selected_probe, enabled):
        return ["openocd", "flash protect 0 0 2 %s" % ("on" if enabled else "off")]

    def factory_flash_command(self, plan):
        return ["openocd", "flash erase_sector 0 0 2", "program", "verify"]

    def reset_command(self, selected_probe):
        return ["openocd", "reset run"]

    def inspect_target(self, selected_probe, event_sink=None):
        self.calls.append(("inspect", selected_probe))
        if self.inspect_error is not None:
            raise self.inspect_error
        return self.target

    def factory_plan(self, image, selected_probe, target):
        self.calls.append(("plan", selected_probe))
        return build_factory_plan(image, selected_probe, target)

    def provision_bootloader(self, plan, event_sink=None, phase_sink=None):
        self.calls.append(("provision", plan.probe))
        return SimpleNamespace(
            status="succeeded", succeeded=True, failure_phase=None, reason="",
            next_action="", final_target=plan.target,
        )


class FactoryCliProbePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeFactoryService.created = []
        FakeFactoryService.target = TargetInfo(
            0x101F6413, 512, 3.09, "Sector 0-2 protected", (0, 1, 2), True, False,
        )
        FakeFactoryService.inspect_error = None

    def run_factory(self, probes, *extra_args):
        module = tool()
        output = io.StringIO()
        with mock.patch.object(module, "B300Service", FakeFactoryService), \
                mock.patch.object(module, "list_probes", return_value=tuple(probes)), \
                redirect_stdout(output):
            result = module.main([
                "provision-bootloader", "--confirm-factory-provision", "--json",
                *extra_args,
            ])
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        return result, records, FakeFactoryService.created[-1]

    def test_missing_confirmation_blocks_before_probe_discovery_or_target_access(self) -> None:
        module = tool()
        output = io.StringIO()
        with mock.patch.object(module, "B300Service", FakeFactoryService), \
                mock.patch.object(
                    module, "list_probes", side_effect=AssertionError("discovery must not run")
                ) as discovery, redirect_stdout(output):
            result = module.main(["provision-bootloader", "--json"])

        self.assertEqual(result, 1)
        discovery.assert_not_called()
        self.assertEqual(FakeFactoryService.created, [])

    def test_factory_dry_run_does_not_discover_probe_or_inspect_target(self) -> None:
        module = tool()
        output = io.StringIO()
        with mock.patch.object(module, "B300Service", FakeFactoryService), \
                mock.patch.object(
                    module, "list_probes", side_effect=AssertionError("discovery must not run")
                ) as discovery, redirect_stdout(output):
            result = module.main(["provision-bootloader", "--dry-run", "--json"])

        self.assertEqual(result, 0)
        discovery.assert_not_called()
        self.assertFalse(any(call[0] == "inspect" for call in FakeFactoryService.created[-1].calls))

    def test_zero_probes_is_blocked_with_stable_reason_before_target_access(self) -> None:
        result, records, service = self.run_factory(())

        self.assertEqual(result, 1)
        error = next(record for record in records if record["event"] == "error")
        self.assertEqual(error["reason_code"], "NO_PROBE")
        self.assertFalse(any(call[0] == "inspect" for call in service.calls))

    def test_one_serialized_probe_is_selected_for_confirmed_factory(self) -> None:
        result, _records, service = self.run_factory((probe("FACTORY123", "usb-1"),))

        self.assertEqual(result, 0)
        self.assertEqual([call[0] for call in service.calls], ["inspect", "plan", "provision"])
        self.assertEqual(service.calls[0][1].serial, "FACTORY123")

    def test_one_serialless_physical_probe_is_selected_without_fabricating_serial(self) -> None:
        result, _records, service = self.run_factory((probe(None, "usb-1"),))

        self.assertEqual(result, 0)
        self.assertIsNone(service.calls[0][1].serial)
        self.assertIsNone(service.calls[-1][1].serial)

    def test_multiple_probes_require_an_exact_serial_before_target_access(self) -> None:
        probes = (probe("ONE", "usb-1"), probe("TWO", "usb-2"))
        result, records, service = self.run_factory(probes)

        self.assertEqual(result, 1)
        error = next(record for record in records if record["event"] == "error")
        self.assertEqual(error["reason_code"], "MULTIPLE_PROBES")
        self.assertFalse(any(call[0] == "inspect" for call in service.calls))

    def test_multiple_serialless_probes_are_blocked_as_unpinnable(self) -> None:
        probes = (probe(None, "usb-1"), probe(None, "usb-2"))
        result, records, service = self.run_factory(probes)

        self.assertEqual(result, 1)
        error = next(record for record in records if record["event"] == "error")
        self.assertEqual(error["reason_code"], "UNPINNABLE_MULTIPLE_PROBES")
        self.assertFalse(any(call[0] == "inspect" for call in service.calls))

    def test_bad_explicit_serial_is_blocked_with_stable_reason(self) -> None:
        result, records, service = self.run_factory(
            (probe("ONE", "usb-1"),), "--probe-serial", "MISSING",
        )

        self.assertEqual(result, 1)
        error = next(record for record in records if record["event"] == "error")
        self.assertEqual(error["reason_code"], "PROBE_NOT_FOUND")
        self.assertFalse(any(call[0] == "inspect" for call in service.calls))

    def test_target_inspection_failure_blocks_factory_transaction(self) -> None:
        FakeFactoryService.inspect_error = RuntimeError("target unavailable")
        result, records, service = self.run_factory((probe("FACTORY123", "usb-1"),))

        self.assertEqual(result, 1)
        error = next(record for record in records if record["event"] == "error")
        self.assertEqual(error["phase"], "target_check")
        self.assertFalse(any(call[0] == "provision" for call in service.calls))

    def test_unsafe_target_reports_are_blocked_before_factory_transaction(self) -> None:
        targets = {
            "wrp incomplete": TargetInfo(0x101F6413, 512, 3.09, "unknown"),
            "wrong device": TargetInfo(0x419, 512, 3.09, "S0-S2 protected", (), True),
            "wrong flash": TargetInfo(0x413, 2048, 3.09, "S0-S2 protected", (), True),
            "rdp": TargetInfo(0x413, 512, 3.09, "RDP enabled", (), True, True),
        }
        for label, target in targets.items():
            with self.subTest(label=label):
                FakeFactoryService.created = []
                FakeFactoryService.target = target
                result, _records, service = self.run_factory(
                    (probe("FACTORY123", "usb-1"),)
                )
                self.assertEqual(result, 1)
                self.assertFalse(any(call[0] == "provision" for call in service.calls))


if __name__ == "__main__":
    unittest.main()
