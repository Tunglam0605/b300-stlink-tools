"""Private, bounded file-queue protocol for the per-user Gateway Agent."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from .gateway_supervisor import gateway_runtime_root


AGENT_PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_PENDING_REQUESTS = 128
AGENT_OPERATIONS = frozenset({"status", "acquire", "renew", "release", "rescan", "shutdown"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _id(value: object) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError("Gateway Agent request id is invalid.")
    return value


def _number(value: object, label: str) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(float(value)) or float(value) < 0):
        raise ValueError("%s must be a non-negative finite number." % label)
    return float(value)


@dataclass(frozen=True)
class GatewayRequest:
    schema_version: int
    request_id: str
    operation: str
    created_mono: float
    expires_mono: float
    payload: dict

    @classmethod
    def create(cls, operation: str, payload: Mapping[str, object], *,
               request_id: Optional[str] = None, timeout_seconds: float = 10.0,
               now_mono: Optional[float] = None) -> "GatewayRequest":
        now = time.monotonic() if now_mono is None else _number(now_mono, "request time")
        timeout = _number(timeout_seconds, "request timeout")
        if timeout <= 0 or timeout > 60:
            raise ValueError("Gateway Agent request timeout must be in (0, 60].")
        return cls.from_record({
            "schema_version": AGENT_PROTOCOL_VERSION,
            "request_id": request_id or uuid.uuid4().hex,
            "operation": operation,
            "created_mono": now,
            "expires_mono": now + timeout,
            "payload": dict(payload),
        })

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "GatewayRequest":
        if not isinstance(record, Mapping) or set(record) != {
            "schema_version", "request_id", "operation", "created_mono",
            "expires_mono", "payload",
        }:
            raise ValueError("Gateway Agent request schema is invalid.")
        version = record["schema_version"]
        if type(version) is not int or version != AGENT_PROTOCOL_VERSION:
            raise ValueError("Gateway Agent request protocol is unsupported.")
        request_id = _id(record["request_id"])
        operation = record["operation"]
        if not isinstance(operation, str) or operation not in AGENT_OPERATIONS:
            raise ValueError("Gateway Agent operation is unsupported.")
        created = _number(record["created_mono"], "request creation time")
        expires = _number(record["expires_mono"], "request expiry time")
        if expires <= created:
            raise ValueError("Gateway Agent request expiry must follow creation.")
        payload = record["payload"]
        if not isinstance(payload, dict):
            raise ValueError("Gateway Agent request payload must be an object.")
        # JSON round trip rejects unserializable values and prevents custom mappings.
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES // 2:
            raise ValueError("Gateway Agent request payload is too large.")
        return cls(version, request_id, operation, created, expires, json.loads(encoded))

    def to_record(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "operation": self.operation,
            "created_mono": self.created_mono,
            "expires_mono": self.expires_mono,
            "payload": self.payload,
        }


class GatewayRequestStore:
    def __init__(self, root: Optional[Path] = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.root = Path(root) if root is not None else gateway_runtime_root() / "agent-control"
        self.requests_dir = self.root / "requests"
        self.responses_dir = self.root / "responses"
        self._clock = clock
        self._sleep = sleep

    def enqueue(self, request: GatewayRequest) -> Path:
        selected = GatewayRequest.from_record(request.to_record())
        self._prepare()
        if self.response_path(selected.request_id).exists():
            raise FileExistsError("REQUEST_REPLAYED")
        path = self.request_path(selected.request_id)
        data = json.dumps(selected.to_record(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError("Gateway Agent request is too large.")
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return path

    def submit_request(self, request: GatewayRequest, *, timeout_seconds: float = 10.0) -> dict:
        if self.response_path(request.request_id).exists():
            return self._error(request.request_id, "REQUEST_REPLAYED")
        try:
            self.enqueue(request)
        except FileExistsError:
            return self._error(request.request_id, "REQUEST_REPLAYED")
        deadline = self._clock() + min(60.0, max(0.01, float(timeout_seconds)))
        while self._clock() < deadline:
            response = self.read_response(request.request_id)
            if response is not None:
                # The submitting client has consumed the response; remove it
                # immediately so completed artifacts cannot accumulate.
                try:
                    self.response_path(request.request_id).unlink()
                except OSError:
                    pass
                return response
            self._sleep(min(0.05, max(0.0, deadline - self._clock())))
        return self._error(request.request_id, "AGENT_RESPONSE_TIMEOUT")

    def submit(self, operation: str, payload: Mapping[str, object],
               timeout_seconds: float = 10.0) -> dict:
        return self.submit_request(
            GatewayRequest.create(operation, payload, timeout_seconds=timeout_seconds),
            timeout_seconds=timeout_seconds,
        )

    def pending(self) -> Sequence[GatewayRequest]:
        self._prepare()
        results = []
        for path in sorted(self.requests_dir.glob("*.json"))[:MAX_PENDING_REQUESTS]:
            try:
                if path.stat().st_size > MAX_REQUEST_BYTES:
                    raise ValueError("oversized request")
                raw = path.read_bytes()
                if len(raw) > MAX_REQUEST_BYTES:
                    raise ValueError("oversized request")
                request = GatewayRequest.from_record(json.loads(raw.decode("utf-8")))
                if path.name != request.request_id + ".json":
                    raise ValueError("request filename mismatch")
                results.append(request)
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                try:
                    path.unlink()
                except OSError:
                    pass
        return tuple(results)

    def respond(self, request_id: str, record: Mapping[str, object]) -> None:
        selected_id = _id(request_id)
        payload = dict(record)
        payload.setdefault("protocol_version", AGENT_PROTOCOL_VERSION)
        payload.setdefault("request_id", selected_id)
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("Gateway Agent response is too large.")
        self._prepare()
        fd, name = tempfile.mkstemp(prefix=selected_id + ".", suffix=".tmp", dir=str(self.responses_dir))
        temp = Path(name)
        try:
            if os.name != "nt": os.chmod(str(temp), 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temp), str(self.response_path(selected_id)))
        finally:
            try: temp.unlink()
            except OSError: pass

    def read_response(self, request_id: str) -> Optional[dict]:
        path = self.response_path(_id(request_id))
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_RESPONSE_BYTES:
                return None
            record = json.loads(raw.decode("utf-8"))
            return record if isinstance(record, dict) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None

    def complete(self, request_id: str) -> None:
        try: self.request_path(_id(request_id)).unlink()
        except FileNotFoundError: pass
        try: self.response_path(_id(request_id)).unlink()
        except FileNotFoundError: pass

    def request_path(self, request_id: str) -> Path:
        return self.requests_dir / (_id(request_id) + ".json")

    def response_path(self, request_id: str) -> Path:
        return self.responses_dir / (_id(request_id) + ".json")

    def _prepare(self) -> None:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(str(self.root), 0o700)
            os.chmod(str(self.requests_dir), 0o700)
            os.chmod(str(self.responses_dir), 0o700)

    @staticmethod
    def _error(request_id: str, reason: str) -> dict:
        return {"protocol_version": AGENT_PROTOCOL_VERSION, "request_id": request_id,
                "status": "error", "reason_code": reason}


__all__ = [
    "AGENT_OPERATIONS", "AGENT_PROTOCOL_VERSION", "GatewayRequest",
    "GatewayRequestStore", "MAX_PENDING_REQUESTS", "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
]
