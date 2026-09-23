from __future__ import annotations

import tempfile
import io
import json
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from contextlib import redirect_stdout

import b300_stlink
from b300_core.gateway_agent import GatewayAgent
from b300_core.gateway_agent_protocol import GatewayRequest, GatewayRequestStore
from b300_core.gateway_protocol import gateway_capabilities
from b300_cli.parser import parse_args


class FakeJobs:
    def __init__(self):
        self.upload_path = ""

    def create_upload(self, manifest, client_id, probe_serial, *, request_id=None):
        self.upload_path = "/private/jobs/abc/artifact.part"
        return {"job_id": "a" * 32, "upload_path": self.upload_path}

    def status(self, job_id):
        return {"job_id": job_id, "state": "STAGED"}


class FakeCoordinator:
    def tick(self):
        return type("Snapshot", (), {"state": "IDLE", "reason_code": "GATEWAY_IDLE", "active": False})()

    def shutdown(self, reason_code):
        return self.tick()


class GatewayProgramProtocolTests(unittest.TestCase):
    def test_same_program_request_waits_for_existing_pending_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayRequestStore(Path(directory))
            request = GatewayRequest.create(
                "program_status", {"job_id": "a" * 32}, request_id="pending-1",
            )
            store.enqueue(request)
            def finish():
                time.sleep(0.02)
                store.respond(request.request_id, {
                    "status": "ok", "reason_code": "OK",
                    "result": {"job_id": "a" * 32, "state": "STAGED"},
                }, request=request)
                store.complete(request.request_id)
            worker = threading.Thread(target=finish)
            worker.start()
            try:
                response = store.submit_request(request, timeout_seconds=0.5)
            finally:
                worker.join(timeout=1)
            self.assertEqual(response["result"]["state"], "STAGED")

    def test_same_program_request_id_and_payload_replays_without_new_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayRequestStore(Path(directory))
            agent = GatewayAgent(FakeCoordinator(), request_store=store, program_jobs=FakeJobs())
            request = GatewayRequest.create("program_status", {"job_id": "a" * 32},
                                            request_id="same-request")
            store.enqueue(request)
            agent.run_once()
            original = store.read_response(request.request_id)
            store.acknowledge_response(request.request_id)
            store.enqueue(request)
            agent.run_once()
            self.assertEqual(store.read_response(request.request_id), original)

            changed = GatewayRequest.create("program_status", {"job_id": "b" * 32},
                                            request_id="same-request")
            with self.assertRaises(FileExistsError):
                store.enqueue(changed)

    def test_slow_prepare_does_not_block_agent_heartbeat_dispatch(self):
        class SlowJobs(FakeJobs):
            def __init__(self):
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()

            def prepare(self, *args):
                self.entered.set()
                self.release.wait(2)
                return {"job_id": "a" * 32, "state": "AWAITING_CONFIRMATION"}

        class RenewCoordinator(FakeCoordinator):
            def renew(self, *args):
                return type("Lease", (), {"to_record": lambda self: {"reason_code": "LEASE_ACTIVE"}})()

        with tempfile.TemporaryDirectory() as directory:
            store = GatewayRequestStore(Path(directory))
            jobs = SlowJobs()
            agent = GatewayAgent(RenewCoordinator(), request_store=store, program_jobs=jobs)
            prepare = GatewayRequest.create("program_prepare", {
                "job_id": "a" * 32, "lease_id": "lease", "lease_token": "secret",
                "lease_generation": 1,
            })
            store.enqueue(prepare)
            try:
                started = time.monotonic()
                agent.run_once()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(jobs.entered.wait(0.5))
                renew = GatewayRequest.create("renew", {
                    "lease_id": "lease", "lease_token": "secret", "lease_generation": 1,
                })
                store.enqueue(renew)
                agent.run_once()
                self.assertEqual(store.read_response(renew.request_id)["status"], "ok")
            finally:
                jobs.release.set()
                for worker in tuple(agent._prepare_workers.values()):
                    worker.join(timeout=2)

    def test_gateway_cli_accepts_program_request_from_stdin(self):
        request = {
            "operation": "program_status", "payload": {"job_id": "a" * 32},
            "request_id": "request-1",
        }
        stdin = type("BinaryStdin", (), {"buffer": io.BytesIO(json.dumps(request).encode())})()
        store = mock.Mock()
        store.submit_request.return_value = {
            "status": "ok", "reason_code": "OK",
            "result": {"job_id": "a" * 32, "state": "STAGED"},
        }
        output = io.StringIO()
        with mock.patch.object(b300_stlink.sys, "stdin", stdin), \
                mock.patch.object(b300_stlink, "GatewayAgentProcessManager"), \
                mock.patch.object(b300_stlink, "GatewayRequestStore", return_value=store), \
                redirect_stdout(output):
            code = b300_stlink.main(["debug", "gateway-program-request", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(store.submit_request.call_args.args[0].operation, "program_status")
        self.assertEqual(json.loads(output.getvalue())["result"]["state"], "STAGED")

    def test_cli_parses_fixed_gateway_program_operations(self):
        self.assertEqual(
            parse_args(["debug", "gateway-program-request", "--json"]).debug_mode,
            "gateway-program-request",
        )

    def test_gateway_advertises_managed_application_flash_capability(self):
        self.assertIn("remote_application_flash_v1", gateway_capabilities()["capabilities"])

    def test_agent_accepts_only_validated_program_upload_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GatewayRequestStore(Path(directory))
            jobs = FakeJobs()
            agent = GatewayAgent(FakeCoordinator(), request_store=store, program_jobs=jobs)
            manifest = {
                "operation": "FLASH_APPLICATION", "firmware_kind": "APPLICATION",
                "file_name": "application.hex", "size": 32, "sha256": "a" * 64,
            }
            request = GatewayRequest.create("program_create_upload", {
                "manifest": manifest,
                "client_id": "client-1", "probe_serial": "SAFE123",
            })
            store.enqueue(request)
            agent.run_once()
            response = store.read_response(request.request_id)
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["result"]["upload_path"], jobs.upload_path)

            invalid = GatewayRequest.create("program_create_upload", {
                "manifest": {}, "client_id": "client-1", "probe_serial": "SAFE123",
                "extra": "forbidden",
            })
            store.enqueue(invalid)
            agent.run_once()
            self.assertEqual(store.read_response(invalid.request_id)["reason_code"], "REQUEST_INVALID")


if __name__ == "__main__":
    unittest.main()
