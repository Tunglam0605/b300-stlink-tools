from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from b300_cli.parser import parse_args
from b300_cli.remote_flash import run_remote_flash, run_remote_status
from tests.test_core_hex_policy import APPLICATION_VECTOR, write_hex


class FakeProfileStore:
    def get(self, profile_id):
        if profile_id == "gateway-1":
            return SimpleNamespace(endpoint=SimpleNamespace(host="gateway", user="aubot", port=22))
        return None


class FakeSession:
    def __init__(self, profile):
        self.profile = profile
        self.commits = 0
        self.cancels = 0
        self.closed = False
        self.statuses = iter(({"job_id": "a" * 32, "state": "RUNNING"},
                              {"job_id": "a" * 32, "state": "SUCCEEDED", "pc": 0x08010101, "bkp1r": 0}))

    def connect(self):
        pass

    def prepare_remote_application(self, path, grant, client_id):
        return {
            "job_id": "a" * 32, "state": "AWAITING_CONFIRMATION",
            "manifest": {"sha256": "a" * 64, "file_name": "application.hex"},
            "plan": {"erase_sectors": [3, 4, 5, 6, 7], "metadata_address": "0x0800C000"},
            "approval_token": "private-approval",
        }

    def commit_remote_application(self, approval, grant):
        self.commits += 1
        return {"job_id": approval["job_id"], "state": "RUNNING"}

    def cancel_remote_application(self, job_id, grant):
        self.cancels += 1
        return {"job_id": job_id, "state": "CANCELLED"}

    def remote_program_status(self, job_id):
        return next(self.statuses)

    def disconnect(self):
        self.closed = True


class FakeLease:
    def __init__(self, session, **kwargs):
        self.closed = False
        self.grant = SimpleNamespace(lease_id="lease", token="private", generation=1,
                                     public={"probe_serial": "SAFE123"})

    def start(self, mode, probe_serial=None):
        if mode != "FLASH_APPLICATION":
            raise AssertionError("wrong lease mode")
        return self.grant

    def close(self):
        self.closed = True


class CliRemoteFlashTests(unittest.TestCase):
    def test_ctrl_c_after_prepare_cancels_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            args = parse_args(["flash", str(image), "--gateway", "gateway-1",
                               "--confirm-remote-application", "--json"])
            session = FakeSession(None)
            def interrupt(_record):
                raise KeyboardInterrupt()
            code = run_remote_flash(args, profile_store=FakeProfileStore(),
                                    session_factory=lambda _: session,
                                    lease_factory=FakeLease, emit=interrupt)
            self.assertEqual(code, 130)
            self.assertEqual(session.cancels, 1)
            self.assertEqual(session.commits, 0)

    def test_status_command_queries_saved_gateway_job_id(self):
        args = parse_args(["program-status", "a" * 32, "--gateway", "gateway-1", "--json"])
        session = FakeSession(None)
        output = []
        code = run_remote_status(args, profile_store=FakeProfileStore(),
                                 session_factory=lambda _: session, emit=output.append)
        self.assertEqual(code, 0)
        self.assertEqual(output[-1]["job_id"], "a" * 32)
        self.assertEqual(output[-1]["state"], "RUNNING")
        self.assertTrue(session.closed)

    def test_ambiguous_commit_response_queries_prepared_job_id_without_retry(self):
        class AmbiguousSession(FakeSession):
            def commit_remote_application(self, approval, grant):
                self.commits += 1
                raise ConnectionError("SSH response lost after Gateway accepted commit")

        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            args = parse_args(["flash", str(image), "--gateway", "gateway-1",
                               "--confirm-remote-application", "--json"])
            session = AmbiguousSession(None)
            output = []
            code = run_remote_flash(args, profile_store=FakeProfileStore(),
                                    session_factory=lambda _: session,
                                    lease_factory=FakeLease, emit=output.append,
                                    poll_interval_seconds=0)
            self.assertEqual(code, 0)
            self.assertEqual(session.commits, 1)
            self.assertEqual(output[-1]["state"], "SUCCEEDED")

    def test_remote_dry_run_returns_gateway_plan_without_committing(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            args = parse_args(["flash", str(image), "--gateway", "gateway-1", "--dry-run", "--json"])
            session = FakeSession(None)
            output = []
            code = run_remote_flash(args, profile_store=FakeProfileStore(),
                                    session_factory=lambda _: session,
                                    lease_factory=FakeLease, emit=output.append)
            self.assertEqual(code, 0)
            self.assertEqual(session.commits, 0)
            self.assertEqual(session.cancels, 1)
            self.assertEqual(output[-1]["plan"]["erase_sectors"], [3, 4, 5, 6, 7])
            self.assertNotIn("approval_token", output[-1])
            self.assertTrue(session.closed)

    def test_explicit_confirmation_commits_one_gateway_job(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_hex(directory, 0x08010000, APPLICATION_VECTOR)
            args = parse_args(["flash", str(image), "--gateway", "gateway-1",
                               "--confirm-remote-application", "--json"])
            session = FakeSession(None)
            output = []
            code = run_remote_flash(args, profile_store=FakeProfileStore(),
                                    session_factory=lambda _: session,
                                    lease_factory=FakeLease, emit=output.append,
                                    poll_interval_seconds=0)
            self.assertEqual(code, 0)
            self.assertEqual(session.commits, 1)
            self.assertEqual(output[-1]["state"], "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
