from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from b300_core.gateway_program_jobs import GatewayProgramJobs, ProgramJobError
from b300_core.remote_programming import (
    FirmwareKind, GatewayProgrammingService, RemoteFirmwareManifest,
    RemoteProgrammingOperation,
)
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex
from tests.test_gateway_program_jobs import FakeCoordinator, FakeService


class IsolatedStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "ingress").mkdir(mode=0o710)
        self.image = write_hex(self.temp.name, 0x08010000, APPLICATION_VECTOR)
        self.original = self.image.read_bytes()
        self.manifest = RemoteFirmwareManifest.from_file(
            self.image, operation=RemoteProgrammingOperation.FLASH_APPLICATION,
            firmware_kind=FirmwareKind.APPLICATION,
        )
        self.service = FakeService()
        self.jobs = GatewayProgramJobs(
            self.root / "private", FakeCoordinator(),
            ingress_root=self.root / "ingress",
            programming=GatewayProgrammingService(service=self.service),
        )

    def slot(self):
        return self.jobs.create_upload(self.manifest, "client-1", "SAFE123")

    def test_finalized_artifact_is_private_and_survives_ingress_rewrite(self):
        slot = self.slot()
        upload = Path(slot["upload_path"])
        self.assertEqual(upload.parts[-3:], ("program-jobs", slot["job_id"], "artifact.part"))
        self.assertEqual(upload.read_bytes(), b"")
        upload.write_bytes(self.original)
        public = self.jobs.finalize_upload(slot["job_id"])
        private = self.jobs.staged_path(slot["job_id"])
        self.assertNotEqual(private.parent, upload.parent)
        self.assertEqual(private.read_bytes(), self.original)
        self.assertNotIn(str(private), str(slot))
        self.assertNotIn(str(private), str(public))
        upload.write_bytes(b"changed")
        approval = self.jobs.prepare(slot["job_id"], "lease-1", "secret", 1)
        self.assertEqual(private.read_bytes(), self.original)
        self.assertEqual(approval["state"], "AWAITING_CONFIRMATION")
        upload.unlink()
        self.jobs.commit(slot["job_id"], approval["approval_token"], "lease-1", "secret", 1)
        self.jobs.wait_active(timeout=3)
        self.assertEqual(self.jobs.status(slot["job_id"])["state"], "SUCCEEDED")
        self.assertEqual(self.service.calls, 1)

    def test_unsafe_ingress_fails_before_private_promotion(self):
        for kind in ("symlink", "hardlink", "oversized", "mismatch"):
            with self.subTest(kind=kind):
                slot = self.slot()
                upload = Path(slot["upload_path"])
                if kind == "symlink":
                    upload.unlink()
                    try:
                        upload.symlink_to(self.image)
                    except (OSError, NotImplementedError):
                        self.skipTest("Symlink creation unavailable")
                elif kind == "hardlink":
                    upload.unlink()
                    os.link(self.image, upload)
                elif kind == "oversized":
                    with upload.open("wb") as stream:
                        stream.truncate(32 * 1024 * 1024 + 1)
                else:
                    upload.write_bytes(self.original[:-1])
                with self.assertRaises(ProgramJobError):
                    self.jobs.finalize_upload(slot["job_id"])
                self.assertEqual(self.jobs.status(slot["job_id"])["state"], "UPLOADING")
                self.assertTrue(self.jobs.status(slot["job_id"])["reason_code"])
                self.assertFalse(self.jobs.staged_path(slot["job_id"]).exists())

    def test_private_artifact_is_never_replaced_by_finalize(self):
        slot = self.slot()
        Path(slot["upload_path"]).write_bytes(self.original)
        private = self.jobs.staged_path(slot["job_id"])
        private.write_bytes(b"prior artifact")
        with self.assertRaises(ProgramJobError):
            self.jobs.finalize_upload(slot["job_id"])
        self.assertEqual(private.read_bytes(), b"prior artifact")
        self.assertEqual(self.jobs.status(slot["job_id"])["state"], "UPLOADING")

    def test_cancel_cleanup_removes_ingress_slot(self):
        slot = self.slot()
        upload = Path(slot["upload_path"])
        upload.write_bytes(self.original)
        self.jobs.cancel(slot["job_id"], "lease-1", "secret", 1)
        self.jobs.cleanup(slot["job_id"])
        self.assertFalse(upload.exists())
        self.assertFalse(upload.parent.exists())

    def test_ingress_change_during_copy_does_not_promote_private_artifact(self):
        slot = self.slot()
        upload = Path(slot["upload_path"])
        upload.write_bytes(self.original)
        real_fstat = os.fstat
        source_checks = 0

        def mutate_on_post_copy(fd):
            nonlocal source_checks
            info = real_fstat(fd)
            if info.st_ino == upload.stat().st_ino and info.st_dev == upload.stat().st_dev:
                source_checks += 1
                if source_checks == 2:
                    upload.write_bytes(b"X" + self.original[1:])
                    info = real_fstat(fd)
            return info

        with mock.patch("b300_core.gateway_program_jobs.os.fstat", side_effect=mutate_on_post_copy):
            with self.assertRaises(ProgramJobError) as captured:
                self.jobs.finalize_upload(slot["job_id"])
        self.assertEqual(captured.exception.reason_code, "STAGING_UNSAFE")
        self.assertFalse(self.jobs.staged_path(slot["job_id"]).exists())
        self.assertEqual(list(self.jobs.staged_path(slot["job_id"]).parent.glob("artifact-*.tmp")), [])

    @unittest.skipUnless(os.name == "posix", "POSIX ownership and mode")
    def test_ingress_slot_has_upload_group_permissions(self):
        slot = self.slot()
        directory = Path(slot["upload_path"]).parent
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o710)
        self.assertEqual(stat.S_IMODE(Path(slot["upload_path"]).stat().st_mode), 0o660)
        self.assertEqual(directory.stat().st_uid, os.getuid())
        self.assertEqual(directory.stat().st_gid, self.jobs.ingress_root.stat().st_gid)


if __name__ == "__main__":
    unittest.main()
