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
        self._lease = self.store.read()
        self._last_generation = self._lease.generation if self._lease is not None else 0
        self._restart_attempted_generation: Optional[int] = None

        # A process restart cannot compare monotonic deadlines from the old
        # process safely. Give the previous owner one bounded reconnect window.
        if self._lease is not None:
            now = self._clock()
            self._lease = replace(
                self._lease,
                state="GRACE",
                last_heartbeat_mono=now,
                deadline_mono=now,
                grace_deadline_mono=now + self.policy.reconnect_grace_seconds,
                reason_code="LEASE_GRACE",
            )
            self.store.write(self._lease)

    def acquire(self, request: GatewayLeaseRequest) -> AcquireResult:
        if not isinstance(request, GatewayLeaseRequest):
            raise ValueError("Gateway lease acquire requires a validated request.")
        with self._lock:
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
            return GatewayLeaseGrant.from_lease(active, token, now)

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
            return GatewayLeasePublicSnapshot.from_lease(renewed, now)

    def release(self, lease_id: str, token: str,
                generation: int) -> GatewayLeasePublicSnapshot:
        with self._lock:
            if self._owner_locked(lease_id, token, generation) is None:
                return _inactive("LEASE_INVALID")
            return self._cleanup_locked("CLIENT_RELEASED")

    def tick(self) -> GatewayLeasePublicSnapshot:
        with self._lock:
            now = self._clock()
            if self._lease is None:
                return _inactive("GATEWAY_IDLE")
            expired = self._advance_expiry_locked(now)
            if expired is not None:
                return expired
            lease = self._lease
            if lease is None:
                return _inactive("GATEWAY_IDLE")
            if lease.state == "GRACE":
                return GatewayLeasePublicSnapshot.from_lease(lease, now)
            if lease.state in {"CLEANING", "RECOVERY_REQUIRED"}:
                return GatewayLeasePublicSnapshot.from_lease(lease, now)

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
                return GatewayLeasePublicSnapshot.from_lease(refreshed, now)

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
                    return GatewayLeasePublicSnapshot.from_lease(refreshed, now)
            return self._enter_grace_locked(gateway.reason_code, now)

    def public_snapshot(self) -> GatewayLeasePublicSnapshot:
        with self._lock:
            if self._lease is None:
                return _inactive("GATEWAY_IDLE")
            return GatewayLeasePublicSnapshot.from_lease(self._lease, self._clock())

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
            return GatewayLeasePublicSnapshot.from_lease(grace, now)
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
        return GatewayLeasePublicSnapshot.from_lease(grace, now)

    def _cleanup_locked(self, final_reason: str) -> GatewayLeasePublicSnapshot:
        lease = self._lease
        if lease is None:
            return _inactive(final_reason)
        cleaning = replace(
            lease, state="CLEANING", grace_deadline_mono=None,
            reason_code="CLEANUP_IN_PROGRESS",
        )
        self._persist_locked(cleaning)
        try:
            self.supervisor.stop()
        except Exception:
            failed = replace(
                cleaning, state="RECOVERY_REQUIRED",
                reason_code="CLEANUP_IN_PROGRESS",
            )
            self._persist_locked(failed)
            return GatewayLeasePublicSnapshot.from_lease(failed, self._clock())
        self.store.clear_if_generation(cleaning.generation)
        self._lease = None
        self._restart_attempted_generation = None
        return _inactive(final_reason)

    def _persist_locked(self, lease: GatewayLease) -> None:
        self.store.write(lease)
        self._lease = lease


__all__ = ["AcquireResult", "GatewayLeaseCoordinator"]
