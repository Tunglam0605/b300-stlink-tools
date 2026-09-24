from __future__ import annotations

import tempfile
import hashlib
import os
import stat
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.gateway_program_jobs import GatewayProgramJobs, ProgramJobError
from b300_core.gateway_lease import GatewayLeaseRequest, GatewayLeaseStore
from b300_core.gateway_lease_coordinator import GatewayLeaseCoordinator
from b300_core.hardware_owner import FileHardwareOwner
from b300_core.hex_image import inspect_image
from b300_core.models import BootVerification, CommandResult, ProbeInfo, TargetInfo
from b300_core.policy import build_flash_plan
from b300_core.remote_programming import (
    FirmwareKind, GatewayProgrammingService, RemoteFirmwareManifest,
    RemoteProgrammingOperation,
)
from b300_core.service import FlashResult
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex


class FakeCoordinator:
    def __init__(self):
        self.owned = True
        self.released = False

    def owns_flash_lease(self, lease_id, token, generation):
        return self.owned and (lease_id, token, generation) == ("lease-1", "secret", 1)

    def renew(self, lease_id, token, generation):
        return SimpleNamespace(active=self.owns_flash_lease(lease_id, token, generation))

    def public_snapshot(self):
        return SimpleNamespace(probe_serial="SAFE123")

    def release(self, lease_id, token, generation):
        self.released = True


class FakeService:
    def __init__(self):
        self.calls = 0

    def inspect_image(self, path):
        return inspect_image(path)

    def inspect_target(self, probe, event_sink=None):
        return TargetInfo(0x413, 512, 3.1, "protected", (0, 1, 2), True)

    def plan(self, image, probe, target):
        return build_flash_plan(image, probe, target)

    def flash(self, plan, event_sink=None, phase_sink=None, cancel_event=None):
        self.calls += 1
        if event_sink:
            event_sink("** Verified OK **")
        return FlashResult(
            "succeeded", CommandResult(("openocd",), 0, "** Verified OK **"),
            CommandResult(("reset",), 0, "reset run"),
            CommandResult(("verify",), 0, "ok"),
            BootVerification(0x08010101, 0, True, ""),
            verified_metadata_bytes=b"\x00" * 44,
            confirmed_metadata=SimpleNamespace(valid=True, state_name="CONFIRMED", sequence=2),
        )


class BlockingFlashService(FakeService):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.continue_flash = threading.Event()

    def flash(self, plan, event_sink=None, phase_sink=None, cancel_event=None):
        self.started.set()
        if not self.continue_flash.wait(3):
            raise TimeoutError("test did not release blocked flash")
        return super().flash(plan, event_sink=event_sink,
                             phase_sink=phase_sink, cancel_event=cancel_event)


class GatewayProgramJobsTests(unittest.TestCase):
    def test_client_release_during_committed_job_does_not_abort_or_duplicate_flash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            manifest = RemoteFirmwareManifest.from_file(
                image, operation=RemoteProgrammingOperation.FLASH_APPLICATION,
                firmware_kind=FirmwareKind.APPLICATION,
            )
            service = BlockingFlashService()
            coordinator = GatewayLeaseCoordinator(
                object(), store=GatewayLeaseStore(root / "lease.json"),
                hardware_owner=FileHardwareOwner(root / "owner.lock"),
                probe_discovery=lambda: (ProbeInfo("SAFE123", "ST-Link", "test", "usb:1"),),
            )
            lease = coordinator.acquire(GatewayLeaseRequest(
                request_id="request-1", client_id="client-1",
                client_label="Client 1", mode="FLASH_APPLICATION",
                probe_serial="SAFE123",
            ))
            jobs = GatewayProgramJobs(root / "jobs", coordinator,
                                      programming=GatewayProgrammingService(service=service))
            slot = jobs.create_upload(manifest, "client-1", "SAFE123")
            Path(slot["upload_path"]).write_bytes(image.read_bytes())
            jobs.finalize_upload(slot["job_id"])
            approval = jobs.prepare(slot["job_id"], lease.lease_id, lease.token, lease.generation)
            try:
                jobs.commit(slot["job_id"], approval["approval_token"],
                            lease.lease_id, lease.token, lease.generation)
                self.assertTrue(service.started.wait(1))
                self.assertTrue(coordinator.release(
                    lease.lease_id, lease.token, lease.generation,
                ).active)
                self.assertEqual(jobs.status(slot["job_id"])["state"], "RUNNING")
            finally:
                service.continue_flash.set()
            jobs.wait_active(timeout=3)
            self.assertEqual(jobs.status(slot["job_id"])["state"], "SUCCEEDED")
            self.assertEqual(service.calls, 1)
            self.assertFalse(coordinator.public_snapshot().active)
            FileHardwareOwner(root / "owner.lock").acquire().release()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = write_hex(self.temp.name, 0x08010000, APPLICATION_VECTOR)
        self.manifest = RemoteFirmwareManifest.from_file(
            self.path, operation=RemoteProgrammingOperation.FLASH_APPLICATION,
            firmware_kind=FirmwareKind.APPLICATION,
        )
        self.service = FakeService()
        self.coordinator = FakeCoordinator()
        self.jobs = GatewayProgramJobs(
            Path(self.temp.name) / "jobs", self.coordinator,
            programming=GatewayProgrammingService(service=self.service),
        )

    def _upload(self):
        slot = self.jobs.create_upload(self.manifest, "client-1", "SAFE123")
        Path(slot["upload_path"]).write_bytes(self.path.read_bytes())
        self.jobs.finalize_upload(slot["job_id"])
        return slot["job_id"]

    def _prepare(self, job_id):
        return self.jobs.prepare(job_id, "lease-1", "secret", 1)

    def test_upload_is_content_checked_before_prepare(self):
        slot = self.jobs.create_upload(self.manifest, "client-1", "SAFE123")
        Path(slot["upload_path"]).write_bytes(b"tampered")
        with self.assertRaises(ProgramJobError):
            self.jobs.finalize_upload(slot["job_id"])
        self.assertEqual(self.jobs.status(slot["job_id"])["state"], "UPLOADING")

    def test_symlink_upload_is_rejected_before_finalization(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "outside.hex"
            outside.write_bytes(self.path.read_bytes())
            slot = self.jobs.create_upload(self.manifest, "client-1", "SAFE123")
            try:
                Path(slot["upload_path"]).symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation unavailable on this host")
            with self.assertRaises(ProgramJobError) as captured:
                self.jobs.finalize_upload(slot["job_id"])
            self.assertEqual(captured.exception.reason_code, "STAGING_UNSAFE")
            self.assertEqual(outside.read_bytes(), self.path.read_bytes())

    def test_upload_hashing_works_without_python_311_file_digest(self):
        slot = self.jobs.create_upload(self.manifest, "client-1", "SAFE123")
        Path(slot["upload_path"]).write_bytes(self.path.read_bytes())
        with mock.patch.object(hashlib, "file_digest", None, create=True):
            self.assertEqual(self.jobs.finalize_upload(slot["job_id"])["state"], "STAGED")

    def test_prepare_binds_plan_and_commit_runs_once(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        self.assertEqual(approval["plan"]["erase_sectors"], [3, 4, 5, 6, 7])
        self.assertEqual(approval["plan"]["device_id"] & 0xFFF, 0x413)
        self.assertEqual(approval["plan"]["flash_kib"], 512)
        self.assertTrue(approval["plan"]["protection_reported"])
        self.assertFalse(approval["plan"]["readout_protected"])
        self.assertEqual(approval["plan"]["protected_sectors"], [0, 1, 2])
        self.assertEqual(approval["plan"]["transaction"], [
            "flash erase_sector 0 3 7",
            "flash write_image {application.hex}",
            "verify_image {application.hex}",
            "metadata_plan: 0x0800C000 / 44 bytes / STLM + VERIFIED",
            "reset run",
        ])
        self.assertEqual(approval["manifest"]["sha256"], self.manifest.sha256)
        self.assertNotIn("approval_token", self.jobs.status(job_id))
        self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.jobs.wait_active(timeout=2)
        self.assertEqual(self.jobs.status(job_id)["state"], "SUCCEEDED")
        self.assertEqual(self.service.calls, 1)
        self.assertTrue(self.coordinator.released)

    def test_replayed_prepare_returns_same_approval_without_reinspecting(self):
        job_id = self._upload()
        first = self._prepare(job_id)
        second = self._prepare(job_id)
        self.assertEqual(second["approval_token"], first["approval_token"])
        self.assertEqual(second["plan"], first["plan"])

    def test_replayed_finalize_and_commit_never_program_twice(self):
        job_id = self._upload()
        self.assertEqual(self.jobs.finalize_upload(job_id)["state"], "STAGED")
        approval = self._prepare(job_id)
        self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.jobs.wait_active(timeout=2)
        repeated = self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.assertEqual(repeated["state"], "SUCCEEDED")
        self.assertEqual(self.service.calls, 1)

    def test_terminal_cleanup_removes_staged_hex_but_keeps_result(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.jobs.wait_active(timeout=2)
        self.assertTrue(self.jobs.staged_path(job_id).exists())
        self.jobs.cleanup(job_id)
        self.assertFalse(self.jobs.staged_path(job_id).exists())
        self.assertEqual(self.jobs.status(job_id)["state"], "SUCCEEDED")

    def test_staged_artifact_and_flash_log_get_private_permissions(self):
        slot = self.jobs.create_upload(self.manifest, "client-1", "SAFE123")
        Path(slot["upload_path"]).write_bytes(self.path.read_bytes())
        with mock.patch("b300_core.gateway_program_jobs.os.chmod", wraps=os.chmod) as chmod:
            self.jobs.finalize_upload(slot["job_id"])
            approval = self._prepare(slot["job_id"])
            self.jobs.commit(slot["job_id"], approval["approval_token"],
                             "lease-1", "secret", 1)
            self.jobs.wait_active(timeout=2)
        staged = self.jobs.staged_path(slot["job_id"])
        log = self.jobs.root / slot["job_id"] / "flash.log"
        paths = {(Path(call.args[0]), call.args[1]) for call in chmod.call_args_list}
        self.assertIn((staged, 0o600), paths)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        else:
            self.assertIn((log, 0o600), paths)

    def test_hardlinked_log_is_rejected_before_flash(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        outside = Path(self.temp.name) / "outside.log"
        outside.write_bytes(b"untouched")
        log = self.jobs.root / job_id / "flash.log"
        os.link(outside, log)
        with self.assertRaises(ProgramJobError) as captured:
            self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.assertEqual(captured.exception.reason_code, "STAGING_UNSAFE")
        self.assertEqual(outside.read_bytes(), b"untouched")
        self.assertEqual(self.service.calls, 0)

    def test_symlink_log_is_rejected_before_flash(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        outside = Path(self.temp.name) / "outside-symlink.log"
        outside.write_bytes(b"untouched")
        log = self.jobs.root / job_id / "flash.log"
        try:
            log.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable on this host")
        with self.assertRaises(ProgramJobError) as captured:
            self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.assertEqual(captured.exception.reason_code, "STAGING_UNSAFE")
        self.assertEqual(outside.read_bytes(), b"untouched")
        self.assertEqual(self.service.calls, 0)

    def test_file_change_after_prepare_is_rejected_before_flash(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        staged = self.jobs.staged_path(job_id)
        staged.write_bytes(b"different")
        with self.assertRaises(ProgramJobError):
            self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.assertEqual(self.service.calls, 0)

    def test_symlink_replacement_after_finalize_is_rejected_before_prepare(self):
        job_id = self._upload()
        staged = self.jobs.staged_path(job_id)
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / staged.name
            outside.write_bytes(staged.read_bytes())
            staged.unlink()
            try:
                staged.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation unavailable on this host")
            with mock.patch.object(self.service, "inspect_image", wraps=self.service.inspect_image) as inspect:
                with self.assertRaises(ProgramJobError) as captured:
                    self._prepare(job_id)
            self.assertEqual(captured.exception.reason_code, "STAGING_UNSAFE")
            inspect.assert_not_called()
            self.assertEqual(self.service.calls, 0)

    def test_symlink_replacement_after_prepare_is_rejected_before_commit(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        staged = self.jobs.staged_path(job_id)
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / staged.name
            outside.write_bytes(staged.read_bytes())
            staged.unlink()
            try:
                staged.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation unavailable on this host")
            with self.assertRaises(ProgramJobError) as captured:
                self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
            self.assertEqual(captured.exception.reason_code, "STAGING_UNSAFE")
            self.assertEqual(self.service.calls, 0)

    def test_hardlink_replacement_after_finalize_is_rejected_before_prepare(self):
        job_id = self._upload()
        staged = self.jobs.staged_path(job_id)
        outside = Path(self.temp.name) / staged.name
        outside.write_bytes(staged.read_bytes())
        staged.unlink()
        os.link(outside, staged)
        with mock.patch.object(self.service, "inspect_image", wraps=self.service.inspect_image) as inspect:
            with self.assertRaises(ProgramJobError) as captured:
                self._prepare(job_id)
        self.assertEqual(captured.exception.reason_code, "STAGING_UNSAFE")
        inspect.assert_not_called()
        self.assertEqual(self.service.calls, 0)

    def test_hardlink_replacement_after_prepare_is_rejected_before_commit(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        staged = self.jobs.staged_path(job_id)
        outside = Path(self.temp.name) / staged.name
        outside.write_bytes(staged.read_bytes())
        staged.unlink()
        os.link(outside, staged)
        try:
            with self.assertRaises(ProgramJobError) as captured:
                self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        finally:
            self.jobs.wait_active(timeout=2)
        self.assertEqual(captured.exception.reason_code, "STAGING_UNSAFE")
        self.assertEqual(self.service.calls, 0)

    def test_worker_rechecks_private_staging_before_flash(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        original = self.jobs._verified_staged_path
        checks = []

        def reject_after_commit(selected_job_id):
            checks.append(selected_job_id)
            if len(checks) == 2:
                raise ProgramJobError("STAGING_UNSAFE", "Staging root was replaced.")
            return original(selected_job_id)

        with mock.patch.object(self.jobs, "_verified_staged_path", side_effect=reject_after_commit):
            self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
            self.jobs.wait_active(timeout=2)
        self.assertEqual(checks, [job_id, job_id])
        self.assertEqual(self.jobs.status(job_id)["state"], "FAILED")
        self.assertEqual(self.service.calls, 0)

    def test_wrong_lease_cannot_prepare_or_commit(self):
        job_id = self._upload()
        with self.assertRaises(ProgramJobError):
            self.jobs.prepare(job_id, "lease-1", "wrong", 1)
        approval = self._prepare(job_id)
        with self.assertRaises(ProgramJobError):
            self.jobs.commit(job_id, approval["approval_token"], "lease-1", "wrong", 1)

    def test_expired_approval_cannot_commit(self):
        job_id = self._upload()
        with mock.patch("b300_core.gateway_program_jobs.APPROVAL_TTL_SECONDS", 0):
            approval = self._prepare(job_id)
        with self.assertRaises(ProgramJobError) as captured:
            self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.assertEqual(captured.exception.reason_code, "APPROVAL_MISMATCH")
        self.assertEqual(self.service.calls, 0)

    def test_unpinned_upload_uses_exact_physical_probe_from_flash_lease(self):
        slot = self.jobs.create_upload(self.manifest, "client-1", None)
        Path(slot["upload_path"]).write_bytes(self.path.read_bytes())
        self.jobs.finalize_upload(slot["job_id"])
        approval = self._prepare(slot["job_id"])
        self.assertEqual(approval["plan"]["probe_serial"], "SAFE123")

    def test_relative_staging_root_can_commit_approved_job(self):
        relative_root = Path(os.path.relpath(Path(self.temp.name) / "relative-jobs"))
        jobs = GatewayProgramJobs(
            relative_root, self.coordinator,
            programming=GatewayProgrammingService(service=self.service),
        )
        slot = jobs.create_upload(self.manifest, "client-1", "SAFE123")
        Path(slot["upload_path"]).write_bytes(self.path.read_bytes())
        jobs.finalize_upload(slot["job_id"])
        approval = jobs.prepare(slot["job_id"], "lease-1", "secret", 1)
        jobs.commit(slot["job_id"], approval["approval_token"], "lease-1", "secret", 1)
        jobs.wait_active(timeout=2)
        self.assertEqual(jobs.status(slot["job_id"])["state"], "SUCCEEDED")

    def test_dry_run_cancel_releases_slot_without_starting_flash(self):
        job_id = self._upload()
        self._prepare(job_id)
        result = self.jobs.cancel(job_id, "lease-1", "secret", 1)
        self.assertEqual(result["state"], "CANCELLED")
        self.assertEqual(self.service.calls, 0)
        with self.assertRaises(ProgramJobError):
            self.jobs.prepare(job_id, "lease-1", "secret", 1)

    def test_prepared_job_becomes_recovery_required_after_agent_restart(self):
        job_id = self._upload()
        self._prepare(job_id)
        fresh = GatewayProgramJobs(
            self.jobs.root, self.coordinator,
            programming=GatewayProgrammingService(service=self.service),
        )
        recovered = fresh.status(job_id)
        self.assertEqual(recovered["state"], "RECOVERY_REQUIRED")
        self.assertTrue(recovered["reason"])
        self.assertTrue(recovered["next_action"])

    def test_abandoned_upload_is_pruned_before_slot_quota(self):
        old = self.jobs.create_upload(self.manifest, "client-1", "SAFE123")
        record = self.jobs._read(old["job_id"])
        record["created_at"] = time.time() - 7200
        self.jobs._write(old["job_id"], record)
        self.jobs.create_upload(self.manifest, "client-1", "SAFE123")
        self.assertFalse((self.jobs.root / old["job_id"]).exists())

    def test_total_staging_byte_quota_rejects_second_upload(self):
        self.jobs.create_upload(self.manifest, "client-1", "SAFE123")
        with mock.patch("b300_core.gateway_program_jobs.MAX_STAGING_BYTES", self.manifest.size * 2 - 1):
            with self.assertRaises(ProgramJobError) as captured:
                self.jobs.create_upload(self.manifest, "client-2", "SAFE123")
        self.assertEqual(captured.exception.reason_code, "STAGING_QUOTA_EXCEEDED")

    def test_replayed_create_upload_returns_same_slot_only_for_same_manifest(self):
        first = self.jobs.create_upload(
            self.manifest, "client-1", "SAFE123", request_id="request-1",
        )
        second = self.jobs.create_upload(
            self.manifest, "client-1", "SAFE123", request_id="request-1",
        )
        self.assertEqual(first["job_id"], second["job_id"])
        with self.assertRaises(ProgramJobError):
            self.jobs.create_upload(
                self.manifest, "client-2", "SAFE123", request_id="request-1",
            )


if __name__ == "__main__":
    unittest.main()
