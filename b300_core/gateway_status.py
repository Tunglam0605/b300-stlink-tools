"""Validated Gateway health snapshots and client-side freshness tracking."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional


SUPPORTED_SCHEMA_VERSION = 1
MAX_READY_EVIDENCE_AGE_MS = 5000
GATEWAY_STATES = frozenset({
    "STOPPED", "WAITING_PROBE", "WAITING_SELECTION", "STARTING",
    "READY", "DISCONNECTED", "FAILED",
})
CPU_STATES = frozenset({"running", "halted", "reset", "unknown"})


def _loopback_endpoint(value: object, label: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    host, separator, port_text = text.rpartition(":")
    if not separator:
        raise ValueError("%s must use HOST:PORT format." % label)
    try:
        address = ipaddress.ip_address(host)
        port = int(port_text)
    except ValueError as error:
        raise ValueError("%s is invalid." % label) from error
    if not address.is_loopback or not 1 <= port <= 65535:
        raise ValueError("%s must be a loopback endpoint." % label)
    return "%s:%d" % (address, port)


@dataclass(frozen=True)
class GatewaySnapshot:
    schema_version: int
    instance_id: str
    generation: int
    sequence: int
    state: str
    reason_code: str
    selected_probe: Optional[Mapping[str, object]]
    gdb_endpoint: Optional[str]
    tcl_endpoint: Optional[str]
    cpu_state: str
    evidence_age_ms: Optional[int]
    gdb_connection_count: Optional[int] = None
    gdb_activity_generation: Optional[int] = None
    gdb_ever_attached: Optional[bool] = None

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "GatewaySnapshot":
        if not isinstance(record, Mapping):
            raise ValueError("Gateway snapshot must be a JSON object.")
        schema_version = int(record.get("schema_version", -1))
        if schema_version != SUPPORTED_SCHEMA_VERSION:
            raise ValueError("Unsupported Gateway protocol schema version: %s." % schema_version)
        instance_id = str(record.get("instance_id") or "").strip()
        reason_code = str(record.get("reason_code") or "").strip()
        if not instance_id or not reason_code:
            raise ValueError("Gateway snapshot requires instance_id and reason_code.")
        generation = int(record.get("generation", -1))
        sequence = int(record.get("sequence", -1))
        if generation < 0 or sequence < 0:
            raise ValueError("Gateway generation and sequence must be non-negative.")
        state = str(record.get("state") or "").upper()
        if state not in GATEWAY_STATES:
            raise ValueError("Unsupported Gateway state: %s." % state)
        cpu_state = str(record.get("cpu_state") or "unknown").lower()
        if cpu_state not in CPU_STATES:
            raise ValueError("Unsupported Gateway CPU state: %s." % cpu_state)
        selected_probe = record.get("selected_probe")
        if selected_probe is not None and not isinstance(selected_probe, Mapping):
            raise ValueError("Gateway selected_probe must be an object or null.")
        gdb_endpoint = _loopback_endpoint(record.get("gdb_endpoint"), "GDB endpoint")
        tcl_endpoint = _loopback_endpoint(record.get("tcl_endpoint"), "TCL endpoint")
        raw_age = record.get("evidence_age_ms")
        evidence_age_ms = None if raw_age is None else int(raw_age)
        if evidence_age_ms is not None and evidence_age_ms < 0:
            raise ValueError("Gateway evidence age must be non-negative.")
        raw_count = record.get("gdb_connection_count")
        raw_activity = record.get("gdb_activity_generation")
        raw_ever_attached = record.get("gdb_ever_attached")
        if raw_count is not None and (not isinstance(raw_count, int) or isinstance(raw_count, bool)):
            raise ValueError("Gateway GDB connection count must be an integer.")
        if raw_activity is not None and (not isinstance(raw_activity, int) or isinstance(raw_activity, bool)):
            raise ValueError("Gateway GDB activity generation must be an integer.")
        gdb_connection_count = raw_count
        gdb_activity_generation = raw_activity
        if raw_ever_attached is not None and not isinstance(raw_ever_attached, bool):
            raise ValueError("Gateway GDB ever-attached evidence must be boolean.")
        gdb_ever_attached = raw_ever_attached
        if gdb_connection_count is not None and gdb_connection_count < 0:
            raise ValueError("Gateway GDB connection count must be non-negative.")
        if gdb_activity_generation is not None and gdb_activity_generation < 0:
            raise ValueError("Gateway GDB activity generation must be non-negative.")
        supplied_activity = (raw_count is not None, raw_activity is not None, raw_ever_attached is not None)
        if any(supplied_activity) and not all(supplied_activity):
            raise ValueError("Gateway GDB activity evidence must be complete when supplied.")
        if state == "READY":
            if selected_probe is None:
                raise ValueError("READY Gateway snapshot requires a selected probe.")
            if cpu_state not in {"running", "halted"}:
                raise ValueError("READY Gateway snapshot requires verified target run state.")
            if gdb_endpoint is None or tcl_endpoint is None:
                raise ValueError("READY Gateway snapshot requires GDB and TCL endpoints.")
            if evidence_age_ms is None or evidence_age_ms > MAX_READY_EVIDENCE_AGE_MS:
                raise ValueError("READY Gateway snapshot requires fresh target evidence.")
        return cls(
            schema_version, instance_id, generation, sequence, state, reason_code,
            selected_probe, gdb_endpoint, tcl_endpoint, cpu_state, evidence_age_ms,
            gdb_connection_count, gdb_activity_generation, gdb_ever_attached,
        )

    @property
    def attach_ready(self) -> bool:
        return self.state == "READY"

    @property
    def has_gdb_activity_evidence(self) -> bool:
        return (
            self.gdb_connection_count is not None
            and self.gdb_activity_generation is not None
            and self.gdb_ever_attached is not None
        )

    def to_record(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "generation": self.generation,
            "sequence": self.sequence,
            "state": self.state,
            "reason_code": self.reason_code,
            "selected_probe": dict(self.selected_probe) if self.selected_probe is not None else None,
            "gdb_endpoint": self.gdb_endpoint,
            "tcl_endpoint": self.tcl_endpoint,
            "cpu_state": self.cpu_state,
            "evidence_age_ms": self.evidence_age_ms,
            "gdb_connection_count": self.gdb_connection_count,
            "gdb_activity_generation": self.gdb_activity_generation,
            "gdb_ever_attached": self.gdb_ever_attached,
        }


class GatewaySnapshotTracker:
    """Reject reordered events and expire READY using the receiving host clock."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 freshness_timeout_seconds: float = 3.0) -> None:
        if freshness_timeout_seconds <= 0:
            raise ValueError("Gateway freshness timeout must be positive.")
        self._clock = clock
        self._timeout = float(freshness_timeout_seconds)
        self._snapshot: Optional[GatewaySnapshot] = None
        self._received_at: Optional[float] = None

    @property
    def snapshot(self) -> Optional[GatewaySnapshot]:
        return self._snapshot

    def accept(self, snapshot: GatewaySnapshot) -> bool:
        current = self._snapshot
        if current is not None and snapshot.instance_id == current.instance_id:
            if snapshot.generation < current.generation:
                return False
            if snapshot.generation == current.generation and snapshot.sequence <= current.sequence:
                return False
        self._snapshot = snapshot
        self._received_at = self._clock()
        return True

    @property
    def attach_ready(self) -> bool:
        if self._snapshot is None or self._received_at is None:
            return False
        return (
            self._snapshot.attach_ready
            and self._clock() - self._received_at <= self._timeout
        )


__all__ = [
    "GatewaySnapshot", "GatewaySnapshotTracker", "SUPPORTED_SCHEMA_VERSION",
]
