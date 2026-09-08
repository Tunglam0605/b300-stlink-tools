"""Immutable, ordered device evidence shared by GUI consumers."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field, replace
from threading import RLock
from typing import Callable, Optional


LIVE = "LIVE"
STALE = "STALE"
DISCONNECTED = "DISCONNECTED"


def _endpoint(value: object, label: str) -> Optional[str]:
    if value is None:
        return None
    host, separator, port_text = str(value).rpartition(":")
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
class DeviceSnapshot:
    connection_id: str = "local"
    probe_identity: Optional[str] = None
    probe_serial: Optional[str] = None
    owner_kind: Optional[str] = None
    lease_token: Optional[str] = field(default=None, repr=False, compare=False)
    gateway_instance_id: Optional[str] = None
    gateway_generation: Optional[int] = None
    sequence: Optional[int] = None
    ssh_generation: Optional[int] = None
    gdb_endpoint: Optional[str] = None
    tcl_endpoint: Optional[str] = None
    target_state: Optional[str] = None
    checked_monotonic: Optional[float] = None
    liveness: str = DISCONNECTED
    axf_basename: Optional[str] = None
    axf_fingerprint: Optional[str] = None
    epoch: int = 0
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "gdb_endpoint", _endpoint(self.gdb_endpoint, "gdb_endpoint"))
        object.__setattr__(self, "tcl_endpoint", _endpoint(self.tcl_endpoint, "tcl_endpoint"))
        if self.target_state is not None and not isinstance(self.target_state, str):
            raise ValueError("target_state must be a support-safe string or null.")
        for name in (
            "connection_id", "probe_identity", "probe_serial", "owner_kind",
            "lease_token", "gateway_instance_id", "axf_basename", "axf_fingerprint", "reason",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError("%s must be a support-safe string or null." % name)
        if self.liveness not in {LIVE, STALE, DISCONNECTED}:
            raise ValueError("Unsupported device liveness: %s." % self.liveness)
        if self.epoch < 0:
            raise ValueError("epoch must be non-negative.")

    @property
    def profile_id(self) -> str:
        """Compatibility name for connection-backed Gateway profiles."""
        return self.connection_id

    @property
    def gateway_instance(self) -> Optional[str]:
        return self.gateway_instance_id

    def to_record(self) -> dict:
        """Return support-safe state; a lease token is deliberately excluded."""
        return {
            name: getattr(self, name) for name in (
                "connection_id", "probe_identity", "probe_serial", "owner_kind",
                "gateway_instance_id", "gateway_generation", "sequence", "ssh_generation",
                "gdb_endpoint", "tcl_endpoint", "target_state", "checked_monotonic",
                "liveness", "axf_basename", "axf_fingerprint", "epoch", "reason",
            )
        }


class DeviceStateStore:
    """Serialize device evidence and discard stale events without Qt affinity."""

    _BINDING_FIELDS = frozenset((
        "connection_id", "probe_identity", "probe_serial", "gateway_instance_id",
        "gateway_generation", "ssh_generation", "axf_basename", "axf_fingerprint",
    ))

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = RLock()
        self._snapshot = DeviceSnapshot()

    @property
    def snapshot(self) -> DeviceSnapshot:
        with self._lock:
            return self._snapshot

    def reduce(self, **updates: object) -> bool:
        """Apply one evidence event. Returns false when its epoch/order is obsolete."""
        with self._lock:
            current = self._snapshot
            event_epoch = updates.pop("epoch", None)
            if event_epoch is not None and int(event_epoch) != current.epoch:
                return False
            unknown = set(updates) - set(DeviceSnapshot.__dataclass_fields__)
            if unknown:
                raise TypeError("Unknown device state field(s): %s" % ", ".join(sorted(unknown)))
            if (updates.get("owner_kind", object()) is None and "lease_token" in updates
                    and updates["lease_token"] != current.lease_token):
                return False
            for field_name in ("gateway_generation", "sequence", "ssh_generation"):
                if field_name in updates and updates[field_name] is not None and int(updates[field_name]) < 0:
                    raise ValueError("%s must be non-negative." % field_name)

            candidate = replace(current, **updates)
            same_binding = all(getattr(candidate, name) == getattr(current, name)
                               for name in self._BINDING_FIELDS)
            if (same_binding and "sequence" in updates and current.sequence is not None
                    and candidate.sequence is not None and candidate.sequence <= current.sequence):
                return False

            binding_changed = not same_binding
            if binding_changed:
                candidate = replace(
                    candidate, epoch=current.epoch + 1, gdb_endpoint=None, tcl_endpoint=None,
                    target_state=None, checked_monotonic=None, liveness=STALE,
                    reason=str(updates.get("reason") or "device binding changed"),
                )
            elif candidate.target_state is not None and ("target_state" in updates or
                                                          "gdb_endpoint" in updates or
                                                          "tcl_endpoint" in updates):
                checked = candidate.checked_monotonic
                if checked is None:
                    checked = float(self._clock())
                candidate = replace(candidate, checked_monotonic=checked, liveness=LIVE,
                                    reason=str(updates.get("reason") or ""))
            elif candidate.target_state is None and candidate.liveness == LIVE:
                candidate = replace(candidate, liveness=STALE, checked_monotonic=None)

            if candidate == current:
                return False
            self._snapshot = candidate
            return True

    apply = reduce

    def expire(self, freshness_timeout_seconds: float, *, reason: str = "evidence expired") -> bool:
        if freshness_timeout_seconds <= 0:
            raise ValueError("freshness timeout must be positive.")
        with self._lock:
            current = self._snapshot
            if (current.liveness != LIVE or current.checked_monotonic is None or
                    self._clock() - current.checked_monotonic <= freshness_timeout_seconds):
                return False
            self._snapshot = replace(current, liveness=STALE, reason=str(reason))
            return True


__all__ = ["DeviceSnapshot", "DeviceStateStore", "LIVE", "STALE", "DISCONNECTED"]
