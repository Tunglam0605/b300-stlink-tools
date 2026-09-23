from __future__ import annotations

import tempfile
import hashlib
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from b300_core.gateway_program_jobs import GatewayProgramJobs, ProgramJobError
from b300_core.hex_image import inspect_image
from b300_core.models import BootVerification, CommandResult, TargetInfo
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


class GatewayProgramJobsTests(unittest.TestCase):
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
        with mock.patch.object(hashlib, "file_digest", None):
            self.assertEqual(self.jobs.finalize_upload(slot["job_id"])["state"], "STAGED")

    def test_prepare_binds_plan_and_commit_runs_once(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        self.assertEqual(approval["plan"]["erase_sectors"], [3, 4, 5, 6, 7])
        self.assertEqual(approval["manifest"]["sha256"], self.manifest.sha256)
        self.assertNotIn("approval_token", self.jobs.status(job_id))
        self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
        self.jobs.wait_active(timeout=2)
        self.assertEqual(self.jobs.status(job_id)["state"], "SUCCEEDED")
        self.assertEqual(self.service.calls, 1)
        self.assertTrue(self.coordinator.released)

    def test_file_change_after_prepare_is_rejected_before_flash(self):
        job_id = self._upload()
        approval = self._prepare(job_id)
        staged = self.jobs.staged_path(job_id)
        staged.write_bytes(b"different")
        with self.assertRaises(ProgramJobError):
            self.jobs.commit(job_id, approval["approval_token"], "lease-1", "secret", 1)
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
        self.assertEqual(fresh.status(job_id)["state"], "RECOVERY_REQUIRED")

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


if __name__ == "__main__":
    unittest.main()
