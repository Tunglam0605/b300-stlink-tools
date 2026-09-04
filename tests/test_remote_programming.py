from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from b300_core.models import ProbeRef
from b300_core.remote_programming import (
    FirmwareKind,
    GatewayProgrammingService,
    RemoteFirmwareManifest,
    RemotePrivilege,
    RemoteProgrammingDenied,
    RemoteProgrammingOperation,
)


class FakeService:
    def __init__(self) -> None:
        self.calls = []
        self.plan_value = object()
        self.flash_result = object()

    def inspect_image(self, path):
        self.calls.append(("inspect_image", Path(path)))
        return "IMAGE"

    def inspect_target(self, probe, event_sink=None):
        self.calls.append(("inspect_target", probe.serial))
        return "TARGET"

    def plan(self, image, probe, target):
        self.calls.append(("plan", image, probe.serial, target))
        return self.plan_value

    def flash(self, plan, event_sink=None, phase_sink=None, cancel_event=None):
        self.calls.append(("flash", plan))
        return self.flash_result


class RemoteProgrammingTests(unittest.TestCase):
    def make_file(self, root: Path, name: str = "application.hex", payload: bytes = b":00000001FF\n") -> Path:
        path = root / name
        path.write_bytes(payload)
        return path

    def test_manifest_is_content_addressed_and_validates_application_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_file(Path(directory))
            manifest = RemoteFirmwareManifest.from_file(
                path,
                operation=RemoteProgrammingOperation.FLASH_APPLICATION,
                firmware_kind=FirmwareKind.APPLICATION,
            )
            self.assertEqual(manifest.file_name, "application.hex")
            self.assertEqual(manifest.size, path.stat().st_size)
            self.assertEqual(len(manifest.sha256), 64)
            self.assertEqual(manifest.privilege, RemotePrivilege.STANDARD)
            self.assertTrue(manifest.matches_file(path))

    def test_manifest_rejects_path_traversal_and_wrong_privilege(self) -> None:
        bad = RemoteFirmwareManifest(
            operation=RemoteProgrammingOperation.FLASH_APPLICATION,
            firmware_kind=FirmwareKind.APPLICATION,
            file_name="../application.hex",
            size=12,
            sha256="0" * 64,
        )
        with self.assertRaises(ValueError):
            bad.validate()

        wrong_privilege = RemoteFirmwareManifest(
            operation=RemoteProgrammingOperation.FLASH_BOOTLOADER,
            firmware_kind=FirmwareKind.BOOTLOADER,
            file_name="boot.hex",
            size=12,
            sha256="0" * 64,
            privilege=RemotePrivilege.STANDARD,
        )
        with self.assertRaises(ValueError):
            wrong_privilege.validate()

    def test_gateway_refuses_tampered_received_file_before_service_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.make_file(root)
            manifest = RemoteFirmwareManifest.from_file(
                path,
                operation=RemoteProgrammingOperation.FLASH_APPLICATION,
                firmware_kind=FirmwareKind.APPLICATION,
            )
            path.write_bytes(b"tampered")
            fake = FakeService()
            gateway = GatewayProgrammingService(service=fake)
            with self.assertRaises(RemoteProgrammingDenied):
                gateway.prepare_application(manifest, path, ProbeRef("STLINK123"))
            self.assertEqual(fake.calls, [])

    def test_gateway_reuses_existing_local_safety_plan_and_flash_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_file(Path(directory))
            manifest = RemoteFirmwareManifest.from_file(
                path,
                operation=RemoteProgrammingOperation.FLASH_APPLICATION,
                firmware_kind=FirmwareKind.APPLICATION,
            )
            fake = FakeService()
            gateway = GatewayProgrammingService(service=fake)
            approval = gateway.prepare_application(manifest, path, ProbeRef("STLINK123"))
            self.assertIs(approval.plan, fake.plan_value)
            self.assertEqual(fake.calls[0], ("inspect_image", path.resolve()))
            self.assertEqual(fake.calls[1], ("inspect_target", "STLINK123"))
            result = gateway.flash_application(approval)
            self.assertIs(result, fake.flash_result)
            self.assertEqual(fake.calls[-1], ("flash", fake.plan_value))

    def test_bin_elf_axf_transfer_contract_exists_but_execution_fails_closed(self) -> None:
        for suffix in (".bin", ".elf", ".axf"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                path = self.make_file(Path(directory), "application" + suffix, b"payload")
                manifest = RemoteFirmwareManifest.from_file(
                    path,
                    operation=RemoteProgrammingOperation.FLASH_APPLICATION,
                    firmware_kind=FirmwareKind.APPLICATION,
                )
                gateway = GatewayProgrammingService(service=FakeService())
                with self.assertRaises(RemoteProgrammingDenied):
                    gateway.prepare_application(manifest, path, ProbeRef("STLINK123"))

    def test_remote_bootloader_stays_disabled_even_with_elevated_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_file(Path(directory), "bootloader.hex")
            manifest = RemoteFirmwareManifest.from_file(
                path,
                operation=RemoteProgrammingOperation.FLASH_BOOTLOADER,
                firmware_kind=FirmwareKind.BOOTLOADER,
                privilege=RemotePrivilege.ELEVATED,
            )
            gateway = GatewayProgrammingService(service=FakeService())
            with self.assertRaises(RemoteProgrammingDenied):
                gateway.prepare_bootloader(manifest, path, ProbeRef("STLINK123"))

    def test_public_remote_operation_surface_has_no_mass_erase_or_raw_write(self) -> None:
        operations = {item.value for item in GatewayProgrammingService.supported_operations()}
        self.assertIn("FLASH_APPLICATION", operations)
        self.assertNotIn("FULL_ERASE", operations)
        self.assertNotIn("MASS_ERASE", operations)
        self.assertNotIn("RAW_WRITE", operations)
        self.assertNotIn(RemoteProgrammingOperation.FLASH_BOOTLOADER.value, operations)


if __name__ == "__main__":
    unittest.main()
