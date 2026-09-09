"""Client-side lease ownership and heartbeat for Live Watch/VS Code flows."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RemoteLeaseGrant:
    lease_id: str
    token: str
    generation: int
    public: dict


class GatewayBusyError(RuntimeError):
    def __init__(self, message: str, *, client_label: str = "",
                 mode: str = "", heartbeat_age_seconds: int = 0,
                 reason_code: str = "GATEWAY_BUSY") -> None:
        if client_label and client_label not in message:
            message = "%s (%s)" % (message, client_label)
        super().__init__(message)
        self.client_label = client_label
        self.mode = mode
        self.heartbeat_age_seconds = int(heartbeat_age_seconds)
        self.reason_code = reason_code


class GatewayLeaseClient:
    def __init__(self, session, *, client_id: str, client_label: str,
                 heartbeat_interval_seconds: float = 5.0,
                 on_lost: Optional[callable] = None) -> None:
        if not str(client_id).strip() or not str(client_label).strip():
            raise ValueError("Gateway lease client identity is required.")
        if not 0.1 <= float(heartbeat_interval_seconds) <= 30:
            raise ValueError("Gateway lease heartbeat interval is out of range.")
        self.session = session
        self.client_id = str(client_id)
        self.client_label = str(client_label)
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self._lock = threading.RLock()
        self._grant: Optional[RemoteLeaseGrant] = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._closed = False
        self._on_lost = on_lost

    @property
    def grant(self) -> Optional[RemoteLeaseGrant]:
        with self._lock:
            return self._grant

    def start(self, mode: str, *, probe_serial: Optional[str] = None) -> RemoteLeaseGrant:
        with self._lock:
            if self._closed:
                raise RuntimeError("Gateway lease client is closed.")
            if self._grant is not None:
                return self._grant
        self.session.ensure_gateway_agent()
        try:
            grant = self.session.acquire_gateway({
                "request_id": uuid.uuid4().hex,
                "client_id": self.client_id,
                "client_label": self.client_label,
                "mode": str(mode).upper(),
                "probe_serial": probe_serial,
            })
        except GatewayBusyError:
            raise
        except Exception as error:
            details = getattr(error, "reason_code", "GATEWAY_ACQUIRE_FAILED")
            raise RuntimeError("Gateway lease acquire failed: %s" % details) from error
        if not isinstance(grant, RemoteLeaseGrant):
            raise RuntimeError("Gateway returned an invalid lease grant.")
        with self._lock:
            if self._closed:
                try:
                    self.session.release_gateway(grant)
                except Exception:
                    pass
                raise RuntimeError("Gateway lease client was closed during acquire.")
            self._grant = grant
            self._heartbeat_stop.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, name="b300-gateway-lease", daemon=True,
            )
            self._heartbeat_thread.start()
        return grant

    def renew_once(self) -> dict:
        with self._lock:
            grant = self._grant
        if grant is None:
            raise RuntimeError("Gateway lease is not active.")
        try:
            result = self.session.renew_gateway(grant)
        except Exception as error:
            self._invalidate_local()
            if isinstance(error, GatewayBusyError):
                raise
            raise RuntimeError("Gateway lease heartbeat failed.") from error
        if isinstance(result, Exception):
            self._invalidate_local()
            raise result
        if isinstance(result, dict) and result.get("reason_code") == "LEASE_INVALID":
            self._invalidate_local()
            raise RuntimeError("Gateway lease is no longer valid.")
        try:
            from .gateway_lease import GatewayLeasePublicSnapshot
            original = GatewayLeasePublicSnapshot.from_record(grant.public)
            renewed = GatewayLeasePublicSnapshot.from_record(result)
        except (TypeError, ValueError):
            self._invalidate_local()
            raise RuntimeError("Gateway lease renewal returned a malformed public snapshot.")
        if (renewed.lease_id != grant.lease_id
                or renewed.generation != grant.generation
                or (renewed.gateway_instance_id, renewed.gateway_generation,
                    renewed.gdb_endpoint, renewed.tcl_endpoint)
                != (original.gateway_instance_id, original.gateway_generation,
                    original.gdb_endpoint, original.tcl_endpoint)):
            self._invalidate_local()
            raise RuntimeError("Gateway lease renewal returned a stale Gateway binding.")
        return result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            grant = self._grant
            self._grant = None
            thread = self._heartbeat_thread
            self._heartbeat_thread = None
            self._heartbeat_stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.heartbeat_interval_seconds + 0.5))
        if grant is not None:
            try:
                self.session.release_gateway(grant)
            except Exception:
                pass

    def _invalidate_local(self) -> None:
        callback = None
        grant = None
        with self._lock:
            grant = self._grant
            self._grant = None
            self._heartbeat_stop.set()
            callback = self._on_lost
        if grant is not None:
            try:
                self.session.release_gateway(grant)
            except Exception:
                pass
        if callback is not None:
            try:
                callback()
            except Exception:
                pass

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_interval_seconds):
            try:
                self.renew_once()
            except Exception:
                return


__all__ = ["GatewayBusyError", "GatewayLeaseClient", "RemoteLeaseGrant"]
