"""Core-only Gateway binding and race-safe recovery over one RemoteSession."""
from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from .gateway_status import GatewaySnapshot
from .remote_session import RemoteSessionError

_SAFE_OWNER_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_TOKEN_LOCK = threading.RLock()
_NON_RETRIABLE = frozenset({"AUTH_FAILED", "CLI_NOT_FOUND", "CLI_TOO_OLD", "PROTOCOL_MISMATCH"})
_NON_RETRIABLE_GATEWAY = frozenset({"MULTIPLE_PROBES", "TARGET_UNVERIFIED"})


def _port(endpoint: str) -> int: return int(endpoint.rpartition(":")[2])
def _endpoint(forward) -> str:
    host, port = forward.endpoint; return "%s:%d" % (host, port)


@dataclass(frozen=True)
class GatewayBinding:
    profile_id: str; ssh_generation: int; instance_id: str; gateway_generation: int
    sequence: int; gdb_endpoint: str; tcl_endpoint: str


@dataclass(frozen=True)
class GatewayClientState:
    state: str; binding: Optional[GatewayBinding] = None; reason_code: str = "GATEWAY_NOT_READY"
    next_action: str = "Check the Gateway and retry."; retriable: bool = True


class GatewayClientCoordinator:
    """Own token-scoped forwards; no RemoteSession I/O occurs while locked."""
    def __init__(self, session, profile_id: str, *, max_recovery_attempts: int = 3, owner_token: Optional[str] = None) -> None:
        if not str(profile_id).strip(): raise ValueError("Gateway profile id is required.")
        if not 1 <= int(max_recovery_attempts) <= 3: raise ValueError("Gateway recovery attempts must be between 1 and 3.")
        selected = str(owner_token or uuid.uuid4().hex[:16]).strip()
        if not _SAFE_OWNER_TOKEN.fullmatch(selected): raise ValueError("Gateway coordinator owner token contains unsupported characters.")
        with _TOKEN_LOCK:
            used = getattr(session, "_b300_gateway_client_owner_tokens", None)
            if used is None: used = set(); setattr(session, "_b300_gateway_client_owner_tokens", used)
            if selected in used: raise ValueError("Gateway coordinator owner token is already in use for this SSH session.")
            used.add(selected)
        self.session, self.profile_id, self.max_recovery_attempts, self.owner_token = session, str(profile_id), int(max_recovery_attempts), selected
        self._lock = threading.RLock(); self._mutation_token = 0; self._bind_attempt = 0; self._closed = False
        self._binding = None; self._snapshot = None; self._owned = (); self._state = GatewayClientState("STALE")

    @property
    def state(self):
        with self._lock: return self._state
    @property
    def binding(self):
        with self._lock: return self._binding
    @property
    def owned_forward_names(self):
        with self._lock: return self._owned

    @staticmethod
    def _same_route(left, right):
        return (left.instance_id, left.generation, left.gdb_endpoint, left.tcl_endpoint) == (right.instance_id, right.generation, right.gdb_endpoint, right.tcl_endpoint)
    def _valid(self, token):
        return not self._closed and token == self._mutation_token
    def _start(self):
        with self._lock:
            if self._closed: return -1
            self._mutation_token += 1; return self._mutation_token
    def _accept_locked(self, snapshot):
        old = self._snapshot
        if old is not None and snapshot.instance_id == old.instance_id and (snapshot.generation < old.generation or (snapshot.generation == old.generation and snapshot.sequence <= old.sequence)): return False
        self._snapshot = snapshot; return True
    def _stale_locked(self, reason, action, retriable):
        self._state = GatewayClientState("STALE", None, reason, action, retriable); return self._state
    def _invalidate_locked(self, reason, action, retriable):
        names = self._owned; self._owned = (); self._binding = None; self._stale_locked(reason, action, retriable); return names
    def _close_names(self, names):
        for name in names:
            try: self.session.close_forward(name)
            except Exception: pass
    def _new_names(self, token):
        with self._lock:
            if not self._valid(token): return ()
            self._bind_attempt += 1; stem = "gateway_client_%s_%d" % (self.owner_token, self._bind_attempt)
            return stem + "_gdb", stem + "_tcl"

    def _stage_bind(self, snapshot, token):
        names = self._new_names(token)
        if not names: return self.state
        staged = ()
        try:
            gdb = self.session.open_forward(names[0], remote_port=_port(snapshot.gdb_endpoint)); staged = (names[0],)
            tcl = self.session.open_forward(names[1], remote_port=_port(snapshot.tcl_endpoint)); staged = names
            ssh_generation = int(self.session.state.generation)
        except Exception:
            self._close_names(staged); raise
        binding = GatewayBinding(self.profile_id, ssh_generation, snapshot.instance_id, snapshot.generation, snapshot.sequence, _endpoint(gdb), _endpoint(tcl))
        with self._lock:
            if not self._valid(token) or self._snapshot != snapshot: committed = False; old = ()
            else:
                old = self._owned; self._owned = names; self._binding = binding
                self._state = GatewayClientState("READY", binding, "TARGET_VERIFIED", "No action is required.", False); result = self._state; committed = True
        if not committed: self._close_names(staged); return self.state
        self._close_names(old); return result

    def _ready(self, snapshot, token):
        with self._lock:
            if not self._valid(token): return self._state
            previous = self._snapshot
            if not self._accept_locked(snapshot): return self._state
            binding, owned = self._binding, self._owned
        # This reentrant RemoteSession read is deliberately outside our lock.
        session_state = self.session.state
        alive = set(owned) <= set(getattr(session_state, "forwards", owned))
        with self._lock:
            if not self._valid(token): return self._state
            reuse = binding is not None and previous is not None and self._same_route(snapshot, previous) and alive and binding.ssh_generation == int(session_state.generation)
            if reuse:
                self._binding = GatewayBinding(binding.profile_id, binding.ssh_generation, binding.instance_id, binding.gateway_generation, snapshot.sequence, binding.gdb_endpoint, binding.tcl_endpoint)
                self._state = GatewayClientState("READY", self._binding, "TARGET_VERIFIED", "No action is required.", False); return self._state
        try: return self._stage_bind(snapshot, token)
        except RemoteSessionError as error:
            with self._lock:
                if not self._valid(token): return self._state
                return self._state if self._binding is not None else self._stale_locked(error.reason_code, error.next_action, error.retriable)
        except Exception:
            with self._lock:
                if not self._valid(token): return self._state
                return self._state if self._binding is not None else self._stale_locked("TUNNEL_FAILED", "Check the SSH tunnel and retry.", True)

    def _recover(self, token):
        action = "Check the Gateway probe and retry when it is ready."
        for _ in range(self.max_recovery_attempts):
            for command in (self.session.gateway_rescan, self.session.ensure_gateway_ready):
                if not self._valid(token): return self.state
                try: snapshot = command()
                except RemoteSessionError as error:
                    action = error.next_action
                    if error.reason_code in _NON_RETRIABLE or not error.retriable:
                        with self._lock: return self._stale_locked(error.reason_code, error.next_action, False) if self._valid(token) else self._state
                    continue
                if snapshot.attach_ready:
                    result = self._ready(snapshot, token)
                    if result.state == "READY" or not self._valid(token): return result
                elif snapshot.reason_code in _NON_RETRIABLE_GATEWAY:
                    with self._lock: return self._stale_locked(snapshot.reason_code, "Resolve the Gateway status before retrying.", False) if self._valid(token) else self._state
        with self._lock: return self._stale_locked("GATEWAY_RECOVERY_EXHAUSTED", action, True) if self._valid(token) else self._state

    def _apply(self, snapshot, token):
        if not self._valid(token): return self.state
        if snapshot.attach_ready:
            result = self._ready(snapshot, token)
            return self._recover(token) if result.reason_code == "TUNNEL_FAILED" and self._valid(token) else result
        with self._lock:
            if not self._valid(token): return self._state
            old = self._invalidate_locked(snapshot.reason_code, "Resolve the Gateway status before retrying.", True); nonretry = snapshot.reason_code in _NON_RETRIABLE_GATEWAY
        self._close_names(old)
        return self.state if nonretry else self._recover(token)

    def ensure_ready(self):
        token = self._start()
        if token < 0: return self.state
        try: snapshot = self.session.gateway_status()
        except RemoteSessionError as error:
            with self._lock: old = self._invalidate_locked(error.reason_code, error.next_action, error.retriable) if self._valid(token) else ()
            self._close_names(old); return self.state
        return self._apply(snapshot, token)
    def health(self):
        if bool(getattr(self.session, "connected", False)): return self.ensure_ready()
        token = self._start()
        with self._lock: old = self._invalidate_locked("SSH_FAILED", "Reconnect the SSH Gateway, then retry.", True) if token >= 0 else ()
        self._close_names(old); return self.state
    def accept_health_snapshot(self, snapshot):
        if not isinstance(snapshot, GatewaySnapshot): return self.state
        with self._lock:
            if self._closed or not self._accept_locked(snapshot): return self._state
            self._mutation_token += 1; old = self._invalidate_locked("GATEWAY_BINDING_CHANGED", "Recover Monitor in the background, then retry.", True)
        self._close_names(old); return self.state
    def accept_binding(self, binding):
        with self._lock: current = self._binding
        return binding == current and current is not None and current.ssh_generation == int(self.session.state.generation)
    def accept_sample(self, binding): return self.accept_binding(binding)
    def close(self):
        with self._lock:
            self._mutation_token += 1; self._closed = True
            old = self._invalidate_locked("GATEWAY_NOT_READY", "Restart Monitor when the Gateway is ready.", True)
        self._close_names(old)


__all__ = ["GatewayBinding", "GatewayClientCoordinator", "GatewayClientState"]
