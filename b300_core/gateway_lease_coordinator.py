"""Serialized exclusive ownership of one managed Gateway hardware session."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Optional, Union

from .gateway_lease import (
    SUPPORTED_LEASE_SCHEMA_VERSION,
    GatewayLease,
    GatewayLeaseBusy,
    GatewayLeaseGrant,
    GatewayLeasePolicy,
    GatewayLeasePublicSnapshot,
    GatewayLeaseRequest,
    GatewayLeaseStore,
    token_digest,
)


AcquireResult = Union[
    GatewayLeaseGrant, GatewayLeaseBusy, GatewayLeasePublicSnapshot,
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _inactive(reason_code: str) -> GatewayLeasePublicSnapshot:
    return GatewayLeasePublicSnapshot(
        active=False,
        lease_id="",
        generation=0,
        client_label="",
        mode="",
        state="IDLE",
        acquired_at="",
        heartbeat_age_seconds=0,
        gateway_instance_id="",
        gateway_generation=0,
        probe_serial=None,
        reason_code=reason_code,
    )


class GatewayLeaseCoordinator:
    """Own the lease state machine above a single ``GatewaySupervisor``.

    All state changes and supervisor lifecycle calls are serialized. The raw
    bearer token exists only in the grant and caller request; persistence keeps
    its digest. An idle coordinator never calls discovery or starts OpenOCD.
    """

    def __init__(
        self,
        supervisor: object,
        *,
        store: Optional[GatewayLeaseStore] = None,
        policy: Optional[GatewayLeasePolicy] = None,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        lease_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        acquired_at_factory: Callable[[], str] = _utc_now,
    ) -> None:
        self.supervisor = supervisor
        self.store = store or GatewayLeaseStore()
        self.policy = policy or GatewayLeasePolicy()
        self._clock = clock
        self._token_factory = token_factory
        self._lease_id_factory = lease_id_factory
        self._acquired_at_factory = acquired_at_factory
        self._lock = threading.RLock()
        try:
            self._lease = self.store.read()
            self._recovery_required = False
        except RuntimeError:
            # Corrupt persisted state must fail closed without touching hardware.
            self._lease = None
            self._recovery_required = True
        self._last_generation = self._lease.generation if self._lease is not None else 0
        self._restart_attempted_generation: Optional[int] = None
        self._gateway_endpoints = (None, None)

        # A process restart cannot compare monotonic deadlines from the old
        # process safely. Fail closed until ownership is positively reconciled.
        if self._lease is not None:
            # A restarted Agent cannot trust monotonic deadlines or a fresh
            # empty supervisor. Preserve the lease and fail closed until an
            # explicit recovery workflow positively reconciles ownership.
            self._recovery_required = True
            self._lease = replace(self._lease, state="RECOVERY_REQUIRED",
                                  reason_code="RECOVERY_REQUIRED")
            self.store.write(self._lease)

    def acquire(self, request: GatewayLeaseRequest) -> AcquireResult:
        if not isinstance(request, GatewayLeaseRequest):
            raise ValueError("Gateway lease acquire requires a validated request.")
        with self._lock:
            if self._recovery_required:
                return _inactive("RECOVERY_REQUIRED")
            now = self._clock()
            if self._lease is not None:
                self._advance_expiry_locked(now)
            if self._lease is not None:
                return GatewayLeaseBusy.from_lease(self._lease, now)

            token = self._token_factory()
            digest = token_digest(token)
            lease_id = self._lease_id_factory()
            self._last_generation += 1
            lease = GatewayLease.from_record({
                "schema_version": SUPPORTED_LEASE_SCHEMA_VERSION,
                "lease_id": lease_id,
                "token_digest": digest,
                "generation": self._last_generation,
                "request_id": request.request_id,
                "client_id": request.client_id,
                "client_label": request.client_label,
                "mode": request.mode,
                "state": "RESERVED",
                "acquired_at": self._acquired_at_factory(),
                "last_heartbeat_mono": now,
                "deadline_mono": now + self.policy.lease_ttl_seconds,
                "grace_deadline_mono": None,
                "gateway_instance_id": "pending",
                "gateway_generation": 0,
                "probe_serial": request.probe_serial,
                "reason_code": "LEASE_RESERVED",
            })
            self._lease = lease
            self.store.write(lease)
            try:
                starting = replace(lease, state="STARTING", reason_code="START_REQUESTED")
                self._persist_locked(starting)
                gateway = self.supervisor.ensure()
            except Exception:
                return self._cleanup_locked("GATEWAY_START_FAILED")
            if not gateway.attach_ready:
                return self._cleanup_locked(gateway.reason_code)

            active = replace(
                self._lease,
                state="ACTIVE",
                gateway_instance_id=gateway.instance_id,
                gateway_generation=gateway.generation,
                reason_code="LEASE_ACTIVE",
            )
            self._restart_attempted_generation = None
            self._persist_locked(active)
            self._gateway_endpoints = (getattr(gateway, "gdb_endpoint", None),
                                       getattr(gateway, "tcl_endpoint", None))
            public = self._public_locked(active, now)
            return GatewayLeaseGrant(active.lease_id, token, active.generation, public)

    def renew(self, lease_id: str, token: str,
              generation: int) -> GatewayLeasePublicSnapshot:
        with self._lock:
            now = self._clock()
            if self._lease is not None:
                self._advance_expiry_locked(now)
            lease = self._owner_locked(lease_id, token, generation)
            if lease is None or lease.state in {"CLEANING", "RECOVERY_REQUIRED"}:
                return _inactive("LEASE_INVALID")
            renewed = replace(
                lease,
                state="ACTIVE",
                last_heartbeat_mono=now,
                deadline_mono=now + self.policy.lease_ttl_seconds,
                grace_deadline_mono=None,
                reason_code="LEASE_ACTIVE",
            )
            self._persist_locked(renewed)
            return self._public_locked(renewed, now)

    def release(self, lease_id: str, token: str,
                generation: int) -> GatewayLeasePublicSnapshot:
        with self._lock:
            if self._owner_locked(lease_id, token, generation) is None:
                return _inactive("LEASE_INVALID")
            return self._cleanup_locked("CLIENT_RELEASED")

    def tick(self) -> GatewayLeasePublicSnapshot:
        with self._lock:
            now = self._clock()
            if self._recovery_required:
                return self._reconcile_recovery_locked(now)
            if self._lease is None:
                return _inactive("GATEWAY_IDLE")
            expired = self._advance_expiry_locked(now)
            if expired is not None:
                return expired
            lease = self._lease
            if lease is None:
                return _inactive("GATEWAY_IDLE")
            if lease.state == "GRACE":
                return self._public_locked(lease, now)
            if lease.state in {"CLEANING", "RECOVERY_REQUIRED"}:
                return self._public_locked(lease, now)

            gateway = self.supervisor.maintain_once()
            if gateway.attach_ready:
                refreshed = replace(
                    lease,
                    state="ACTIVE",
                    gateway_instance_id=gateway.instance_id,
                    gateway_generation=gateway.generation,
                    reason_code="LEASE_ACTIVE",
                )
                self._persist_locked(refreshed)
                self._gateway_endpoints = (getattr(gateway, "gdb_endpoint", None),
                                           getattr(gateway, "tcl_endpoint", None))
                return self._public_locked(refreshed, now)

            if (gateway.reason_code == "OPENOCD_EXITED"
                    and self._restart_attempted_generation != lease.generation):
                self._restart_attempted_generation = lease.generation
                try:
                    self.supervisor.stop()
                    recovered = self.supervisor.ensure()
                except Exception:
                    recovered = None
                if recovered is not None and recovered.attach_ready:
                    refreshed = replace(
                        lease,
                        state="ACTIVE",
                        gateway_instance_id=recovered.instance_id,
                        gateway_generation=recovered.generation,
                        reason_code="LEASE_ACTIVE",
                    )
                    self._persist_locked(refreshed)
                    self._gateway_endpoints = (getattr(recovered, "gdb_endpoint", None),
                                               getattr(recovered, "tcl_endpoint", None))
                    return self._public_locked(refreshed, now)
            return self._enter_grace_locked(gateway.reason_code, now)

    def public_snapshot(self) -> GatewayLeasePublicSnapshot:
        with self._lock:
            if self._recovery_required:
                if self._lease is not None:
                    return self._public_locked(self._lease, self._clock())
                return _inactive("RECOVERY_REQUIRED")
            if self._lease is None:
                return _inactive("GATEWAY_IDLE")
            return self._public_locked(self._lease, self._clock())

    def shutdown(self, reason_code: str = "AGENT_SHUTDOWN") -> GatewayLeasePublicSnapshot:
        with self._lock:
            if self._lease is None:
                return _inactive("GATEWAY_IDLE")
            return self._cleanup_locked(reason_code)

    def _owner_locked(self, lease_id: object, token: object,
                      generation: object) -> Optional[GatewayLease]:
        lease = self._lease
        if lease is None or not isinstance(lease_id, str) or not isinstance(token, str):
            return None
        if not isinstance(generation, int) or isinstance(generation, bool):
            return None
        try:
            digest = token_digest(token)
        except ValueError:
            return None
        if (lease.lease_id != lease_id or lease.generation != generation
                or not hmac.compare_digest(lease.token_digest, digest)):
            return None
        return lease

    def _advance_expiry_locked(
        self, now: float,
    ) -> Optional[GatewayLeasePublicSnapshot]:
        lease = self._lease
        if lease is None:
            return _inactive("GATEWAY_IDLE")
        if lease.state == "GRACE":
            if lease.grace_deadline_mono is not None and now >= lease.grace_deadline_mono:
                return self._cleanup_locked("LEASE_EXPIRED")
            return None
        if lease.state in {"RESERVED", "STARTING", "ACTIVE"} and now >= lease.deadline_mono:
            grace_deadline = lease.deadline_mono + self.policy.reconnect_grace_seconds
            if now >= grace_deadline:
                return self._cleanup_locked("LEASE_EXPIRED")
            grace = replace(
                lease,
                state="GRACE",
                grace_deadline_mono=grace_deadline,
                reason_code="LEASE_GRACE",
            )
            self._persist_locked(grace)
            return self._public_locked(grace, now)
        return None

    def _enter_grace_locked(self, reason_code: str,
                            now: float) -> GatewayLeasePublicSnapshot:
        lease = self._lease
        if lease is None:
            return _inactive("GATEWAY_IDLE")
        grace = replace(
            lease,
            state="GRACE",
            deadline_mono=min(lease.deadline_mono, now),
            grace_deadline_mono=now + self.policy.reconnect_grace_seconds,
            reason_code=reason_code,
        )
        self._persist_locked(grace)
        return self._public_locked(grace, now)

    def _cleanup_locked(self, final_reason: str) -> GatewayLeasePublicSnapshot:
        lease = self._lease
        if lease is None:
            return _inactive(final_reason)
        cleaning = replace(
            lease, state="CLEANING", grace_deadline_mono=None,
            reason_code="CLEANUP_IN_PROGRESS",
        )
        self._persist_locked(cleaning)
        error = []
        def stop_gateway():
            try:
                self.supervisor.stop()
            except Exception as exc:
                error.append(exc)
        worker = threading.Thread(target=stop_gateway, name="b300-gateway-cleanup", daemon=True)
        worker.start()
        worker.join(timeout=self.policy.cleanup_timeout_seconds)
        if worker.is_alive() or error:
            failed = replace(
                cleaning, state="RECOVERY_REQUIRED",
                reason_code="CLEANUP_IN_PROGRESS",
            )
            self._persist_locked(failed)
            return self._public_locked(failed, self._clock())
        self.store.clear_if_generation(cleaning.generation)
        self._lease = None
        self._recovery_required = False
        self._gateway_endpoints = (None, None)
        self._restart_attempted_generation = None
        return _inactive(final_reason)

    def _reconcile_recovery_locked(self, now: float) -> GatewayLeasePublicSnapshot:
        """Clear a restarted lease only after its former B300 owner is proven.

        A new Agent has no authority over a process merely because it owns the
        persisted lease file.  The supervisor must positively identify its own
        live Gateway before cleanup is requested; missing or slow evidence is
        retained as a fail-closed, operator-visible recovery state.
        """
        lease = self._lease
        if lease is None:
            return _inactive("RECOVERY_REQUIRED")
        checker = getattr(self.supervisor, "reconcile_lease_owner", None)
        if not callable(checker):
            return self._mark_recovery_locked(lease, "RECOVERY_OWNER_UNPROVEN", now)

        outcome = []
        error = []

        def check_owner() -> None:
            try:
                outcome.append(checker(lease) is True)
            except Exception as exc:
                error.append(exc)

        worker = threading.Thread(
            target=check_owner, name="b300-gateway-recovery-check", daemon=True,
        )
        worker.start()
        worker.join(timeout=self.policy.cleanup_timeout_seconds)
        if worker.is_alive():
            return self._mark_recovery_locked(lease, "RECOVERY_RECONCILE_TIMEOUT", now)
        if error or not outcome or not outcome[0]:
            return self._mark_recovery_locked(lease, "RECOVERY_OWNER_UNPROVEN", now)
        return self._cleanup_locked("RECOVERY_RECONCILED")

    def _mark_recovery_locked(self, lease: GatewayLease, reason_code: str,
                              now: float) -> GatewayLeasePublicSnapshot:
        recovered = replace(lease, state="RECOVERY_REQUIRED", reason_code=reason_code)
        self._persist_locked(recovered)
        return self._public_locked(recovered, now)

    def _persist_locked(self, lease: GatewayLease) -> None:
        self.store.write(lease)
        self._lease = lease

    def _public_locked(self, lease: GatewayLease, now: float) -> GatewayLeasePublicSnapshot:
        return replace(
            GatewayLeasePublicSnapshot.from_lease(lease, now),
            gdb_endpoint=self._gateway_endpoints[0],
            tcl_endpoint=self._gateway_endpoints[1],
        )


__all__ = ["AcquireResult", "GatewayLeaseCoordinator"]
