"""Strict, non-secret contracts for exclusive remote Gateway ownership."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


SUPPORTED_LEASE_SCHEMA_VERSION = 1
LEASE_MODES = frozenset({"LIVE_WATCH", "VSCODE_DEBUG"})
LEASE_STATES = frozenset({
    "RESERVED", "STARTING", "ACTIVE", "GRACE", "CLEANING",
    "RECOVERY_REQUIRED",
})
MAX_IDENTIFIER_LENGTH = 64
MAX_CLIENT_LABEL_LENGTH = 64
MAX_TOKEN_LENGTH = 256

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_REASON = re.compile(r"^[A-Z][A-Z0-9_]*$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE = re.compile(r"\s+")


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError("%s must be an integer greater than or equal to %d." % (label, minimum))
    return value


def _finite_number(value: object, label: str, *, minimum: float = 0.0,
                   inclusive: bool = True) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(float(value))):
        raise ValueError("%s must be a finite number." % label)
    selected = float(value)
    if (selected < minimum if inclusive else selected <= minimum):
        relation = "at least" if inclusive else "greater than"
        raise ValueError("%s must be %s %s." % (label, relation, minimum))
    return selected


def _safe_identifier(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError("%s must be text." % label)
    selected = value.strip()
    if (not selected or len(selected) > MAX_IDENTIFIER_LENGTH
            or _SAFE_IDENTIFIER.fullmatch(selected) is None):
        raise ValueError("%s contains unsupported characters or length." % label)
    return selected


def _optional_probe_serial(value: object) -> Optional[str]:
    if value is None:
        return None
    return _safe_identifier(value, "Gateway lease probe serial")


def sanitize_client_label(value: object) -> str:
    """Return bounded display text that cannot inject terminal/UI control data."""
    text = _CONTROL_CHARACTERS.sub(" ", str(value or ""))
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return "Unknown Client"
    return text[:MAX_CLIENT_LABEL_LENGTH].rstrip() or "Unknown Client"


def token_digest(token: object) -> str:
    """Hash one private lease token for persistence and constant-size comparison."""
    if not isinstance(token, str):
        raise ValueError("Gateway lease token must be text.")
    if (not token or len(token) > MAX_TOKEN_LENGTH
            or _CONTROL_CHARACTERS.search(token) is not None):
        raise ValueError("Gateway lease token contains unsupported characters or length.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GatewayLeasePolicy:
    heartbeat_interval_seconds: float = 5.0
    lease_ttl_seconds: float = 20.0
    reconnect_grace_seconds: float = 10.0
    cleanup_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        heartbeat = _finite_number(
            self.heartbeat_interval_seconds, "Gateway heartbeat interval", minimum=0.0,
            inclusive=False,
        )
        ttl = _finite_number(
            self.lease_ttl_seconds, "Gateway lease TTL", minimum=0.0, inclusive=False,
        )
        grace = _finite_number(
            self.reconnect_grace_seconds, "Gateway reconnect grace", minimum=0.0,
        )
        cleanup = _finite_number(
            self.cleanup_timeout_seconds, "Gateway cleanup timeout", minimum=0.0,
            inclusive=False,
        )
        if ttl <= heartbeat:
            raise ValueError("Gateway lease TTL must be greater than its heartbeat interval.")
        object.__setattr__(self, "heartbeat_interval_seconds", heartbeat)
        object.__setattr__(self, "lease_ttl_seconds", ttl)
        object.__setattr__(self, "reconnect_grace_seconds", grace)
        object.__setattr__(self, "cleanup_timeout_seconds", cleanup)


@dataclass(frozen=True)
class GatewayLeaseRequest:
    request_id: str
    client_id: str
    client_label: str
    mode: str
    probe_serial: Optional[str] = None

    def __post_init__(self) -> None:
        request_id = _safe_identifier(self.request_id, "Gateway lease request id")
        client_id = _safe_identifier(self.client_id, "Gateway lease client id")
        label = sanitize_client_label(self.client_label)
        if not isinstance(self.mode, str):
            raise ValueError("Gateway lease mode must be text.")
        mode = self.mode.strip().upper()
        if mode not in LEASE_MODES:
            raise ValueError("Unsupported Gateway lease mode: %s." % mode)
        serial = _optional_probe_serial(self.probe_serial)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "client_id", client_id)
        object.__setattr__(self, "client_label", label)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "probe_serial", serial)


@dataclass(frozen=True)
class GatewayLease:
    schema_version: int
    lease_id: str
    token_digest: str
    generation: int
    request_id: str
    client_id: str
    client_label: str
    mode: str
    state: str
    acquired_at: str
    last_heartbeat_mono: float
    deadline_mono: float
    grace_deadline_mono: Optional[float]
    gateway_instance_id: str
    gateway_generation: int
    probe_serial: Optional[str]
    reason_code: str

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "GatewayLease":
        if not isinstance(record, Mapping):
            raise ValueError("Gateway lease must be a JSON object.")
        expected = {
            "schema_version", "lease_id", "token_digest", "generation", "request_id",
            "client_id", "client_label", "mode", "state", "acquired_at",
            "last_heartbeat_mono", "deadline_mono", "grace_deadline_mono",
            "gateway_instance_id", "gateway_generation", "probe_serial", "reason_code",
        }
        if set(record) != expected:
            raise ValueError("Gateway lease schema fields are invalid.")
        schema = _strict_int(record["schema_version"], "Gateway lease schema version")
        if schema != SUPPORTED_LEASE_SCHEMA_VERSION:
            raise ValueError("Unsupported Gateway lease schema version: %s." % schema)
        lease_id = _safe_identifier(record["lease_id"], "Gateway lease id")
        digest = record["token_digest"]
        if not isinstance(digest, str) or _HEX_DIGEST.fullmatch(digest) is None:
            raise ValueError("Gateway lease token digest is invalid.")
        generation = _strict_int(record["generation"], "Gateway lease generation")
        request_id = _safe_identifier(record["request_id"], "Gateway lease request id")
        client_id = _safe_identifier(record["client_id"], "Gateway lease client id")
        label = sanitize_client_label(record["client_label"])
        if label != record["client_label"]:
            raise ValueError("Gateway lease client label must already be sanitized.")
        mode = record["mode"]
        if not isinstance(mode, str) or mode not in LEASE_MODES:
            raise ValueError("Unsupported Gateway lease mode: %s." % mode)
        state = record["state"]
        if not isinstance(state, str) or state not in LEASE_STATES:
            raise ValueError("Unsupported Gateway lease state: %s." % state)
        acquired_at = record["acquired_at"]
        if (not isinstance(acquired_at, str) or not acquired_at.strip()
                or len(acquired_at) > 40 or _CONTROL_CHARACTERS.search(acquired_at)):
            raise ValueError("Gateway lease acquired_at is invalid.")
        heartbeat = _finite_number(
            record["last_heartbeat_mono"], "Gateway last heartbeat", minimum=0.0,
        )
        deadline = _finite_number(record["deadline_mono"], "Gateway lease deadline", minimum=0.0)
        if heartbeat > deadline:
            raise ValueError("Gateway lease deadline cannot precede its heartbeat.")
        raw_grace = record["grace_deadline_mono"]
        grace = None if raw_grace is None else _finite_number(
            raw_grace, "Gateway grace deadline", minimum=0.0,
        )
        if state == "GRACE":
            if grace is None or grace < deadline:
                raise ValueError("GRACE lease requires a deadline after lease expiry.")
        elif grace is not None:
            raise ValueError("Only a GRACE lease may contain a grace deadline.")
        instance_id = _safe_identifier(
            record["gateway_instance_id"], "Gateway lease instance id",
        )
        gateway_generation = _strict_int(
            record["gateway_generation"], "Gateway lease Gateway generation",
        )
        serial = _optional_probe_serial(record["probe_serial"])
        reason = record["reason_code"]
        if (not isinstance(reason, str) or len(reason) > MAX_IDENTIFIER_LENGTH
                or _SAFE_REASON.fullmatch(reason) is None):
            raise ValueError("Gateway lease reason code is invalid.")
        return cls(
            schema, lease_id, digest, generation, request_id, client_id, label, mode,
            state, acquired_at, heartbeat, deadline, grace, instance_id,
            gateway_generation, serial, reason,
        )

    def to_record(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "lease_id": self.lease_id,
            "token_digest": self.token_digest,
            "generation": self.generation,
            "request_id": self.request_id,
            "client_id": self.client_id,
            "client_label": self.client_label,
            "mode": self.mode,
            "state": self.state,
            "acquired_at": self.acquired_at,
            "last_heartbeat_mono": self.last_heartbeat_mono,
            "deadline_mono": self.deadline_mono,
            "grace_deadline_mono": self.grace_deadline_mono,
            "gateway_instance_id": self.gateway_instance_id,
            "gateway_generation": self.gateway_generation,
            "probe_serial": self.probe_serial,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class GatewayLeasePublicSnapshot:
    active: bool
    lease_id: str
    generation: int
    client_label: str
    mode: str
    state: str
    acquired_at: str
    heartbeat_age_seconds: int
    gateway_instance_id: str
    gateway_generation: int
    probe_serial: Optional[str]
    reason_code: str

    @classmethod
    def from_lease(cls, lease: GatewayLease,
                   now_mono: float) -> "GatewayLeasePublicSnapshot":
        now = _finite_number(now_mono, "Gateway snapshot time", minimum=0.0)
        age = max(0, int(now - lease.last_heartbeat_mono))
        return cls(
            True, lease.lease_id, lease.generation, lease.client_label, lease.mode,
            lease.state, lease.acquired_at, age, lease.gateway_instance_id,
            lease.gateway_generation, lease.probe_serial, lease.reason_code,
        )

    def to_record(self) -> dict:
        return {
            "active": self.active,
            "lease_id": self.lease_id,
            "generation": self.generation,
            "client_label": self.client_label,
            "mode": self.mode,
            "state": self.state,
            "acquired_at": self.acquired_at,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "gateway_instance_id": self.gateway_instance_id,
            "gateway_generation": self.gateway_generation,
            "probe_serial": self.probe_serial,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class GatewayLeaseGrant:
    lease_id: str
    token: str
    generation: int
    public: GatewayLeasePublicSnapshot

    @classmethod
    def from_lease(cls, lease: GatewayLease, token: str,
                   now_mono: float) -> "GatewayLeaseGrant":
        if token_digest(token) != lease.token_digest:
            raise ValueError("Gateway lease grant token does not match the lease.")
        return cls(
            lease.lease_id, token, lease.generation,
            GatewayLeasePublicSnapshot.from_lease(lease, now_mono),
        )


@dataclass(frozen=True)
class GatewayLeaseBusy:
    client_label: str
    mode: str
    started_at: str
    heartbeat_age_seconds: int
    reason_code: str = "GATEWAY_BUSY"

    @classmethod
    def from_lease(cls, lease: GatewayLease, now_mono: float) -> "GatewayLeaseBusy":
        public = GatewayLeasePublicSnapshot.from_lease(lease, now_mono)
        return cls(
            public.client_label, public.mode, public.acquired_at,
            public.heartbeat_age_seconds,
        )

    def to_record(self) -> dict:
        return {
            "client_label": self.client_label,
            "mode": self.mode,
            "started_at": self.started_at,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "reason_code": self.reason_code,
        }


def _default_lease_path() -> Path:
    override = os.environ.get("B300_GATEWAY_RUNTIME_DIR")
    root = Path(override).expanduser() if override else (
        Path.home() / ".b300-stlink" / "gateway-runtime"
    )
    return root / "lease.json"


class GatewayLeaseStore:
    """Atomic private lease persistence; corrupt state is never treated as idle."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or _default_lease_path()).expanduser()

    def read(self) -> Optional[GatewayLease]:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return GatewayLease.from_record(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise RuntimeError(
                "B300 Gateway lease store is unreadable/corrupt: %s" % self.path
            ) from error

    def write(self, lease: GatewayLease) -> None:
        selected = GatewayLease.from_record(lease.to_record())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(str(self.path.parent), 0o700)
        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent),
        )
        temp = Path(temp_name)
        try:
            if os.name != "nt":
                os.chmod(str(temp), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(selected.to_record(), handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temp), str(self.path))
            if os.name != "nt":
                os.chmod(str(self.path), 0o600)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

    def clear_if_generation(self, generation: int) -> bool:
        selected = _strict_int(generation, "Gateway lease generation")
        current = self.read()
        if current is None or current.generation != selected:
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


__all__ = [
    "GatewayLease", "GatewayLeaseBusy", "GatewayLeaseGrant", "GatewayLeasePolicy",
    "GatewayLeasePublicSnapshot", "GatewayLeaseRequest", "GatewayLeaseStore",
    "LEASE_MODES", "LEASE_STATES", "SUPPORTED_LEASE_SCHEMA_VERSION",
    "sanitize_client_label", "token_digest",
]
