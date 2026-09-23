"""Client-side managed Application flash through one authenticated Gateway."""

from __future__ import annotations

import getpass
import time
import uuid
from typing import Callable, Optional

from b300_core.gateway_profiles import GatewayProfileStore
from b300_core.gateway_lease_client import GatewayLeaseClient
from b300_core.remote_session import RemoteAuthenticationError, RemoteSession


def _public_record(record: dict) -> dict:
    return {key: value for key, value in record.items()
            if key not in {"approval_token", "lease_token", "upload_path"}}


def run_remote_flash(args, *, profile_store=None,
                     session_factory=RemoteSession,
                     lease_factory=GatewayLeaseClient,
                     emit: Optional[Callable[[dict], None]] = None,
                     poll_interval_seconds: float = 0.5,
                     timeout_seconds: float = 420.0) -> int:
    """Prepare on Gateway; commit only with the explicit CLI confirmation flag."""
    if args.dry_run and args.confirm_remote_application:
        raise ValueError("Remote --dry-run and --confirm-remote-application cannot be combined.")
    store = profile_store or GatewayProfileStore()
    profile = store.default() if args.gateway == "default" else store.get(args.gateway)
    if profile is None:
        raise ValueError("Saved Gateway profile was not found: %s" % args.gateway)
    send = emit or (lambda _record: None)
    session = session_factory(profile.endpoint)
    lease = None
    committed_job_id = None
    prepared_job_id = None
    prepared_grant = None
    try:
        try:
            session.connect()
        except RemoteAuthenticationError as error:
            if error.reason_code != "SSH_PASSWORD_REQUIRED":
                raise
            session.connect(getpass.getpass("Gateway SSH password: "))
        client_id = "b300-cli-" + uuid.uuid4().hex
        lease = lease_factory(
            session, client_id=client_id,
            client_label="B300 CLI Remote Flash",
        )
        grant = lease.start("FLASH_APPLICATION", probe_serial=args.probe_serial)
        approval = session.prepare_remote_application(args.application, grant, client_id)
        prepared_job_id = approval["job_id"]
        prepared_grant = grant
        safe_plan = _public_record(approval)
        safe_plan.update(command="flash", mode="remote", status="prepared")
        send(safe_plan)
        if not args.confirm_remote_application:
            session.cancel_remote_application(approval["job_id"], grant)
            prepared_job_id = None
            return 0
        committed_job_id = approval["job_id"]
        prepared_job_id = None
        try:
            started = session.commit_remote_application(approval, grant)
        except Exception:
            # A lost SSH response is ambiguous. Query the prepared job ID;
            # never submit a second commit or start another flash.
            try:
                started = session.remote_program_status(committed_job_id)
            except Exception:
                send({"command": "flash", "mode": "remote", "status": "pending",
                      "job_id": committed_job_id, "reason_code": "COMMIT_OUTCOME_UNKNOWN",
                      "next_action": "Reconnect and query this job ID before any new flash."})
                return 2
            if started.get("state") not in {"RUNNING", "SUCCEEDED", "FAILED"}:
                send({"command": "flash", "mode": "remote", "status": "pending",
                      "job_id": committed_job_id, "reason_code": "COMMIT_OUTCOME_UNKNOWN",
                      "next_action": "Query this job ID before any new flash."})
                return 2
        send(dict(_public_record(started), command="flash", mode="remote", status="running"))
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = session.remote_program_status(committed_job_id)
            if result.get("state") in {"SUCCEEDED", "FAILED", "RECOVERY_REQUIRED", "CANCELLED"}:
                try:
                    session.cleanup_remote_application(committed_job_id)
                except Exception:
                    pass  # terminal result remains authoritative; TTL cleanup remains
                send(dict(_public_record(result), command="flash", mode="remote",
                          status="ok" if result["state"] == "SUCCEEDED" else "error"))
                return 0 if result["state"] == "SUCCEEDED" else 1
            if poll_interval_seconds:
                time.sleep(poll_interval_seconds)
        send({"command": "flash", "mode": "remote", "status": "pending",
              "job_id": committed_job_id, "reason_code": "CLIENT_WAIT_TIMEOUT",
              "next_action": "Query the same Gateway job id; do not start another flash."})
        return 2
    except KeyboardInterrupt:
        if committed_job_id is None and prepared_job_id is not None and prepared_grant is not None:
            try:
                session.cancel_remote_application(prepared_job_id, prepared_grant)
            except Exception:
                pass
        if committed_job_id is not None:
            send({"command": "flash", "mode": "remote", "status": "pending",
                  "job_id": committed_job_id, "reason_code": "CLIENT_DETACHED",
                  "next_action": "Gateway continues the job; query its id before any new flash."})
        return 130
    finally:
        if lease is not None:
            lease.close()
        session.disconnect()


def run_remote_status(args, *, profile_store=None,
                      session_factory=RemoteSession,
                      emit: Optional[Callable[[dict], None]] = None) -> int:
    store = profile_store or GatewayProfileStore()
    profile = store.default() if args.gateway == "default" else store.get(args.gateway)
    if profile is None:
        raise ValueError("Saved Gateway profile was not found: %s" % args.gateway)
    session = session_factory(profile.endpoint)
    try:
        try:
            session.connect()
        except RemoteAuthenticationError as error:
            if error.reason_code != "SSH_PASSWORD_REQUIRED":
                raise
            session.connect(getpass.getpass("Gateway SSH password: "))
        record = session.remote_program_status(args.job_id)
        public = dict(_public_record(record), command="program-status", mode="remote")
        if emit is not None:
            emit(public)
        return 1 if record.get("state") in {"FAILED", "RECOVERY_REQUIRED"} else 0
    finally:
        session.disconnect()
