"""Bounded Unix socket boundary for the system Gateway Agent."""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
import threading
import time
from pathlib import Path
from typing import Callable

from .gateway_agent_protocol import (
    AGENT_PROTOCOL_VERSION, GatewayRequest, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
)


def _receive(connection: socket.socket, limit: int, *,
             stop_event: threading.Event = None, deadline: float = None) -> bytes:
    header = _read_exact(connection, 4, stop_event=stop_event, deadline=deadline)
    length = struct.unpack(">I", header)[0]
    if length == 0 or length > limit:
        raise ValueError("Gateway frame exceeds limit")
    return _read_exact(connection, length, stop_event=stop_event, deadline=deadline)


def _read_exact(connection: socket.socket, length: int, *,
                stop_event: threading.Event = None, deadline: float = None) -> bytes:
    data = bytearray()
    while len(data) < length:
        if stop_event is not None and stop_event.is_set():
            raise ConnectionError("Gateway socket server is stopping")
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Gateway socket frame timed out")
        try:
            chunk = connection.recv(length - len(data))
        except socket.timeout:
            if stop_event is None:
                raise
            continue
        if not chunk:
            raise ConnectionError("Gateway socket closed before frame completed")
        data.extend(chunk)
    return bytes(data)


def _send(connection: socket.socket, payload: bytes, limit: int) -> None:
    if len(payload) == 0 or len(payload) > limit:
        raise ValueError("Gateway response exceeds limit")
    connection.sendall(struct.pack(">I", len(payload)) + payload)


class GatewayUnixClient:
    def __init__(self, socket_path: Path) -> None:
        self.socket_path = Path(socket_path)

    def submit_request(self, request: GatewayRequest, timeout_seconds: float) -> dict:
        timeout = float(timeout_seconds)
        if not 0 < timeout <= 60:
            raise ValueError("Gateway socket timeout must be in (0, 60]")
        selected = GatewayRequest.from_record(request.to_record())
        payload = json.dumps(selected.to_record(), separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Gateway request exceeds limit")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(self.socket_path))
            connection.sendall(struct.pack(">I", len(payload)) + payload)
            response = json.loads(_receive(connection, MAX_RESPONSE_BYTES).decode("utf-8"))
        if (not isinstance(response, dict)
                or response.get("protocol_version") != AGENT_PROTOCOL_VERSION
                or response.get("request_id") != selected.request_id
                or response.get("status") not in {"ok", "error"}
                or not isinstance(response.get("reason_code"), str)):
            raise ValueError("Gateway socket response schema is invalid")
        return response


class GatewayUnixServer:
    def __init__(self, socket_path: Path, allowed_uid: int,
                 submit: Callable[[GatewayRequest, float], dict], *,
                 allowed_gid: int = None) -> None:
        self.socket_path = Path(socket_path)
        if type(allowed_uid) is not int or allowed_uid < 0:
            raise ValueError("Gateway operator UID is invalid")
        self.allowed_uid = allowed_uid
        if allowed_gid is not None and (type(allowed_gid) is not int or allowed_gid < 0):
            raise ValueError("Gateway operator GID is invalid")
        self.allowed_gid = allowed_gid
        self.submit = submit

    def serve(self, stop_event: threading.Event) -> None:
        if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
            raise RuntimeError("Gateway Unix transport requires Linux SO_PEERCRED")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.socket_path))
            try:
                if self.allowed_gid is not None:
                    if self.allowed_gid not in (os.getgid(), *os.getgroups()):
                        raise PermissionError("Agent is not in the Gateway operator group")
                    os.chown(str(self.socket_path), -1, self.allowed_gid)
                os.chmod(str(self.socket_path), 0o660)
                if self.allowed_gid is not None:
                    info = self.socket_path.lstat()
                    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                            or info.st_gid != self.allowed_gid
                            or stat.S_IMODE(info.st_mode) != 0o660):
                        raise PermissionError("Gateway socket ownership is unsafe")
                listener.listen(8)
                listener.settimeout(0.2)
                while not stop_event.is_set():
                    try:
                        accepted, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with accepted:
                        accepted.settimeout(0.1)
                        try:
                            self._handle(accepted, stop_event)
                        except (OSError, UnicodeError, ValueError, TypeError, RecursionError,
                                ConnectionError, json.JSONDecodeError):
                            # A rejected peer receives no operation response.
                            continue
            finally:
                self.socket_path.unlink(missing_ok=True)

    def _handle(self, accepted: socket.socket, stop_event: threading.Event) -> None:
        peer_pid, peer_uid, peer_gid = struct.unpack(
            "3i", accepted.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer_uid != self.allowed_uid:
            raise PermissionError("Gateway socket peer is not the approved SSH operator")
        request = GatewayRequest.from_record(json.loads(
            _receive(accepted, MAX_REQUEST_BYTES, stop_event=stop_event,
                     deadline=time.monotonic() + 60.0).decode("utf-8")))
        remaining = request.expires_mono - time.monotonic()
        if not 0 < remaining <= 60:
            raise ValueError("Gateway socket request expired")
        response = self.submit(request, remaining)
        if not isinstance(response, dict):
            raise ValueError("Gateway response must be an object")
        payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
        _send(accepted, payload, MAX_RESPONSE_BYTES)


__all__ = ["GatewayUnixClient", "GatewayUnixServer", "MAX_REQUEST_BYTES", "MAX_RESPONSE_BYTES"]
