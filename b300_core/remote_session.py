"""Reusable embedded SSH session and local credential persistence for B300 remote debug."""

from __future__ import annotations

import json
import os
import shlex
import socket
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Protocol, Tuple

from cryptography.fernet import Fernet, InvalidToken

from .remote_profile import RemoteGatewayProfile, default_remote_profile_path
from .gateway_status import GatewaySnapshot
from .gateway_lease import GatewayLeasePublicSnapshot
from .gateway_protocol import (
    GATEWAY_AGENT_ENSURE_COMMAND, GATEWAY_AGENT_STATUS_COMMAND,
    GATEWAY_ENSURE_COMMAND, GATEWAY_PROTOCOL_VERSION, GATEWAY_RESCAN_COMMAND,
    GATEWAY_STATUS_COMMAND,
)
from .versioning import SemVer
from b300_version import __version__


def _remote_cli_resolver(cli_path: Optional[str]) -> str:
    configured = (
        'if [ -x "{path}" ]; then b300_cli="{path}"; elif '.format(path=cli_path)
        if cli_path else 'if '
    )
    return configured + (
    '[ -n "${B300_CLI_PATH:-}" ] && [ -x "$B300_CLI_PATH" ]; then '
    'b300_cli="$B300_CLI_PATH"; '
    'elif [ -x "$HOME/.local/share/b300-stlink/b300-stlink" ]; then '
    'b300_cli="$HOME/.local/share/b300-stlink/b300-stlink"; '
    'elif [ -x "$HOME/.local/bin/b300-stlink" ]; then '
    'b300_cli="$HOME/.local/bin/b300-stlink"; '
    'else b300_cli="$(command -v b300-stlink 2>/dev/null)" || exit 127; fi; '
    '[ -n "$b300_cli" ] || exit 127; exec "$b300_cli" '
)


def _remote_gateway_command(command: str, *, cli_path: Optional[str] = None) -> str:
    """Resolve the per-user native CLI without relying on an SSH login shell."""
    allowed = {GATEWAY_STATUS_COMMAND, GATEWAY_ENSURE_COMMAND, GATEWAY_RESCAN_COMMAND}
    if command not in allowed:
        raise ValueError("Unsupported remote Gateway CLI command.")
    arguments = command.split()[1:]
    return _remote_cli_resolver(cli_path) + " ".join(arguments)


class RemoteSessionError(RuntimeError):
    """Base class for reusable remote-session failures."""

    def __init__(self, message: str, *, reason_code: str = "SSH_FAILED",
                 phase: str = "ssh_connect", next_action: str = "Check the SSH Gateway and retry.",
                 retriable: bool = True) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.phase = phase
        self.next_action = next_action
        self.retriable = bool(retriable)


class RemoteAuthenticationError(RemoteSessionError):
    """The Gateway rejected the supplied account credentials."""

    def __init__(self, message: str, **details) -> None:
        details.setdefault("reason_code", "AUTH_FAILED")
        details.setdefault("phase", "ssh_auth")
        details.setdefault("next_action", "Check the SSH username and password, then reconnect.")
        details.setdefault("retriable", False)
        super().__init__(message, **details)


class RemoteForwardError(RemoteSessionError):
    """A requested local SSH forwarding endpoint could not be opened."""

    def __init__(self, message: str, **details) -> None:
        details.setdefault("reason_code", "TUNNEL_FAILED")
        details.setdefault("phase", "ssh_tunnel")
        details.setdefault("next_action", "Check the Gateway loopback listener and SSH tunnel, then retry.")
        details.setdefault("retriable", True)
        super().__init__(message, **details)


@dataclass(frozen=True)
class RemoteSessionState:
    state: str
    endpoint: str
    authenticated: bool
    forwards: Tuple[str, ...] = ()
    generation: int = 0
    error_code: Optional[str] = None


@dataclass(frozen=True)
class RemoteForward:
    name: str
    local_host: str
    local_port: int
    remote_host: str
    remote_port: int

    @property
    def endpoint(self) -> Tuple[str, int]:
        return self.local_host, self.local_port


class CredentialStore(Protocol):
    def load(self, profile: RemoteGatewayProfile) -> Optional[str]: ...
    def save(self, profile: RemoteGatewayProfile, password: str) -> None: ...
    def clear(self, profile: RemoteGatewayProfile) -> bool: ...


def _credential_root(*, root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    return default_remote_profile_path().parent


def _credential_id(profile: RemoteGatewayProfile) -> str:
    selected = profile.validate()
    return "%s@%s:%d" % (selected.user, selected.host, selected.port)


class LocalCredentialStore:
    """Keep a remembered password on this workstation without plaintext config.

    The Fernet key is also local to the same user profile. This is intentionally a
    convenience boundary for an internal engineering tool, not protection against an
    attacker who already controls the OS account. Password text is never written to
    logs, profile JSON, command lines, or process arguments.
    """

    def __init__(self, *, root: Optional[Path] = None) -> None:
        base = _credential_root(root=root)
        self.key_path = base / "remote_credentials.key"
        self.data_path = base / "remote_credentials.bin"
        self._lock = threading.RLock()

    def _ensure_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.is_file():
            key = self.key_path.read_bytes().strip()
            try:
                Fernet(key)
            except Exception as error:
                raise RemoteSessionError("Local credential key is invalid.") from error
            return key
        key = Fernet.generate_key()
        fd, temp_name = tempfile.mkstemp(prefix=self.key_path.name + ".", dir=str(self.key_path.parent))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(str(temp), 0o600)
            os.replace(str(temp), str(self.key_path))
            if os.name != "nt":
                os.chmod(str(self.key_path), 0o600)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass
        return key

    def _read_all(self) -> Dict[str, str]:
        if not self.data_path.is_file():
            return {}
        key = self._ensure_key()
        try:
            raw = Fernet(key).decrypt(self.data_path.read_bytes())
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, InvalidToken, json.JSONDecodeError) as error:
            raise RemoteSessionError("Local remembered SSH credentials are unreadable/corrupt.") from error
        if not isinstance(payload, dict) or any(
                not isinstance(key_name, str) or not isinstance(secret, str)
                for key_name, secret in payload.items()):
            raise RemoteSessionError("Local remembered SSH credential schema is invalid.")
        return payload

    def _write_all(self, payload: Dict[str, str]) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        token = Fernet(self._ensure_key()).encrypt(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        fd, temp_name = tempfile.mkstemp(prefix=self.data_path.name + ".", dir=str(self.data_path.parent))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(token)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(str(temp), 0o600)
            os.replace(str(temp), str(self.data_path))
            if os.name != "nt":
                os.chmod(str(self.data_path), 0o600)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

    def load(self, profile: RemoteGatewayProfile) -> Optional[str]:
        with self._lock:
            return self._read_all().get(_credential_id(profile))

    def save(self, profile: RemoteGatewayProfile, password: str) -> None:
        secret = str(password)
        if not secret:
            raise ValueError("SSH password must not be empty when remembering credentials.")
        with self._lock:
            payload = self._read_all()
            payload[_credential_id(profile)] = secret
            self._write_all(payload)

    def clear(self, profile: RemoteGatewayProfile) -> bool:
        with self._lock:
            payload = self._read_all()
            removed = payload.pop(_credential_id(profile), None) is not None
            if not removed:
                return False
            self._write_all(payload)
            return True


class _ForwardServer:
    def __init__(self, transport, *, name: str, local_host: str, local_port: int,
                 remote_host: str, remote_port: int) -> None:
        self.transport = transport
        self.name = name
        self.remote_host = remote_host
        self.remote_port = int(remote_port)
        self._stop = threading.Event()
        self._bridges: Dict[int, Tuple[object, object]] = {}
        self._bridges_lock = threading.Lock()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((local_host, int(local_port)))
        self._listener.listen(8)
        self._listener.settimeout(0.25)
        bound_host, bound_port = self._listener.getsockname()[:2]
        self.forward = RemoteForward(
            name=name, local_host=str(bound_host), local_port=int(bound_port),
            remote_host=remote_host, remote_port=int(remote_port),
        )
        self._thread = threading.Thread(target=self._serve, name="b300-ssh-%s" % name, daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    def matches(self, *, local_host: str, local_port: int,
                remote_host: str, remote_port: int) -> bool:
        selected_local = int(local_port)
        local_matches = selected_local == 0 or selected_local == self.forward.local_port
        return bool(
            self.forward.local_host == local_host
            and local_matches
            and self.forward.remote_host == remote_host
            and self.forward.remote_port == int(remote_port)
        )

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, origin = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                channel = self.transport.open_channel(
                    "direct-tcpip", (self.remote_host, self.remote_port), origin,
                )
                if channel is None:
                    raise OSError("SSH transport did not return a forwarding channel.")
            except Exception:
                client.close()
                continue
            bridge_id = id(client)
            with self._bridges_lock:
                self._bridges[bridge_id] = (client, channel)
            threading.Thread(
                target=self._bridge, args=(bridge_id, client, channel),
                name="b300-ssh-%s-client" % self.name, daemon=True,
            ).start()

    def _bridge(self, bridge_id: int, client: socket.socket, channel) -> None:
        def copy(source, target) -> None:
            try:
                while not self._stop.is_set():
                    data = source.recv(65536)
                    if not data:
                        break
                    target.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    target.shutdown(socket.SHUT_WR)
                except Exception:
                    pass

        left = threading.Thread(target=copy, args=(client, channel), daemon=True)
        right = threading.Thread(target=copy, args=(channel, client), daemon=True)
        left.start()
        right.start()
        left.join()
        right.join()
        for endpoint in (channel, client):
            try:
                endpoint.close()
            except Exception:
                pass
        with self._bridges_lock:
            self._bridges.pop(bridge_id, None)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._bridges_lock:
            bridges = tuple(self._bridges.values())
        for client, channel in bridges:
            for endpoint in (channel, client):
                try:
                    endpoint.close()
                except Exception:
                    pass
        self._thread.join(timeout=1.0)


SshClientFactory = Callable[[], object]
ForwardServerFactory = Callable[..., object]


class RemoteSession:
    """One authenticated SSH connection reused by GDB, TCL and Live Monitor forwards."""

    supports_gateway_leases = True

    def __init__(self, profile: RemoteGatewayProfile, *,
                 credential_store: Optional[CredentialStore] = None,
                 ssh_client_factory: Optional[SshClientFactory] = None,
                 forward_server_factory: ForwardServerFactory = _ForwardServer,
                 keepalive_seconds: int = 15) -> None:
        self.profile = profile.validate()
        self.credential_store = credential_store or LocalCredentialStore()
        self._ssh_client_factory = ssh_client_factory
        self._forward_server_factory = forward_server_factory
        self.keepalive_seconds = max(0, int(keepalive_seconds))
        self._client = None
        self._transport = None
        self._forwards: Dict[str, object] = {}
        self._lock = threading.RLock()
        self._connecting = False
        self._generation = 0
        self._last_error_code: Optional[str] = None

    @property
    def endpoint(self) -> str:
        return "%s@%s:%d" % (self.profile.user, self.profile.host, self.profile.port)

    @property
    def connected(self) -> bool:
        transport = self._transport
        if transport is None:
            return False
        try:
            return bool(transport.is_active())
        except Exception:
            return False

    @property
    def state(self) -> RemoteSessionState:
        with self._lock:
            if self._connecting:
                state = "connecting"
            elif self.connected:
                state = "connected"
            elif self._last_error_code:
                state = "error"
            else:
                state = "disconnected"
            names = tuple(sorted(
                name for name, server in self._forwards.items()
                if bool(getattr(server, "alive", True))
            ))
            return RemoteSessionState(
                state=state,
                endpoint=self.endpoint,
                authenticated=self.connected,
                forwards=names,
                generation=self._generation,
                error_code=self._last_error_code,
            )

    def _new_client(self):
        if self._ssh_client_factory is not None:
            return self._ssh_client_factory()
        try:
            import paramiko
        except ImportError as error:
            raise RemoteSessionError(
                "Embedded SSH runtime is unavailable. Install the packaged Paramiko dependency."
            ) from error
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        # Internal-tool default: accept first-contact host keys without a second wizard.
        # Existing known system keys are still checked by Paramiko when present.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return client

    @staticmethod
    def _classify_connect_error(error: BaseException) -> str:
        name = error.__class__.__name__.lower()
        if "authentication" in name or "badauth" in name or "password" in name:
            return "SSH_AUTH_FAILED"
        if isinstance(error, (socket.timeout, TimeoutError)) or "timeout" in name:
            return "SSH_TIMEOUT"
        if isinstance(error, (socket.gaierror, ConnectionError, OSError)):
            return "SSH_NETWORK_FAILED"
        return "SSH_CONNECT_FAILED"

    @staticmethod
    def _message_for_error(code: str) -> str:
        return {
            "SSH_AUTH_FAILED": "SSH authentication failed.",
            "SSH_TIMEOUT": "SSH connection timed out.",
            "SSH_NETWORK_FAILED": "SSH Gateway is unreachable or refused the connection.",
            "SSH_CONNECT_FAILED": "SSH session could not be established.",
        }.get(code, "SSH session failed.")

    def connect(self, password: Optional[str] = None, *, remember: bool = False,
                timeout_seconds: float = 30.0) -> RemoteSessionState:
        if timeout_seconds <= 0:
            raise ValueError("SSH connect timeout must be positive.")
        if self._transport is not None and not self.connected:
            self.disconnect()
        with self._lock:
            if self.connected:
                return self.state
            if self._connecting:
                raise RemoteSessionError("SSH connection is already in progress.")
            self._connecting = True
            self._last_error_code = None

        from_store = password is None
        secret = password if password is not None else self.credential_store.load(self.profile)
        if not secret:
            with self._lock:
                self._connecting = False
                self._last_error_code = "SSH_PASSWORD_REQUIRED"
            raise RemoteAuthenticationError("SSH password is required for the first connection.")

        client = self._new_client()
        try:
            client.connect(
                hostname=self.profile.host,
                port=self.profile.port,
                username=self.profile.user,
                password=secret,
                timeout=timeout_seconds,
                auth_timeout=timeout_seconds,
                banner_timeout=timeout_seconds,
                allow_agent=False,
                look_for_keys=False,
            )
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise RemoteSessionError("SSH transport did not become active after authentication.")
            if self.keepalive_seconds and hasattr(transport, "set_keepalive"):
                transport.set_keepalive(self.keepalive_seconds)
        except Exception as error:
            try:
                client.close()
            except Exception:
                pass
            code = self._classify_connect_error(error)
            if code == "SSH_AUTH_FAILED" and from_store:
                try:
                    self.credential_store.clear(self.profile)
                except Exception:
                    pass
            with self._lock:
                self._connecting = False
                self._last_error_code = code
            message = self._message_for_error(code)
            if code == "SSH_AUTH_FAILED":
                raise RemoteAuthenticationError(message) from error
            raise RemoteSessionError(message) from error

        with self._lock:
            self._client = client
            self._transport = transport
            self._connecting = False
            self._generation += 1
            self._last_error_code = None
        if remember:
            self.credential_store.save(self.profile, secret)
        elif password is not None:
            # An unchecked Remember box means this endpoint must not retain an older password.
            self.credential_store.clear(self.profile)
        return self.state

    def ensure_connected(self, password: Optional[str] = None, *, remember: bool = False,
                         timeout_seconds: float = 30.0) -> RemoteSessionState:
        if self.connected:
            return self.state
        return self.connect(password, remember=remember, timeout_seconds=timeout_seconds)

    def reconnect(self, password: Optional[str] = None, *, remember: bool = False,
                  timeout_seconds: float = 30.0) -> RemoteSessionState:
        self.disconnect()
        return self.connect(password, remember=remember, timeout_seconds=timeout_seconds)

    def check_health(self) -> RemoteSessionState:
        dead = []
        with self._lock:
            if self._transport is not None and not self.connected:
                self._last_error_code = "SSH_SESSION_LOST"
            for name, server in self._forwards.items():
                if not bool(getattr(server, "alive", True)):
                    dead.append(name)
        for name in dead:
            self.close_forward(name)
        return self.state

    def require_remote_listener(self, *, remote_port: int,
                                timeout_seconds: float = 3.0) -> None:
        """Require a Gateway loopback listener without sending protocol data.

        SSH channel opening checks the remote destination, unlike binding a
        local forwarding socket. The short-lived channel is closed immediately.
        """
        port = int(remote_port)
        timeout = float(timeout_seconds)
        if not 1 <= port <= 65535:
            raise ValueError("Remote listener port is out of range.")
        if not 0 < timeout <= 10:
            raise ValueError("Remote listener timeout must be greater than 0 and at most 10 seconds.")
        with self._lock:
            if not self.connected:
                raise RemoteForwardError("SSH session is not connected.")
            transport = self._transport
        channel = None
        try:
            try:
                channel = transport.open_channel(
                    "direct-tcpip", ("127.0.0.1", port), ("127.0.0.1", 0),
                    timeout=timeout,
                )
                if channel is None:
                    raise OSError("SSH transport did not return a listener channel.")
            finally:
                if channel is not None:
                    channel.close()
        except Exception as error:
            raise RemoteForwardError(
                "Gateway loopback listener 127.0.0.1:%d is unavailable through SSH." % port
            ) from error

    def _run_gateway_cli(self, command: str, *, timeout_seconds: float) -> GatewaySnapshot:
        allowed = {GATEWAY_STATUS_COMMAND, GATEWAY_ENSURE_COMMAND, GATEWAY_RESCAN_COMMAND}
        if command not in allowed:
            raise ValueError("Unsupported remote Gateway CLI command.")
        if not 0 < float(timeout_seconds) <= 60:
            raise ValueError("Remote Gateway CLI timeout must be greater than 0 and at most 60 seconds.")
        with self._lock:
            if not self.connected or self._client is None:
                raise RemoteSessionError("SSH session is not connected.")
            client = self._client
        try:
            # Paramiko exec channels are non-interactive and commonly omit the
            # managed per-user launcher from PATH. Resolve only fixed safe
            # candidates, with an explicit per-user override taking priority.
            remote_command = _remote_gateway_command(command, cli_path=self.profile.cli_path)
            _stdin, stdout, stderr = client.exec_command(
                remote_command, timeout=float(timeout_seconds)
            )
            output = stdout.read(256 * 1024 + 1)
            error_output = stderr.read(16 * 1024 + 1)
            if len(output) > 256 * 1024 or len(error_output) > 16 * 1024:
                raise RemoteSessionError(
                    "Gateway CLI response exceeded the supported size.",
                    reason_code="CLI_RESPONSE_TOO_LARGE", phase="gateway_cli",
                    next_action="Check the Gateway CLI output and retry.", retriable=False,
                )
            exit_status = stdout.channel.recv_exit_status()
        except RemoteSessionError:
            raise
        except Exception as error:
            raise RemoteSessionError(
                "Gateway CLI could not be executed over SSH.",
                reason_code="CLI_EXECUTION_FAILED", phase="gateway_cli",
                next_action="Check the Gateway CLI installation and SSH session, then retry.", retriable=True,
            ) from error
        text = output.decode("utf-8", "replace") if isinstance(output, bytes) else str(output)
        records = []
        for line in text.splitlines():
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and "state" in candidate:
                records.append(candidate)
        if not records:
            detail = error_output.decode("utf-8", "replace") if isinstance(error_output, bytes) else str(error_output)
            lowered = detail.casefold()
            if exit_status == 127 or "command not found" in lowered or "not recognized" in lowered:
                raise RemoteSessionError(
                    "B300 CLI is not installed for the SSH user on the Gateway; install the CLI and retry.",
                    reason_code="CLI_NOT_FOUND", phase="gateway_cli",
                    next_action="Install B300 CLI for the SSH user on the Gateway, then retry.", retriable=False,
                )
            if any(marker in lowered for marker in (
                    "invalid choice", "unrecognized argument", "unknown command")):
                raise RemoteSessionError(
                    "CLI B300 trên Gateway chưa hỗ trợ trạng thái Gateway; "
                    "hãy cập nhật (update) CLI trên máy Gateway rồi thử lại.",
                    reason_code="CLI_TOO_OLD", phase="gateway_cli",
                    next_action="Update B300 CLI on the Gateway, then retry.", retriable=False,
                )
            raise RemoteSessionError(
                "Gateway CLI did not return a supported status snapshot%s." %
                (" (exit %d)" % exit_status if exit_status else ""),
                reason_code="CLI_RESPONSE_INVALID", phase="gateway_cli",
                next_action="Check the Gateway CLI output and retry.", retriable=True,
            ) from (RuntimeError(detail.strip()) if detail.strip() else None)
        record = records[-1]
        protocol_version = record.get("protocol_version")
        if type(protocol_version) is not int:
            raise RemoteSessionError(
                "Gateway CLI returned an invalid protocol version; update the remote CLI.",
                reason_code="PROTOCOL_MISMATCH", phase="gateway_protocol",
                next_action="Update B300 CLI on the Gateway, then retry.", retriable=False,
            )
        if protocol_version != GATEWAY_PROTOCOL_VERSION:
            raise RemoteSessionError(
                "Gateway CLI protocol version %s is incompatible with Client protocol %s; "
                "update the remote CLI." % (protocol_version, GATEWAY_PROTOCOL_VERSION),
                reason_code="PROTOCOL_MISMATCH", phase="gateway_protocol",
                next_action="Update B300 CLI on the Gateway, then retry.", retriable=False,
            )
        capabilities = record.get("capabilities")
        required_capability = command.split()[2]
        if (not isinstance(capabilities, list)
                or required_capability not in capabilities):
            raise RemoteSessionError(
                "Gateway CLI does not advertise the required %s capability; "
                "update the remote CLI." % required_capability,
                reason_code="CLI_TOO_OLD", phase="gateway_cli",
                next_action="Update B300 CLI on the Gateway, then retry.", retriable=False,
            )
        try:
            remote_version = SemVer.parse(str(record.get("tool_version") or "").strip())
        except ValueError:
            raise RemoteSessionError(
                "Gateway CLI did not report its tool version; update the remote CLI.",
                reason_code="CLI_TOO_OLD", phase="gateway_cli",
                next_action="Update B300 CLI on the Gateway, then retry.", retriable=False,
            )
        if remote_version < SemVer.parse(__version__):
            raise RemoteSessionError(
                "Gateway CLI version %s is older than Client version %s; update the remote CLI."
                % (remote_version, __version__),
                reason_code="CLI_TOO_OLD", phase="gateway_cli",
                next_action="Update B300 CLI on the Gateway, then retry.", retriable=False,
            )
        try:
            snapshot = GatewaySnapshot.from_record(record)
        except (TypeError, ValueError) as error:
            raise RemoteSessionError(
                "Gateway CLI returned an invalid status snapshot.",
                reason_code="GATEWAY_RESPONSE_INVALID", phase="gateway_protocol",
                next_action="Update or repair B300 CLI on the Gateway, then retry.", retriable=False,
            ) from error
        if exit_status and snapshot.state == "READY":
            raise RemoteSessionError(
                "Gateway CLI exited with status %d despite reporting READY." % exit_status,
                reason_code="GATEWAY_COMMAND_FAILED", phase="gateway_cli",
                next_action="Check the Gateway CLI error output and retry.", retriable=True,
            )
        return snapshot

    def _run_gateway_control(self, command: str, arguments: Tuple[str, ...] = (), *,
                             timeout_seconds: float = 10.0) -> dict:
        """Run one fixed Gateway Agent command and return bounded JSON output."""
        allowed = {
            GATEWAY_AGENT_STATUS_COMMAND, GATEWAY_AGENT_ENSURE_COMMAND,
            "b300-stlink debug gateway-acquire --json",
            "b300-stlink debug gateway-renew --json",
            "b300-stlink debug gateway-release --json",
        }
        if command not in allowed:
            raise ValueError("Unsupported remote Gateway Agent command.")
        if not 0 < float(timeout_seconds) <= 60:
            raise ValueError("Remote Gateway Agent timeout is out of range.")
        with self._lock:
            if not self.connected or self._client is None:
                raise RemoteSessionError("SSH session is not connected.")
            client = self._client
        remote_command = _remote_cli_resolver(self.profile.cli_path)
        # Each argument is a bounded value produced by the lease client. Quote it
        # as one shell word because Paramiko accepts a command string only.
        remote_command += " " + " ".join(shlex.quote(item) for item in command.split()[1:])
        if arguments:
            remote_command += " " + " ".join(shlex.quote(str(item)) for item in arguments)
        try:
            _stdin, stdout, stderr = client.exec_command(remote_command, timeout=float(timeout_seconds))
            output = stdout.read(256 * 1024 + 1)
            error_output = stderr.read(16 * 1024 + 1)
            if len(output) > 256 * 1024 or len(error_output) > 16 * 1024:
                raise RemoteSessionError(
                    "Gateway Agent response exceeded the supported size.",
                    reason_code="CLI_RESPONSE_TOO_LARGE", phase="gateway_cli",
                    retriable=False,
                )
            exit_status = stdout.channel.recv_exit_status()
        except RemoteSessionError:
            raise
        except Exception as error:
            raise RemoteSessionError(
                "Gateway Agent command could not be executed over SSH.",
                reason_code="CLI_EXECUTION_FAILED", phase="gateway_cli",
            ) from error
        records = []
        text = output.decode("utf-8", "replace") if isinstance(output, bytes) else str(output)
        for line in text.splitlines():
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and ("status" in candidate or "state" in candidate):
                records.append(candidate)
        if not records:
            detail = error_output.decode("utf-8", "replace") if isinstance(error_output, bytes) else str(error_output)
            raise RemoteSessionError(
                "Gateway Agent returned no supported JSON response.",
                reason_code="CLI_RESPONSE_INVALID", phase="gateway_protocol",
                next_action="Update the Gateway CLI and retry.", retriable=False,
            ) from (RuntimeError(detail.strip()) if detail.strip() else None)
        record = records[-1]
        protocol_version = record.get("protocol_version")
        capabilities = record.get("capabilities")
        if type(protocol_version) is not int or protocol_version != GATEWAY_PROTOCOL_VERSION:
            raise RemoteSessionError(
                "Gateway Agent protocol version is incompatible.",
                reason_code="PROTOCOL_MISMATCH", phase="gateway_protocol",
                next_action="Update B300 CLI on the Gateway, then retry.", retriable=False,
            )
        if not isinstance(capabilities, list) or "gateway-exclusive-lease-v1" not in capabilities:
            raise RemoteSessionError(
                "Gateway CLI does not advertise exclusive lease support.",
                reason_code="CLI_TOO_OLD", phase="gateway_cli",
                next_action="Update B300 CLI on the Gateway, then retry.", retriable=False,
            )
        if record.get("status") == "error":
            result = record.get("result") if isinstance(record.get("result"), dict) else {}
            if record.get("reason_code") == "GATEWAY_BUSY":
                from .gateway_lease_client import GatewayBusyError
                raise GatewayBusyError(
                    "Gateway is busy.", client_label=str(result.get("client_label", "")),
                    mode=str(result.get("mode", "")),
                    heartbeat_age_seconds=int(result.get("heartbeat_age_seconds", 0)),
                )
            raise RemoteSessionError(
                "Gateway Agent rejected the request: %s" % record.get("reason_code", "UNKNOWN"),
                reason_code=str(record.get("reason_code", "GATEWAY_COMMAND_FAILED")),
                phase="gateway_lease", retriable=True,
            )
        if exit_status and record.get("status") not in {"ok", "ready"}:
            raise RemoteSessionError(
                "Gateway Agent exited with status %d." % exit_status,
                reason_code="GATEWAY_COMMAND_FAILED", phase="gateway_cli",
            )
        return record.get("result", record)

    def ensure_gateway_agent(self, *, timeout_seconds: float = 10.0) -> dict:
        return self._run_gateway_control(
            GATEWAY_AGENT_ENSURE_COMMAND, timeout_seconds=timeout_seconds,
        )

    def acquire_gateway(self, request: dict, *, timeout_seconds: float = 15.0):
        from .gateway_lease_client import RemoteLeaseGrant
        required = {"request_id", "client_id", "client_label", "mode", "probe_serial"}
        if set(request) != required:
            raise ValueError("Gateway lease request fields are invalid.")
        result = self._run_gateway_control(
            "b300-stlink debug gateway-acquire --json",
            ("--request-id", request["request_id"], "--client-id", request["client_id"],
             "--client-label", request["client_label"], "--lease-mode", request["mode"])
            + (("--probe-serial", request["probe_serial"]) if request.get("probe_serial") else ()),
            timeout_seconds=timeout_seconds,
        )
        public_fields = GatewayLeasePublicSnapshot.from_record({
            key: result[key] for key in (
                "active", "lease_id", "generation", "client_label", "mode", "state",
                "acquired_at", "heartbeat_age_seconds", "gateway_instance_id",
                "gateway_generation", "probe_serial", "reason_code", "gdb_endpoint",
                "tcl_endpoint",
            )
        })
        return RemoteLeaseGrant(
            lease_id=str(result["lease_id"]), token=str(result["lease_token"]),
            generation=int(result["lease_generation"]), public=public_fields.to_record(),
        )

    def renew_gateway(self, grant, *, timeout_seconds: float = 5.0) -> dict:
        return self._run_gateway_control(
            "b300-stlink debug gateway-renew --json",
            ("--lease-id", grant.lease_id, "--lease-token", grant.token,
             "--lease-generation", str(grant.generation)), timeout_seconds=timeout_seconds,
        )

    def release_gateway(self, grant, *, timeout_seconds: float = 5.0) -> dict:
        return self._run_gateway_control(
            "b300-stlink debug gateway-release --json",
            ("--lease-id", grant.lease_id, "--lease-token", grant.token,
             "--lease-generation", str(grant.generation)), timeout_seconds=timeout_seconds,
        )

    def gateway_status(self, *, timeout_seconds: float = 5.0) -> GatewaySnapshot:
        """Read the authenticated Gateway's current fail-closed snapshot."""
        return self._run_gateway_cli(
            GATEWAY_STATUS_COMMAND, timeout_seconds=timeout_seconds,
        )

    def gateway_rescan(self, *, timeout_seconds: float = 15.0) -> GatewaySnapshot:
        """Ask the Gateway owner to rediscover ST-Link and refresh target evidence."""
        return self._run_gateway_cli(
            GATEWAY_RESCAN_COMMAND, timeout_seconds=timeout_seconds,
        )

    def ensure_gateway_ready(self, *, timeout_seconds: float = 15.0) -> GatewaySnapshot:
        """Idempotently ask the authenticated per-user CLI to provide a READY Gateway."""
        current = self.gateway_status(timeout_seconds=min(timeout_seconds, 5.0))
        if current.attach_ready:
            return current
        result = self._run_gateway_cli(
            GATEWAY_ENSURE_COMMAND, timeout_seconds=timeout_seconds,
        )
        if not result.attach_ready:
            raise RemoteSessionError(
                "Gateway is not ready: %s (%s)." % (result.state, result.reason_code),
                reason_code="GATEWAY_NOT_READY", phase="gateway_ready",
                next_action="Resolve the Gateway status shown above, then retry.", retriable=True,
            )
        return result

    def open_forward(self, name: str, *, remote_port: int, local_port: int = 0,
                     remote_host: str = "127.0.0.1",
                     local_host: str = "127.0.0.1") -> RemoteForward:
        selected = str(name).strip()
        if not selected or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for char in selected):
            raise ValueError("Forward name contains unsupported characters.")
        for label, port, allow_zero in (("remote", remote_port, False), ("local", local_port, True)):
            minimum = 0 if allow_zero else 1
            if not minimum <= int(port) <= 65535:
                raise ValueError("%s forwarding port is out of range." % label)
        if local_host != "127.0.0.1":
            raise ValueError("B300 embedded SSH forwards must bind local loopback only.")
        if remote_host != "127.0.0.1":
            raise ValueError("B300 embedded SSH forwards may target Gateway loopback only.")

        stale = None
        with self._lock:
            if not self.connected:
                raise RemoteForwardError("SSH session is not connected.")
            existing = self._forwards.get(selected)
            if existing is not None:
                if bool(getattr(existing, "alive", True)):
                    if existing.matches(
                            local_host=local_host, local_port=int(local_port),
                            remote_host=remote_host, remote_port=int(remote_port)):
                        return existing.forward
                    raise RemoteForwardError(
                        "Forward '%s' already exists with a different endpoint." % selected
                    )
                stale = self._forwards.pop(selected)
        if stale is not None:
            try:
                stale.stop()
            except Exception:
                pass

        try:
            server = self._forward_server_factory(
                self._transport, name=selected, local_host=local_host,
                local_port=int(local_port), remote_host=remote_host,
                remote_port=int(remote_port),
            )
        except OSError as error:
            raise RemoteForwardError("Unable to open local forwarding listener.") from error
        except Exception as error:
            raise RemoteForwardError("Unable to create SSH forwarding channel.") from error
        with self._lock:
            if not self.connected:
                try:
                    server.stop()
                finally:
                    raise RemoteForwardError("SSH session closed while opening a forward.")
            self._forwards[selected] = server
        return server.forward

    def open_debug_forwards(self, *, local_gdb_port: int = 0, local_tcl_port: int = 0,
                            gateway_gdb_port: int = 3333,
                            gateway_tcl_port: int = 6666) -> Tuple[RemoteForward, RemoteForward]:
        with self._lock:
            had_gdb = "gdb" in self._forwards
        gdb = self.open_forward("gdb", remote_port=gateway_gdb_port, local_port=local_gdb_port)
        try:
            tcl = self.open_forward("tcl", remote_port=gateway_tcl_port, local_port=local_tcl_port)
        except Exception:
            if not had_gdb:
                self.close_forward("gdb")
            raise
        return gdb, tcl

    def close_forward(self, name: str) -> None:
        with self._lock:
            server = self._forwards.pop(str(name), None)
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass

    def disconnect(self, *, forget_password: bool = False) -> None:
        with self._lock:
            forwards = tuple(self._forwards.values())
            self._forwards.clear()
            client = self._client
            self._client = None
            self._transport = None
            self._connecting = False
            self._last_error_code = None
            self._generation += 1
        for server in forwards:
            try:
                server.stop()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if forget_password:
            self.credential_store.clear(self.profile)

    def forget_password(self) -> bool:
        return self.credential_store.clear(self.profile)
